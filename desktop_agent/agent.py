import asyncio
import functools
import json
import os
import sys
from datetime import datetime

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
BACKEND_WS_URL = BACKEND_URL.replace('http://', 'ws://').replace('https://', 'wss://')
WHISPER_MODEL = str(_config.get('whisper_model', os.getenv('WHISPER_MODEL', 'medium')))
WHISPER_DEVICE = str(_config.get('whisper_device', os.getenv('WHISPER_DEVICE', 'cpu')))
WHISPER_COMPUTE = str(_config.get('whisper_compute', os.getenv('WHISPER_COMPUTE_TYPE', 'int8')))
DISCORD_CLIENT_ID = str(_config.get('discord_client_id', os.getenv('DISCORD_CLIENT_ID', '')))
SAMPLE_RATE = 16000
BLOCK_DURATION = 30

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


async def capture_loop(ws=None, device_id: int = None, loopback: bool = False):
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
        if ws:
            await ws.send(json.dumps({'type': 'status', 'status': 'listening', 'device_id': device_id}))

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
                    if ws:
                        await ws.send(json.dumps({'type': 'transcription', 'text': text, 'timestamp': ts}))
                else:
                    print("[agent] Whisper returned empty")

                if _running:
                    if ws:
                        await ws.send(json.dumps({'type': 'status', 'status': 'listening', 'device_id': device_id}))


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
        if ws:
            await ws.send(json.dumps({'type': 'status', 'status': 'idle'}))


def transcribe_audio(audio: np.ndarray) -> str | None:
    model = get_whisper_model()
    segments, _ = model.transcribe(audio, language='ru', beam_size=5, vad_filter=True, temperature=0.0)
    text = ' '.join(seg.text for seg in segments)
    return text if text.strip() else None


async def pair_via_code() -> str | None:
    """
    Ask user for a pairing code and claim it from the backend.
    Returns discord_id on success, None on failure.
    """
    print("\n[agent] ════════════════════════════════════════")
    print("[agent]   Добро пожаловать! Агент не настроен.")
    print("[agent]   1. Откройте сайт → Download Agent")
    print("[agent]   2. Нажмите «Сгенерировать код»")
    print("[agent]   3. Введите код ниже")
    print("[agent] ════════════════════════════════════════\n")
    code = input("[agent] Введите пин-код: ").strip().upper()
    if not code:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{BACKEND_URL}/api/pairing/claim/{code}")
            if resp.status_code == 200:
                data = resp.json()
                discord_id = data.get('discord_id')
                print(f"[agent] Подключение успешно! Discord ID: {discord_id}")
                return discord_id
            elif resp.status_code == 410:
                print("[agent] Ошибка: код истёк (действителен 10 минут). Сгенерируйте новый на сайте.")
            else:
                print(f"[agent] Ошибка: код не найден. Проверьте правильность ввода.")
    except Exception as e:
        print(f"[agent] Ошибка подключения к серверу: {e}")
    return None


async def main():
    import websockets
    import httpx

    discord_id = _config.get('discord_id') or os.getenv('DISCORD_USER_ID')
    if not discord_id:
        users_store_path = BASE_DIR.parent / 'users_store.json'
        try:
            if users_store_path.exists():
                with open(users_store_path) as f:
                    users = json.load(f)
                    if users:
                        discord_id = users[0].get('discord_id')
        except Exception:
            pass

    # If still no ID, try pairing via code
    if not discord_id:
        discord_id = await pair_via_code()

    query = f'?discord_id={discord_id}' if discord_id else ''
    ws_url = f"{BACKEND_WS_URL}/api/agent/ws{query}"

    if not discord_id:
        print("[agent] ERROR: не удалось получить Discord ID.")
        print("[agent] Откройте сайт → Download Agent, скопируйте ID и вставьте в config.yml")
        input("[agent] Нажмите Enter для выхода...")
        return

    print(f"[agent] Starting — connecting to {BACKEND_URL}")
    print(f"[agent] Discord ID: {discord_id}")
    print(f"[agent] Whisper model: {WHISPER_MODEL} ({WHISPER_DEVICE}, {WHISPER_COMPUTE})")

    while True:
        print(f"[agent] Connecting to WebSocket: {ws_url}")
        try:
            async for ws in websockets.connect(ws_url, ping_timeout=10, close_timeout=10):
                print(f"[agent] Connected to WebSocket")
                rpc: DiscordRPCClient | None = None
                capture_task: asyncio.Task | None = None
                rpc_available = False
                try:
                    print(f"[agent] Registered as discord user {discord_id}")

                    devices_list = []
                    lb_id = get_loopback_device_id()
                    cable_id = get_cable_device_id()
                    vm_id = get_voicemeeter_device_id()
                    mic_id = get_microphone_device_id()
                    if lb_id is not None:
                        devices_list.append({'id': lb_id, 'name': 'System Audio (loopback)'})
                    if cable_id is not None and cable_id != lb_id:
                        devices_list.append({'id': cable_id, 'name': 'VB-CABLE'})
                    if vm_id is not None and vm_id != lb_id and vm_id != cable_id:
                        devices_list.append({'id': vm_id, 'name': 'Voicemeeter'})
                    if mic_id is not None and mic_id != lb_id and mic_id != cable_id and mic_id != vm_id:
                        devices_list.append({'id': mic_id, 'name': 'Microphone'})

                    register_msg = {
                        'type': 'register',
                        'devices': devices_list,
                        'status': 'idle',
                        'device_id': lb_id if lb_id is not None else mic_id,
                        'discord_id': discord_id,
                    }
                    await ws.send(json.dumps(register_msg))

                    # ── RPC watcher ──────────────────────────────────────────────

                    async def stop_capture(reason='unknown'):
                        nonlocal capture_task
                        global _running
                        print(f"[agent] stop_capture (reason={reason})")
                        _running = False
                        if capture_task and not capture_task.done():
                            capture_task.cancel()
                            try:
                                await capture_task
                            except (asyncio.CancelledError, Exception):
                                pass
                            capture_task = None
                        try:
                            await ws.send(json.dumps({'type': 'call_end'}))
                            print("[agent] -> call_end sent")
                        except Exception as e:
                            print(f"[agent] -> call_end send error: {e}")

                    async def start_capture(reason='unknown'):
                        nonlocal capture_task, rpc_available
                        print(f"[agent] start_capture (reason={reason})")
                        await stop_capture(reason='restart')
                        device_id = lb_id if lb_id is not None else mic_id
                        if device_id is None:
                            print("[agent] No audio device available")
                            return
                        capture_task = asyncio.create_task(
                            capture_loop(ws, device_id, loopback=(lb_id is not None and device_id == lb_id))
                        )
                        try:
                            await ws.send(json.dumps({'type': 'call_start'}))
                            print("[agent] -> call_start sent")
                        except Exception as e:
                            print(f"[agent] -> call_start send error: {e}")

                    def on_join():
                        print("[rpc] on_join callback fired")
                        asyncio.create_task(start_capture(reason='rpc_join'))

                    def on_leave():
                        print("[rpc] on_leave callback fired")
                        asyncio.create_task(stop_capture(reason='rpc_leave'))

                    if DISCORD_CLIENT_ID:
                        rpc = DiscordRPCClient(DISCORD_CLIENT_ID)
                        rpc.on_voice_join = on_join
                        rpc.on_voice_leave = on_leave
                        rpc.start()
                        await asyncio.sleep(3)
                        if rpc.is_connected:
                            rpc_available = True
                            print("[agent] RPC connected — will capture during calls only")
                        else:
                            print("[agent] RPC not available, falling back to always-on capture")

                    if not rpc_available:
                        device_id = lb_id if lb_id is not None else mic_id
                        if device_id is not None:
                            print("[agent] Fallback: auto-start capture")
                            capture_task = asyncio.create_task(
                                capture_loop(ws, device_id, loopback=(lb_id is not None))
                            )

                    # ── Command loop ──────────────────────────────────────────────
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        if msg.get('type') != 'command':
                            continue

                        action = msg.get('action')
                        print(f"[agent] Command from backend: {action}")

                        if action == 'start':
                            await start_capture(reason='backend_cmd')

                        elif action == 'stop':
                            await stop_capture(reason='backend_cmd')

                        elif action == 'select_device':
                            pass

                except websockets.ConnectionClosed:
                    print("[agent] Disconnected. Reconnecting in 3s...")
                    await asyncio.sleep(3)
                except Exception as e:
                    print(f"[agent] Error: {e}")
                    await asyncio.sleep(3)
                finally:
                    await stop_capture()
                    if rpc:
                        rpc.stop()
        except (OSError, websockets.InvalidURI, asyncio.TimeoutError) as e:
            print(f"[agent] Connection failed: {e}, retrying in 5s...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"[agent] Unexpected connection error: {e}, retrying in 5s...")
            await asyncio.sleep(5)


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
            await capture_loop(ws=None, device_id=device_id, loopback=(lb_id is not None))

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
