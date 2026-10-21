import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import WebSocket

from services.nlp_engine.summarizer import summarize_call
from services.database import Task, SourceType
from services.json_store import add_task as json_add_task
from services.web.database import get_session_factory

AGENT_STATUS_IDLE = 'idle'
AGENT_STATUS_LISTENING = 'listening'
AGENT_STATUS_PROCESSING = 'processing'
AGENT_STATUSES = {AGENT_STATUS_IDLE, AGENT_STATUS_LISTENING, AGENT_STATUS_PROCESSING}

SESSION_TIMEOUT = 120
# How long a call session survives an agent disconnect, waiting for it to come back.
RECONNECT_GRACE = 60
MAX_SESSION_TRANSCRIPTIONS = 500
MAX_SESSION_CHARS = 100_000
MAX_TRANSCRIPTION_CHARS = 8_000
MAX_DEVICES = 32
SUMMARY_QUEUE_SIZE = 100
FRONTEND_SEND_TIMEOUT = 5

# Commands a dashboard may forward to its own agent.
FRONTEND_COMMANDS = {'start', 'stop', 'select_device'}

# Close codes (4000-4999 are application-defined)
CLOSE_REPLACED = 4000
CLOSE_UNAUTHORIZED = 4401


@dataclass
class CallSession:
    id: str
    start_time: datetime
    transcriptions: list[dict] = field(default_factory=list)
    chars: int = 0

    def texts(self) -> list[str]:
        return [t['text'] for t in self.transcriptions if t.get('text')]


@dataclass
class AgentConnection:
    user_id: str
    discord_id: str
    ws: WebSocket
    status: str = AGENT_STATUS_IDLE
    devices: list[dict] = field(default_factory=list)
    device_id: int | None = None
    active_session: CallSession | None = None
    _session_timer: asyncio.Task | None = None


@dataclass
class _ParkedSession:
    session: CallSession
    discord_id: str
    timer: asyncio.Task


@dataclass
class _SummaryJob:
    session: CallSession
    user_id: str
    discord_id: str


def _as_int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _clean_devices(devices) -> list[dict]:
    if not isinstance(devices, list):
        return []
    out = []
    for d in devices[:MAX_DEVICES]:
        if isinstance(d, dict) and _as_int(d.get('id')) is not None:
            out.append({'id': d['id'], 'name': str(d.get('name', ''))[:200]})
    return out


class AgentManager:
    def __init__(self):
        # One live agent connection per user (keyed by internal user_id).
        self.agents: dict[str, AgentConnection] = {}
        # Dashboards, per user. A dashboard only ever sees its own user's agent.
        self.frontends: dict[str, set[WebSocket]] = {}
        # Sessions of agents that dropped mid-call, kept for RECONNECT_GRACE seconds.
        self._parked: dict[str, _ParkedSession] = {}
        # Last processed (stream_id, seq) per user, to drop messages re-sent after a reconnect.
        self._last_seq: dict[str, tuple[str, int]] = {}
        self._summary_queue: asyncio.Queue[_SummaryJob] | None = None
        self._summary_worker: asyncio.Task | None = None

    def get_agent(self, user_id: str) -> AgentConnection | None:
        return self.agents.get(user_id)

    # ── Summarization worker ─────────────────────────────────────────────

    def start(self):
        if self._summary_worker is None or self._summary_worker.done():
            self._summary_queue = asyncio.Queue(maxsize=SUMMARY_QUEUE_SIZE)
            self._summary_worker = asyncio.create_task(self._summary_loop())

    async def stop(self):
        if self._summary_worker:
            self._summary_worker.cancel()
            try:
                await self._summary_worker
            except asyncio.CancelledError:
                pass
            self._summary_worker = None

    def _enqueue_summary(self, session: CallSession, user_id: str, discord_id: str):
        if not session.transcriptions:
            return
        self.start()
        try:
            self._summary_queue.put_nowait(_SummaryJob(session, user_id, discord_id))
            print(f"[agent] Call session {session.id} ended — {len(session.transcriptions)} chunks queued")
        except asyncio.QueueFull:
            print(f"[agent] Summary queue full, dropping session {session.id} of user {user_id}")

    async def _summary_loop(self):
        while True:
            job = await self._summary_queue.get()
            try:
                await self._summarize_session(job)
            except Exception as e:
                print(f"[agent] Session summarization error: {e}")
            finally:
                self._summary_queue.task_done()

    async def _summarize_session(self, job: _SummaryJob):
        texts = job.session.texts()
        if not texts:
            return

        try:
            llm_tasks = await summarize_call(texts)
        except Exception as e:
            print(f"[agent] LLM call error: {e}")
            llm_tasks = []

        if not llm_tasks:
            return

        print(f"[agent] LLM extracted {len(llm_tasks)} task(s)")
        await self._save_tasks(llm_tasks, job)

    async def _save_tasks(self, tasks: list[dict], job: _SummaryJob):
        tasks = [t for t in tasks if isinstance(t, dict) and t.get('content')]
        factory = get_session_factory()
        if factory is not None:
            async with factory() as db:
                for t in tasks:
                    db.add(Task(
                        id=uuid4(),
                        user_id=UUID(job.user_id),
                        source=SourceType.desktop_loopback.value,
                        content=str(t['content']),
                        author='desktop',
                        urgency=t.get('urgency', 'low'),
                        channel='desktop',
                        dm_sent='pending',
                    ))
                try:
                    await db.commit()
                    print(f"[agent] Created {len(tasks)} task(s) from call summary")
                except Exception as e:
                    print(f"[agent] DB commit error: {e}")
        else:
            for t in tasks:
                json_add_task(
                    user_id=job.discord_id,
                    content=str(t['content']),
                    urgency=t.get('urgency', 'low'),
                )
            print(f"[agent] Saved {len(tasks)} task(s) to JSON store")

    # ── Agent connections ────────────────────────────────────────────────

    async def connect_agent(self, ws: WebSocket, user_id: str, discord_id: str) -> AgentConnection:
        await ws.accept()
        conn = AgentConnection(user_id=user_id, discord_id=discord_id, ws=ws)

        old = self.agents.get(user_id)
        if old is not None:
            # Same account connected again (reconnect before the old socket timed out,
            # or a second agent instance). The newest connection wins and inherits the call.
            conn.active_session = old.active_session
            old.active_session = None
            self._cancel_timer(old)
            asyncio.create_task(self._close_quietly(old.ws, CLOSE_REPLACED, 'replaced by a newer connection'))

        parked = self._parked.pop(user_id, None)
        if parked is not None:
            parked.timer.cancel()
            if conn.active_session is None:
                conn.active_session = parked.session
            else:
                self._enqueue_summary(parked.session, user_id, discord_id)

        self.agents[user_id] = conn
        if conn.active_session is not None:
            print(f"[agent] Resumed call session {conn.active_session.id} for user {user_id}")
            self._reset_session_timer(conn)

        await self._send_status(user_id)
        return conn

    def disconnect_agent(self, conn: AgentConnection):
        self._cancel_timer(conn)
        if self.agents.get(conn.user_id) is not conn:
            # Already replaced by a newer connection, which owns the session now.
            return
        del self.agents[conn.user_id]

        session, conn.active_session = conn.active_session, None
        if session is not None and session.transcriptions:
            timer = asyncio.create_task(self._expire_parked(conn.user_id))
            self._parked[conn.user_id] = _ParkedSession(session, conn.discord_id, timer)
        asyncio.create_task(self._send_status(conn.user_id))

    async def _expire_parked(self, user_id: str):
        await asyncio.sleep(RECONNECT_GRACE)
        parked = self._parked.pop(user_id, None)
        if parked is not None:
            self._enqueue_summary(parked.session, user_id, parked.discord_id)

    @staticmethod
    async def _close_quietly(ws: WebSocket, code: int, reason: str):
        try:
            await ws.close(code=code, reason=reason)
        except Exception:
            pass

    # ── Agent messages ───────────────────────────────────────────────────

    def _is_duplicate(self, conn: AgentConnection, msg: dict) -> bool:
        stream_id, seq = msg.get('stream_id'), _as_int(msg.get('seq'))
        if not isinstance(stream_id, str) or seq is None:
            return False
        last = self._last_seq.get(conn.user_id)
        if last is not None and last[0] == stream_id and seq <= last[1]:
            return True
        self._last_seq[conn.user_id] = (stream_id, seq)
        return False

    async def handle_agent_message(self, conn: AgentConnection, msg: dict):
        seq = _as_int(msg.get('seq'))
        if not self._is_duplicate(conn, msg):
            await self._dispatch_agent_message(conn, msg)
        if seq is not None:
            await conn.ws.send_json({'type': 'ack', 'seq': seq})

    async def _dispatch_agent_message(self, conn: AgentConnection, msg: dict):
        msg_type = msg.get('type')

        if msg_type == 'register':
            conn.devices = _clean_devices(msg.get('devices'))
            if msg.get('status') in AGENT_STATUSES:
                conn.status = msg['status']
            conn.device_id = _as_int(msg.get('device_id'))
            await self._send_status(conn.user_id)

        elif msg_type == 'status':
            if msg.get('status') in AGENT_STATUSES:
                conn.status = msg['status']
            if 'device_id' in msg:
                conn.device_id = _as_int(msg.get('device_id'))
            await self._send_status(conn.user_id)

        elif msg_type == 'devices':
            conn.devices = _clean_devices(msg.get('devices'))
            if 'device_id' in msg:
                conn.device_id = _as_int(msg.get('device_id'))
            await self._send_status(conn.user_id)

        elif msg_type == 'call_start':
            # A new call closes whatever was left of the previous one instead of discarding it.
            self._end_session(conn)

        elif msg_type == 'call_end':
            self._end_session(conn)

        elif msg_type == 'transcription':
            text = msg.get('text')
            if not isinstance(text, str) or not text.strip():
                return
            if len(text) > MAX_TRANSCRIPTION_CHARS:
                print(f"[agent] Transcription of {len(text)} chars from user {conn.user_id} truncated")
                text = text[:MAX_TRANSCRIPTION_CHARS]
            timestamp = str(msg.get('timestamp', ''))[:64]

            await self._send_to_frontends(conn.user_id, {
                'type': 'transcription',
                'text': text,
                'timestamp': timestamp,
            })

            if conn.active_session is None:
                conn.active_session = CallSession(id=str(uuid4()), start_time=datetime.utcnow())
            session = conn.active_session
            session.transcriptions.append({'text': text, 'timestamp': timestamp})
            session.chars += len(text)
            if len(session.transcriptions) >= MAX_SESSION_TRANSCRIPTIONS or session.chars >= MAX_SESSION_CHARS:
                self._end_session(conn)
            else:
                self._reset_session_timer(conn)

    # ── Call sessions ────────────────────────────────────────────────────

    @staticmethod
    def _cancel_timer(conn: AgentConnection):
        if conn._session_timer is not None and conn._session_timer is not asyncio.current_task():
            conn._session_timer.cancel()
        conn._session_timer = None

    def _reset_session_timer(self, conn: AgentConnection):
        self._cancel_timer(conn)
        conn._session_timer = asyncio.create_task(self._session_timeout(conn))

    async def _session_timeout(self, conn: AgentConnection):
        await asyncio.sleep(SESSION_TIMEOUT)
        self._end_session(conn)

    def _end_session(self, conn: AgentConnection):
        self._cancel_timer(conn)
        session, conn.active_session = conn.active_session, None
        if session is not None:
            self._enqueue_summary(session, conn.user_id, conn.discord_id)

    # ── Dashboards ───────────────────────────────────────────────────────

    def add_frontend(self, user_id: str, ws: WebSocket):
        self.frontends.setdefault(user_id, set()).add(ws)

    def remove_frontend(self, user_id: str, ws: WebSocket):
        sockets = self.frontends.get(user_id)
        if sockets is not None:
            sockets.discard(ws)
            if not sockets:
                del self.frontends[user_id]

    def status_snapshot(self, user_id: str) -> dict:
        conn = self.agents.get(user_id)
        if conn is None:
            return {'type': 'status', 'connected': False, 'status': AGENT_STATUS_IDLE,
                    'device_id': None, 'devices': [], 'user_id': None}
        return {'type': 'status', 'connected': True, 'status': conn.status,
                'device_id': conn.device_id, 'devices': conn.devices, 'user_id': conn.user_id}

    async def send_command(self, user_id: str, action: str, device_id: int | None = None) -> bool:
        conn = self.agents.get(user_id)
        if conn is None or action not in FRONTEND_COMMANDS:
            return False
        cmd = {'type': 'command', 'action': action}
        if action == 'start':
            cmd['device_id'] = conn.device_id
        elif action == 'select_device':
            if device_id is None:
                return False
            cmd['device_id'] = device_id
        try:
            await conn.ws.send_json(cmd)
        except Exception:
            return False
        return True

    async def _send_status(self, user_id: str):
        await self._send_to_frontends(user_id, self.status_snapshot(user_id))

    async def _send_to_frontends(self, user_id: str, msg: dict):
        # Bounded wait so a stuck browser can't stall the agent's receive loop.
        for ws in list(self.frontends.get(user_id, ())):
            try:
                await asyncio.wait_for(ws.send_json(msg), FRONTEND_SEND_TIMEOUT)
            except Exception:
                self.remove_frontend(user_id, ws)
                asyncio.create_task(self._close_quietly(ws, 1011, 'send failed'))


manager = AgentManager()
