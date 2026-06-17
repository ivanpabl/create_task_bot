import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.nlp_engine.summarizer import summarize_call
from services.database import Task, SourceType, User
from services.json_store import add_task as json_add_task

AGENT_STATUS_IDLE = 'idle'
AGENT_STATUS_LISTENING = 'listening'
AGENT_STATUS_PROCESSING = 'processing'

SESSION_TIMEOUT = 120
MAX_SESSION_TRANSCRIPTIONS = 500


@dataclass
class CallSession:
    id: str
    start_time: datetime
    transcriptions: list[dict] = field(default_factory=list)

    def full_text(self) -> str:
        return '\n'.join(t.get('text', '') for t in self.transcriptions if t.get('text'))


@dataclass
class AgentConnection:
    discord_id: str
    ws: WebSocket
    user_id: str | None = None
    status: str = AGENT_STATUS_IDLE
    devices: list[dict] = field(default_factory=list)
    device_id: int | None = None
    active_session: CallSession | None = None
    _session_timer: asyncio.Task | None = None
    _ending_session_lock: bool = False


class AgentManager:
    def __init__(self):
        self.agents: dict[str, AgentConnection] = {}
        self.frontends: list[WebSocket] = []

    def _get(self, discord_id: str) -> AgentConnection | None:
        return self.agents.get(discord_id)

    async def connect_agent(self, ws: WebSocket, discord_id: str):
        await ws.accept()
        conn = AgentConnection(discord_id=discord_id, ws=ws)
        self.agents[discord_id] = conn

    def disconnect_agent(self, discord_id: str):
        conn = self.agents.pop(discord_id, None)
        if conn and conn._session_timer:
            conn._session_timer.cancel()

    async def connect_frontend(self, ws: WebSocket):
        await ws.accept()
        self.frontends.append(ws)

    def disconnect_frontend(self, ws: WebSocket):
        self.frontends = [w for w in self.frontends if w != ws]

    async def handle_agent_message(self, msg: dict, db: AsyncSession | None = None):
        msg_type = msg.get('type')
        discord_id = msg.get('discord_id')
        if not discord_id:
            return
        conn = self._get(discord_id)
        if not conn:
            return

        if msg_type == 'register':
            conn.devices = msg.get('devices', [])
            conn.status = msg.get('status', AGENT_STATUS_IDLE)
            conn.device_id = msg.get('device_id')

            if db is not None:
                try:
                    result = await db.execute(select(User).where(User.discord_id == discord_id))
                    user = result.scalar_one_or_none()
                    if user is None:
                        user = User(discord_id=discord_id, username=f"user_{discord_id[-4:]}")
                        db.add(user)
                        await db.commit()
                        await db.refresh(user)
                        print(f"[agent] Created user {user.id} for discord_id={discord_id}")
                    conn.user_id = str(user.id)
                except Exception as e:
                    print(f"[agent] DB error: {e}")
                    conn.user_id = discord_id
            else:
                conn.user_id = discord_id

            await self._broadcast_devices()
            await self._broadcast_status()

        elif msg_type == 'status':
            conn.status = msg.get('status', conn.status)
            conn.device_id = msg.get('device_id', conn.device_id)
            await self._broadcast_status()

        elif msg_type == 'call_start':
            if conn._session_timer:
                conn._session_timer.cancel()
                conn._session_timer = None
            conn.active_session = None

        elif msg_type == 'call_end':
            await self._end_session(conn, db)

        elif msg_type == 'transcription':
            await self._broadcast_frontends({
                'type': 'transcription',
                'text': msg.get('text', ''),
                'timestamp': msg.get('timestamp', ''),
            })

            if conn.user_id:
                if conn.active_session is None:
                    conn.active_session = CallSession(id=str(uuid4()), start_time=datetime.utcnow())
                conn.active_session.transcriptions.append(msg)
                count = len(conn.active_session.transcriptions)
                if count >= MAX_SESSION_TRANSCRIPTIONS:
                    await self._end_session(conn, db)
                else:
                    self._reset_session_timer(conn, db)

        elif msg_type == 'devices':
            conn.devices = msg.get('devices', [])
            conn.device_id = msg.get('device_id', conn.device_id)
            await self._broadcast_devices()

    def _reset_session_timer(self, conn: AgentConnection, db):
        if conn._session_timer:
            conn._session_timer.cancel()
        conn._session_timer = asyncio.create_task(self._session_timeout(conn, db))

    async def _session_timeout(self, conn: AgentConnection, db):
        await asyncio.sleep(SESSION_TIMEOUT)
        await self._end_session(conn, db)

    async def _end_session(self, conn: AgentConnection, db: AsyncSession | None):
        if conn._ending_session_lock:
            return
        conn._ending_session_lock = True
        try:
            current = asyncio.current_task()
            if conn._session_timer is not None and conn._session_timer is not current:
                conn._session_timer.cancel()
                conn._session_timer = None

            session = conn.active_session
            conn.active_session = None

            if session is None or not session.transcriptions:
                return

            print(f"[agent] Call session {session.id} ended — {len(session.transcriptions)} chunks")
            try:
                await self._summarize_session(session, conn, db)
            except Exception as e:
                print(f"[agent] Session summarization error: {e}")
        finally:
            conn._ending_session_lock = False

    async def _summarize_session(self, session: CallSession, conn: AgentConnection, db: AsyncSession | None):
        texts = [t.get('text', '') for t in session.transcriptions if t.get('text')]
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
        await self._save_tasks(llm_tasks, conn, db)

    async def _save_tasks(self, tasks: list[dict], conn: AgentConnection, db: AsyncSession | None):
        if db and conn.user_id:
            for t in tasks:
                task = Task(
                    id=uuid4(),
                    user_id=conn.user_id,
                    source=SourceType.desktop_loopback.value,
                    content=t.get('content', ''),
                    author='desktop',
                    urgency=t.get('urgency', 'low'),
                    channel='desktop',
                    dm_sent='pending',
                )
                db.add(task)
            try:
                await db.commit()
                print(f"[agent] Created {len(tasks)} task(s) from call summary")
            except Exception as e:
                print(f"[agent] DB commit error: {e}")
        elif conn.discord_id:
            for t in tasks:
                json_add_task(
                    user_id=conn.discord_id,
                    content=t.get('content', ''),
                    urgency=t.get('urgency', 'low'),
                )
            print(f"[agent] Saved {len(tasks)} task(s) to JSON store")

    async def _broadcast_status(self):
        statuses = [
            {'discord_id': d, 'status': c.status, 'device_id': c.device_id, 'user_id': c.user_id}
            for d, c in self.agents.items()
        ]
        await self._broadcast_frontends({'type': 'agents', 'agents': statuses})

    async def _broadcast_devices(self):
        pass

    async def _broadcast_frontends(self, msg: dict):
        dead = []
        for ws in self.frontends:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_frontend(ws)


manager = AgentManager()
