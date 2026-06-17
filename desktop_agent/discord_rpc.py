import asyncio
import json
import struct
import threading
import uuid

OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2
OP_PING = 3
OP_PONG = 4


def _encode(op: int, data: dict) -> bytes:
    payload = json.dumps(data).encode('utf-8')
    return struct.pack('<II', op, len(payload)) + payload


def _decode(data: bytes) -> tuple[int, dict]:
    op, length = struct.unpack('<II', data[:8])
    return op, json.loads(data[8:8 + length].decode('utf-8'))


class DiscordRPCClient:
    def __init__(self, client_id: str):
        self._client_id = client_id
        self._handle = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self.on_voice_join = None
        self.on_voice_leave = None

    def _try_pipe(self, index: int) -> bool:
        try:
            import win32file
            import win32pipe
            import pywintypes

            name = rf'\\.\pipe\discord-ipc-{index}'
            try:
                win32pipe.WaitNamedPipe(name, 100)
            except pywintypes.error:
                return False

            self._handle = win32file.CreateFile(
                name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None,
                win32file.OPEN_EXISTING,
                0, None,
            )
            return True
        except Exception:
            return False

    def _write(self, data: bytes):
        import win32file
        win32file.WriteFile(self._handle, data)

    def _read(self) -> tuple[int, dict]:
        import win32file
        _, header = win32file.ReadFile(self._handle, 8)
        op, length = struct.unpack('<II', header)
        _, payload = win32file.ReadFile(self._handle, length)
        return op, json.loads(payload.decode('utf-8'))

    def _send(self, op: int, data: dict):
        if 'nonce' not in data:
            data['nonce'] = str(uuid.uuid4())
        self._write(_encode(op, data))

    def _handshake(self) -> bool:
        self._send(OP_HANDSHAKE, {'v': 1, 'client_id': self._client_id})
        op, data = self._read()
        ok = op == OP_FRAME and data.get('evt') == 'READY'
        user = data.get('data', {})
        print(f'[rpc] Handshake: op={op} ok={ok} user={user.get("username", "?")}#{user.get("discriminator", "?")}')
        return ok

    def _subscribe(self, event: str) -> bool:
        self._send(OP_FRAME, {
            'cmd': 'SUBSCRIBE',
            'evt': event,
            'args': {},
        })
        op, data = self._read()
        ok = data.get('evt') != 'ERROR'
        err = data.get('data', {}).get('message', '') if not ok else ''
        print(f'[rpc] Subscribe {event}: ok={ok}' + (f' error={err}' if err else ''))
        return ok

    def _run(self):
        connected = False
        for i in range(10):
            if self._try_pipe(i):
                if self._handshake():
                    connected = True
                    break
                self._close()

        if not connected:
            print('[rpc] Could not connect to Discord IPC.')
            print('[rpc] Make sure Discord desktop client is running.')
            self._running = False
            if self._loop:
                self._loop.call_soon_threadsafe(self._on_rpc_fail)
            return

        print(f'[rpc] Connected to Discord IPC')

        ok1 = self._subscribe('VOICE_STATE_CREATE')
        ok2 = self._subscribe('VOICE_STATE_DELETE')

        if not ok1 or not ok2:
            print('[rpc] Voice subscriptions failed — call detection unavailable')
            self._close()
            self._running = False
            if self._loop:
                self._loop.call_soon_threadsafe(self._on_rpc_fail)
            return

        print(f'[rpc] Subscribed to voice state events')

        import win32file
        import pywintypes

        while self._running:
            try:
                op, data = self._read()

                if op == OP_CLOSE:
                    print('[rpc] Connection closed by Discord')
                    break

                if op == OP_PING:
                    self._send(OP_PONG, {})
                    continue

                if op == OP_FRAME:
                    evt = data.get('evt')

                    if evt == 'VOICE_STATE_CREATE':
                        channel_id = data.get('data', {}).get('channel_id')
                        print(f'[rpc] Voice join: channel_id={channel_id}')
                        if self._loop and self.on_voice_join:
                            self._loop.call_soon_threadsafe(self.on_voice_join)

                    elif evt == 'VOICE_STATE_DELETE':
                        print(f'[rpc] Voice leave')
                        if self._loop and self.on_voice_leave:
                            self._loop.call_soon_threadsafe(self.on_voice_leave)

            except pywintypes.error as e:
                if e.winerror != 109:
                    print(f'[rpc] Pipe error: {e}')
                break
            except Exception as e:
                print(f'[rpc] Read error: {e}')
                break

        self._close()
        print('[rpc] Disconnected')
        if self._loop:
            self._loop.call_soon_threadsafe(self._on_rpc_disconnect)

    def _on_rpc_fail(self):
        self._running = False
        if self.on_voice_leave:
            self.on_voice_leave()

    def _on_rpc_disconnect(self):
        if self._running:
            self._running = False
            if self.on_voice_leave:
                self.on_voice_leave()

    def _close(self):
        if self._handle:
            try:
                import win32file
                win32file.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None

    def start(self, loop: asyncio.AbstractEventLoop | None = None):
        if self._running:
            return
        self._running = True
        self._loop = loop or asyncio.get_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._close()

    @property
    def is_connected(self) -> bool:
        return self._running and self._handle is not None
