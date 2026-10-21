import asyncio
import functools
import json
import os
import random
import sys
from collections import deque
from datetime import datetime
from urllib.parse import urlparse
from uuid import uuid4

print = functools.partial(print, flush=True)
from pathlib import Path

import httpx
import numpy as np
import yaml
from dotenv import load_dotenv
from discord_rpc import DiscordRPCClient

# Determine base directory (next to .exe when bundled, next to .py when script)
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent.resolve()
else:
    BASE_DIR = Path(__file__).parent.resolve()
    sys.path.insert(0, str(BASE_DIR))
    sys.path.insert(0, str(BASE_DIR.parent))

# Try loading .env (optional — settings come from config.yml first)
dotenv_path = BASE_DIR.parent / '.env'
if dotenv_path.exists():
    load_dotenv(dotenv_path)

# Load config.yml
CONFIG_PATH = BASE_DIR / 'config.yml'
_config = {}
if CONFIG_PATH.exists():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        _config = yaml.safe_load(f) or {}

BACKEND_URL = str(_config.get('backend_url', os.getenv('BACKEND_URL', 'http://localhost:8000'))).rstrip('/')
_backend = urlparse(BACKEND_URL)
BACKEND_WS_URL = _backend._replace(scheme='wss' if _backend.scheme == 'https' else 'ws').geturl()
LOOPBACK_HOSTS = {'localhost', '127.0.0.1', '::1'}
WHISPER_MODEL = str(_config.get('whisper_model', os.getenv('WHISPER_MODEL', 'medium')))
WHISPER_DEVICE = str(_config.get('whisper_device', os.getenv('WHISPER_DEVICE', 'cpu')))
WHISPER_COMPUTE = str(_config.get('whisper_compute', os.getenv('WHISPER_COMPUTE_TYPE', 'int8')))
DISCORD_CLIENT_ID = str(_config.get('discord_client_id', os.getenv('DISCORD_CLIENT_ID', '')))
SAMPLE_RATE = 16000
BLOCK_DURATION = 30
# If Whisper is slower than real time, keep at most this much unprocessed audio
# and drop the oldest beyond it instead of growing memory and latency forever.
MAX_BACKLOG_SECONDS = 90

WS_MAX_SIZE = 64 * 1024
# Unacknowledged messages kept across reconnects (~4 hours of transcriptions).
OUTBOX_MAX = 500
# Replay pace after a reconnect; must stay below the server's per-connection rate limit.
OUTBOX_SEND_RATE = 5
RECONNECT_MIN_DELAY = 1
RECONNECT_MAX_DELAY = 60
CLOSE_REPLACED = 4000
CLOSE_UNAUTHORIZED = 4401

_whisper_model = None
_audio_buffer = []
_accumulated = np.array([], dtype=np.float32)
_stream = None
_running = False
_vad_model = None


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        print(f"[agent] Loading {WHISPER_MODEL}...")
        _whisper_model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
    return _whisper_model


def get_vad_model():
    global _vad_model
    if _vad_model is None:
        from silero_vad import load_silero_vad
        print("[agent] Loading Silero VAD...")
        _vad_model = load_silero_vad()
    return _vad_model


def get_loopback_device_id() -> int | None:
    import sounddevice as sd
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()

    # Find WASAPI host API
    wasapi_index = next((i for i, ha in enumerate(hostapis) if 'wasapi' in ha['name'].lower()), None)

    if wasapi_index is not None:
        # Loopback in WASAPI uses an OUTPUT device opened as input with the loopback flag.
        default_out = sd.default.device[1]
        if default_out is not None and default_out != -1:
            default_name = devices[default_out]['name'].strip()
            for i, d in enumerate(devices):
                if (d['hostapi'] == wasapi_index
                        and d['max_output_channels'] > 0
                        and d['name'].strip() == default_name):
                    info = sd.query_devices(i)
                    print(f"[agent] WASAPI loopback device: [{i}] {info['name']}")
                    return i

    # Fallback: any device with 'loopback' in name (rare, some drivers expose it)
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0 and 'loopback' in d['name'].lower():
            print(f"[agent] Loopback device: [{i}] {d['name']}")
            return i

    return None


def get_cable_device_id() -> int | None:
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d['max_input_channels'] > 0 and 'cable' in d['name'].lower():
            info = sd.query_devices(i)
            print(f"[agent] CABLE device: [{i}] {info['name']}")
            return i
    return None


def get_voicemeeter_device_id() -> int | None:
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d['max_input_channels'] > 0 and ('voicemeeter' in d['name'].lower() or 'vb-audio' in d['name'].lower()):
            info = sd.query_devices(i)
            print(f"[agent] Voicemeeter device: [{i}] {info['name']}")
            return i
    return None


def auto_install_cable() -> bool:
    import subprocess
    import ctypes
    import tempfile

    def is_admin():
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    # Try bundled installer first (frozen: _internal/vbcable/, script: vbcable/)
    bundled = (BASE_DIR / "_internal" / "vbcable" / "VBCABLE_Setup_x64.exe")
    if not bundled.exists():
        bundled = BASE_DIR / "vbcable" / "VBCABLE_Setup_x64.exe"
    if bundled.exists():
        installer = bundled
        print("[agent] Using bundled VB-CABLE installer")
    else:
        print("[agent] VB-CABLE not found. Downloading installer...")
        installer = Path(tempfile.gettempdir()) / "VBCABLE_Setup_x64.exe"
        try:
            r = subprocess.run(
                ["curl.exe", "-L", "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip", "-o", str(installer) + ".zip"],
                capture_output=True, text=True, timeout=60
            )
            if r.returncode != 0:
                print(f"[agent] Download failed: {r.stderr[:200]}")
                print("[agent] Download manually: https://vb-audio.com/Cable/")
                return False
            import zipfile
            with zipfile.ZipFile(str(installer) + ".zip") as zf:
                zf.extract("VBCABLE_Setup_x64.exe", Path(tempfile.gettempdir()))
            (Path(tempfile.gettempdir()) / "VBCABLE_Setup_x64.exe").rename(installer)
        except Exception as e:
            print(f"[agent] Download/extract error: {e}")
            print("[agent] Download manually: https://vb-audio.com/Cable/")
            return False

    print("[agent] Running installer (UAC prompt may appear)...")

    if is_admin():
        try:
            r = subprocess.run([str(installer), "/S"], capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                print("[agent] VB-CABLE installed successfully")
                return True
        except subprocess.TimeoutExpired:
            pass
    else:
        ps_cmd = (
            f'Start-Process -FilePath "{installer}" '
            f'-ArgumentList "/S" '
            f'-Verb RunAs -Wait'
        )
        try:
            r = subprocess.run(
                ["powershell", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=120
            )
            if r.returncode == 0:
                print("[agent] VB-CABLE installed successfully")
                return True
        except subprocess.TimeoutExpired:
            pass

    print("[agent] Installer may need manual interaction.")
    return False


def get_microphone_device_id() -> int | None:
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d['max_input_channels'] > 0:
            info = sd.query_devices(i)
            print(f"[agent] Mic device: [{i}] {info['name']}")
            return i
    return None


def _resample(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    if from_rate == to_rate:
        return audio
    ratio = to_rate / from_rate
    new_len = max(1, int(len(audio) * ratio))
    x_old = np.linspace(0, 1, len(audio))
    x_new = np.linspace(0, 1, new_len)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def _open_native_stream(device_id: int, channels: int, extra, callback) -> tuple:
    import sounddevice as sd
    info = sd.query_devices(device_id)
    native_rate = int(info['default_samplerate'])
    stream = sd.InputStream(
        device=device_id, channels=channels,
        samplerate=native_rate,
        callback=callback, dtype='float32',
        extra_settings=extra,
    )
    return stream, native_rate


async def capture_loop(emit=None, device_id: int = None, loopback: bool = False):
    """emit(msg, reliable=False) delivers messages to the backend; None = local mode."""
    global _audio_buffer, _accumulated, _running, _stream
    import sounddevice as sd

    _running = True
    _audio_buffer = []
    _accumulated = np.array([], dtype=np.float32)
    wasapi_cap = None  # WasapiCapture instance
    mic_stream = None
    mic_rate = SAMPLE_RATE

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[agent] Stream status: {status}")
        if indata.shape[1] > 1:
            indata = np.mean(indata, axis=1, keepdims=True)
        _audio_buffer.append(indata.copy())

    try:
        device_rate = SAMPLE_RATE

        # ── Try native WASAPI loopback first ──────────────────────────
        if loopback:
            try:
                from wasapi_capture import WasapiCapture
                cap = WasapiCapture()
                cap.start(target_rate=SAMPLE_RATE)
                wasapi_cap = cap
                device_rate = SAMPLE_RATE
                print(f"[agent] Started WASAPI loopback on device {device_id}")
            except Exception as e:
                print(f"[agent] Native WASAPI loopback failed ({e})")

        # ── Fall back to PortAudio if loopback failed or not loopback ─
        if wasapi_cap is None:
            ch = 1
            extra = None
            if loopback:
                try:
                    from sounddevice import WasapiSettings
                    extra = WasapiSettings(exclusive=False, auto_convert=True)
                    extra._streaminfo.flags |= 0x4
                    ch = 2
                except ImportError:
                    loopback = False

            try:
                _stream, device_rate = _open_native_stream(device_id, ch, extra, callback)
                _stream.start()
            except Exception as e:
                if not loopback:
                    raise

                print(f"[agent] WASAPI loopback via PortAudio failed ({e})")
                fallback_devices = [
                    ('VB-CABLE', get_cable_device_id),
                    ('VB-CABLE (auto-install)', None),
                    ('Voicemeeter', get_voicemeeter_device_id),
                    ('Microphone', get_microphone_device_id),
                ]

                opened = False
                for name, lookup in fallback_devices:
                    alt = None
                    if name == 'VB-CABLE (auto-install)':
                        if get_cable_device_id() is not None:
                            continue
                        print(f"[agent] VB-CABLE not found, attempting auto-install...")
                        auto_install_cable()
                        print(f"[agent] Waiting for device to appear...")
                        import time
                        for _ in range(10):
                            sd._terminate()
                            sd._initialize()
                            alt = get_cable_device_id()
                            if alt is not None:
                                break
                            time.sleep(1)
                    else:
                        alt = lookup()
                        if alt is None or alt == device_id:
                            continue

                    if alt is not None:
                        print(f"[agent] Falling back to {name}")
                        device_id = alt
                        try:
                            _stream, device_rate = _open_native_stream(device_id, 1, None, callback)
                            _stream.start()
                            loopback = False
                            opened = True
                            break
                        except Exception as e2:
                            print(f"[agent] {name} failed ({e2})")
                            continue

                if not opened:
                    raise RuntimeError("No audio device available")

        # ── Start parallel mic capture (if WASAPI loopback active) ────
        if wasapi_cap is not None:
            mic_dev = get_microphone_device_id()
            if mic_dev is not None and mic_dev != device_id:
                try:
                    mic_info = sd.query_devices(mic_dev)
                    mic_rate = int(mic_info['default_samplerate'])
                    mic_stream = sd.InputStream(
                        device=mic_dev, channels=1,
                        samplerate=mic_rate,
                        callback=callback, dtype='float32',
                    )
                    mic_stream.start()
                    print(f"[agent] Mic capture started on device {mic_dev}: {mic_info['name']} @ {mic_rate} Hz")
                except Exception as e:
                    print(f"[agent] Mic capture failed ({e})")
                    mic_stream = None

        dev_info = sd.query_devices(device_id)
        mode = 'wasapi_loopback' if wasapi_cap else ('loopback' if loopback else 'mic')
        print(f"[agent] Started {mode} capture on device {device_id}: {dev_info['name']} @ {device_rate} Hz")
        if emit:
            emit({'type': 'status', 'status': 'listening', 'device_id': device_id})

        # ── Main capture loop ─────────────────────────────────────────
        chunk_size = device_rate * BLOCK_DURATION
        while _running:
            await asyncio.sleep(0.01)

            if wasapi_cap is not None:
                loopback_data = wasapi_cap.read_and_resample()

                # Mix with mic if available
                if mic_stream is not None and _audio_buffer:
                    mic_chunk = np.concatenate(_audio_buffer).flatten()
                    _audio_buffer = []
                    mic_chunk = _resample(mic_chunk, mic_rate, SAMPLE_RATE)

                    if loopback_data is not None and len(loopback_data) > 0:
                        min_len = min(len(loopback_data), len(mic_chunk))
                        if min_len > 0:
                            mixed = loopback_data[:min_len] + mic_chunk[:min_len]
                            _accumulated = np.concatenate([_accumulated, mixed])
                    else:
                        _accumulated = np.concatenate([_accumulated, mic_chunk])
                elif loopback_data is not None and len(loopback_data) > 0:
                    _accumulated = np.concatenate([_accumulated, loopback_data])
            else:
                if not _audio_buffer:
                    continue
                chunk = np.concatenate(_audio_buffer).flatten()
                _audio_buffer = []
                _accumulated = np.concatenate([_accumulated, chunk])

            max_backlog = device_rate * MAX_BACKLOG_SECONDS
            if len(_accumulated) > max_backlog:
                dropped = len(_accumulated) - max_backlog
                _accumulated = _accumulated[dropped:]
                print(f"[agent] WARNING: transcription is falling behind, dropped {dropped / device_rate:.0f}s of oldest audio")

            if len(_accumulated) >= chunk_size:
                segment = _accumulated[:chunk_size]
                _accumulated = _accumulated[chunk_size:]

                segment = _resample(segment, device_rate, SAMPLE_RATE)

                vad = get_vad_model()
                from silero_vad import get_speech_timestamps
                speech_ts = get_speech_timestamps(segment, vad, sampling_rate=SAMPLE_RATE)
                if not speech_ts:
                    print(f"[agent] VAD: no speech in 30s block (max={segment.max():.4f})")
                    continue

                loop = asyncio.get_event_loop()
                text = await loop.run_in_executor(None, transcribe_audio, segment)
                if text:
                    print(f"[agent] Transcription: {text[:80]}...")
                    ts = datetime.utcnow().isoformat()
                    if emit:
                        emit({'type': 'transcription', 'text': text, 'timestamp': ts}, reliable=True)
                else:
                    print("[agent] Whisper returned empty")

                if _running and emit:
                    emit({'type': 'status', 'status': 'listening', 'device_id': device_id})


    except Exception as e:
        print(f"[agent] Capture error: {e}")
    finally:
        if _stream:
            _stream.stop()
            _stream.close()
            _stream = None
        if mic_stream:
            try:
                mic_stream.stop()
                mic_stream.close()
            except Exception:
                pass
        if wasapi_cap:
            try:
                wasapi_cap.stop()
            except Exception:
                pass
        _running = False
        if emit:
            emit({'type': 'status', 'status': 'idle', 'device_id': device_id})


def transcribe_audio(audio: np.ndarray) -> str | None:
    model = get_whisper_model()
    segments, _ = model.transcribe(audio, language='ru', beam_size=5, vad_filter=True, temperature=0.0)
    text = ' '.join(seg.text for seg in segments)
    return text if text.strip() else None


def backend_is_secure(url: str) -> bool:
    """Only TLS is allowed, except for a backend on this machine (local development)."""
    parsed = urlparse(url)
    if parsed.scheme == 'https':
        return True
    return parsed.scheme == 'http' and parsed.hostname in LOOPBACK_HOSTS


def save_config_value(key: str, value):
    data = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    _config.clear()
    _config.update(data)


async def pair_via_code() -> str | None:
    """
    Ask user for a pairing code and exchange it for an agent token.
    Returns the token on success, None on failure.
    """
    print("\n[agent] ════════════════════════════════════════")
    print("[agent]   Агент не привязан к аккаунту.")
    print("[agent]   1. Откройте сайт → Download Agent")
    print("[agent]   2. Нажмите «Сгенерировать код»")
    print("[agent]   3. Введите код ниже")
    print("[agent] ════════════════════════════════════════\n")
    code = (await asyncio.to_thread(input, "[agent] Введите пин-код: ")).strip()
    if not code:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{BACKEND_URL}/api/pairing/claim", json={'code': code})
            if resp.status_code == 200:
                data = resp.json()
                print(f"[agent] Подключение успешно! Discord ID: {data.get('discord_id')}")
                save_config_value('discord_id', data.get('discord_id'))
                save_config_value('agent_token', data['agent_token'])
                return data['agent_token']
            elif resp.status_code == 429:
                print("[agent] Ошибка: слишком много попыток. Подождите 10 минут.")
            else:
                print("[agent] Ошибка: код не найден или истёк (действителен 10 минут). Сгенерируйте новый на сайте.")
    except Exception as e:
        print(f"[agent] Ошибка подключения к серверу: {e}")
    return None


class Outbox:
    """
    Messages that must reach the backend (transcriptions, call boundaries).
    Each gets a sequence number; it stays queued until the server acks it and is
    re-sent after a reconnect. The server drops duplicates by (stream_id, seq).
    """

    def __init__(self, maxlen: int):
        self.stream_id = uuid4().hex
        self._seq = 0
        self._maxlen = maxlen
        self._items: deque[dict] = deque()
        self._event = asyncio.Event()

    def put(self, msg: dict):
        self._seq += 1
        if len(self._items) >= self._maxlen:
            dropped = self._items.popleft()
            print(f"[agent] WARNING: outbox full, dropped unsent {dropped.get('type')} #{dropped['seq']}")
        self._items.append({**msg, 'seq': self._seq, 'stream_id': self.stream_id})
        self._event.set()

    def ack(self, seq: int):
        while self._items and self._items[0]['seq'] <= seq:
            self._items.popleft()

    def __len__(self):
        return len(self._items)

    async def pump(self, ws):
        """Send everything not yet sent on this connection; runs until the connection dies."""
        sent_upto = 0
        while True:
            pending = [m for m in self._items if m['seq'] > sent_upto]
            if not pending:
                self._event.clear()
                await self._event.wait()
                continue
            for m in pending:
                await ws.send(json.dumps(m))
                sent_upto = m['seq']
                await asyncio.sleep(1 / OUTBOX_SEND_RATE)


class Agent:
    def __init__(self, token: str):
        self.token = token
        self.outbox = Outbox(OUTBOX_MAX)
        self.ws = None
        self.status = {'type': 'status', 'status': 'idle', 'device_id': None}
        self.capture_task: asyncio.Task | None = None
        self.devices: list[dict] = []
        self.lb_id: int | None = None
        self.mic_id: int | None = None
        self.rpc: DiscordRPCClient | None = None

    # ── Messaging ────────────────────────────────────────────────────────

    def emit(self, msg: dict, reliable: bool = False):
        if msg.get('type') == 'status':
            self.status = msg
        if reliable:
            self.outbox.put(msg)
        elif self.ws is not None:
            asyncio.create_task(self._send_best_effort(self.ws, msg))

    @staticmethod
    async def _send_best_effort(ws, msg: dict):
        try:
            await ws.send(json.dumps(msg))
        except Exception:
            pass  # status is re-sent in 'register' after reconnect

    # ── Capture ──────────────────────────────────────────────────────────

    def discover_devices(self):
        self.lb_id = get_loopback_device_id()
        cable_id = get_cable_device_id()
        vm_id = get_voicemeeter_device_id()
        self.mic_id = get_microphone_device_id()
        lb_id, mic_id = self.lb_id, self.mic_id
        devices = []
        if lb_id is not None:
            devices.append({'id': lb_id, 'name': 'System Audio (loopback)'})
        if cable_id is not None and cable_id != lb_id:
            devices.append({'id': cable_id, 'name': 'VB-CABLE'})
        if vm_id is not None and vm_id != lb_id and vm_id != cable_id:
            devices.append({'id': vm_id, 'name': 'Voicemeeter'})
        if mic_id is not None and mic_id not in (lb_id, cable_id, vm_id):
            devices.append({'id': mic_id, 'name': 'Microphone'})
        self.devices = devices
        self.status['device_id'] = lb_id if lb_id is not None else mic_id

    async def stop_capture(self, reason='unknown'):
        global _running
        if self.capture_task is None:
            return
        print(f"[agent] stop_capture (reason={reason})")
        _running = False
        task, self.capture_task = self.capture_task, None
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self.emit({'type': 'call_end'}, reliable=True)

    async def start_capture(self, reason='unknown'):
        print(f"[agent] start_capture (reason={reason})")
        await self.stop_capture(reason='restart')
        device_id = self.lb_id if self.lb_id is not None else self.mic_id
        if device_id is None:
            print("[agent] No audio device available")
            return
        self.emit({'type': 'call_start'}, reliable=True)
        self.capture_task = asyncio.create_task(
            capture_loop(self.emit, device_id, loopback=(self.lb_id is not None and device_id == self.lb_id))
        )

    async def setup_capture(self):
        """Capture lives for the whole process, independent of the WebSocket connection."""
        self.discover_devices()
        if DISCORD_CLIENT_ID:
            self.rpc = DiscordRPCClient(DISCORD_CLIENT_ID)
            self.rpc.on_voice_join = lambda: asyncio.create_task(self.start_capture(reason='rpc_join'))
            self.rpc.on_voice_leave = lambda: asyncio.create_task(self.stop_capture(reason='rpc_leave'))
            self.rpc.start()
            await asyncio.sleep(3)
            if self.rpc.is_connected:
                print("[agent] RPC connected — will capture during calls only")
                return
            print("[agent] RPC not available, falling back to always-on capture")
        await self.start_capture(reason='always_on')

    # ── Connection ───────────────────────────────────────────────────────

    async def _handle_server_message(self, raw):
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        if msg.get('type') == 'ack' and isinstance(msg.get('seq'), int):
            self.outbox.ack(msg['seq'])
        elif msg.get('type') == 'command':
            action = msg.get('action')
            print(f"[agent] Command from backend: {action}")
            if action == 'start':
                asyncio.create_task(self.start_capture(reason='backend_cmd'))
            elif action == 'stop':
                asyncio.create_task(self.stop_capture(reason='backend_cmd'))

    async def _run_connection(self, ws):
        await ws.send(json.dumps({
            'type': 'register',
            'devices': self.devices,
            'status': self.status.get('status', 'idle'),
            'device_id': self.status.get('device_id'),
        }))
        self.ws = ws
        if len(self.outbox):
            print(f"[agent] Re-sending {len(self.outbox)} buffered message(s)")
        pump = asyncio.create_task(self.outbox.pump(ws))
        try:
            async for raw in ws:
                await self._handle_server_message(raw)
        finally:
            self.ws = None
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)

    async def connection_loop(self) -> str:
        """Returns 'unauthorized' or 'replaced' when reconnecting makes no sense."""
        import websockets
        from websockets.exceptions import ConnectionClosed, InvalidStatus, InvalidHandshake, InvalidURI

        loop = asyncio.get_running_loop()
        delay = RECONNECT_MIN_DELAY
        ws_url = f"{BACKEND_WS_URL}/api/agent/ws"

        while True:
            print(f"[agent] Connecting to WebSocket: {ws_url}")
            connected_at = None
            close_code = None
            try:
                async with websockets.connect(
                    ws_url,
                    additional_headers={'Authorization': f'Bearer {self.token}'},
                    open_timeout=15,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                    max_size=WS_MAX_SIZE,
                ) as ws:
                    connected_at = loop.time()
                    print("[agent] Connected to WebSocket")
                    try:
                        await self._run_connection(ws)
                    except ConnectionClosed:
                        pass
                    close_code = ws.close_code
            except InvalidStatus as e:
                if e.response.status_code in (401, 403):
                    return 'unauthorized'
                print(f"[agent] Server rejected connection: HTTP {e.response.status_code}")
            except (OSError, asyncio.TimeoutError, InvalidHandshake, InvalidURI) as e:
                print(f"[agent] Connection failed: {e}")
            except Exception as e:
                print(f"[agent] Unexpected connection error: {e}")

            if close_code == CLOSE_REPLACED:
                return 'replaced'
            if close_code == CLOSE_UNAUTHORIZED:
                return 'unauthorized'

            # Exponential backoff with jitter; reset once a connection has proven stable.
            if connected_at is not None and loop.time() - connected_at > 30:
                delay = RECONNECT_MIN_DELAY
            sleep_for = delay * random.uniform(0.5, 1.0)
            print(f"[agent] Disconnected (code={close_code}). Reconnecting in {sleep_for:.1f}s...")
            await asyncio.sleep(sleep_for)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)


async def main():
    if not backend_is_secure(BACKEND_URL):
        print(f"[agent] ERROR: backend_url={BACKEND_URL} не использует https.")
        print("[agent] Транскрипции разговоров нельзя передавать без шифрования. Укажите https:// в config.yml.")
        await asyncio.to_thread(input, "[agent] Нажмите Enter для выхода...")
        return

    token = _config.get('agent_token') or os.getenv('AGENT_TOKEN')
    if not token and _config.get('discord_id'):
        print("[agent] config.yml от старой версии: нужна повторная привязка по коду.")

    print(f"[agent] Starting — backend {BACKEND_URL}")
    print(f"[agent] Whisper model: {WHISPER_MODEL} ({WHISPER_DEVICE}, {WHISPER_COMPUTE})")

    agent = None
    while True:
        while not token:
            token = await pair_via_code()

        if agent is None:
            agent = Agent(token)
            await agent.setup_capture()
        agent.token = token

        result = await agent.connection_loop()
        if result == 'replaced':
            print("[agent] К этому аккаунту подключился другой экземпляр агента. Этот экземпляр завершает работу.")
            await agent.stop_capture(reason='replaced')
            if agent.rpc:
                agent.rpc.stop()
            await asyncio.to_thread(input, "[agent] Нажмите Enter для выхода...")
            return
        # 'unauthorized': token expired or revoked — pair again, keep capturing meanwhile.
        print("[agent] Сервер отклонил токен агента. Нужна повторная привязка.")
        save_config_value('agent_token', None)
        token = None


if __name__ == '__main__':
    import sys as _sys
    # --local = офлайн-режим без бэкенда (только транскрипция в консоль)
    if '--local' in _sys.argv:
        async def local_main():
            lb_id = get_loopback_device_id()
            mic_id = get_microphone_device_id()
            device_id = lb_id if lb_id is not None else mic_id
            if device_id is None:
                print("[agent] No audio device available")
                return
            print(f"[agent] Starting local capture (no backend)...")
            await capture_loop(emit=None, device_id=device_id, loopback=(lb_id is not None))

        try:
            asyncio.run(local_main())
        except KeyboardInterrupt:
            print("\n[agent] Shutting down...")
            import os
            os._exit(0)
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\n[agent] Shutting down...")
            import os
            os._exit(0)
