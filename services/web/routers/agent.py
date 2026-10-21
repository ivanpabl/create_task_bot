import asyncio
import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.responses import PlainTextResponse, Response

from services.web.agent_manager import manager, CLOSE_UNAUTHORIZED
from services.web.auth import create_agent_token, verify_agent_token, verify_user_jwt
from services.web.dependencies import get_current_user
from services.web.config import settings
from services.web.ratelimit import TokenBucket

router = APIRouter(prefix='/api/agent', tags=['agent'])

_base_zip_path = Path(__file__).resolve().parents[3] / 'desktop_agent' / 'dist' / 'base.zip'
_base_zip: bytes | None = None


def _load_base_zip() -> bytes:
    global _base_zip
    if _base_zip is None:
        if not _base_zip_path.is_file():
            raise HTTPException(status_code=404, detail='Agent build not found. Run build_exe.ps1 first.')
        _base_zip = _base_zip_path.read_bytes()
    return _base_zip


# Hard cap on a single WebSocket frame, in bytes. Also pass --ws-max-size to uvicorn
# so oversized frames are rejected before they are buffered.
MAX_WS_MESSAGE_BYTES = 64 * 1024
AUTH_TIMEOUT = 10

# Agents send one transcription per ~30s; the burst covers replay after a reconnect
# (the agent paces its replay below this rate).
AGENT_RATE, AGENT_BURST = 10.0, 60
FRONTEND_RATE, FRONTEND_BURST = 2.0, 10


def _user_ids(current_user) -> tuple[str, str]:
    if hasattr(current_user, 'id'):
        return str(current_user.id), str(current_user.discord_id)
    return str(current_user.get('id')), str(current_user.get('discord_id'))


def _agent_config(current_user) -> str:
    user_id, discord_id = _user_ids(current_user)
    token = create_agent_token(user_id, discord_id)
    return f"""# Discord Task Bot — Desktop Agent Configuration
# agent_token is a personal credential: do not share this file.
discord_id: "{discord_id}"
agent_token: "{token}"
backend_url: "{settings.backend_url.rstrip('/')}"
whisper_model: medium
whisper_device: cpu
whisper_compute: int8
"""


@router.get('/config')
async def download_config(current_user=Depends(get_current_user)):
    config = _agent_config(current_user)
    return PlainTextResponse(
        config,
        media_type='text/plain',
        headers={'Content-Disposition': 'attachment; filename="config.yml"', 'Cache-Control': 'no-store'},
    )


@router.get('/download')
async def download_agent(current_user=Depends(get_current_user)):
    base = _load_base_zip()
    config_yml = _agent_config(current_user)

    buf = io.BytesIO(base)
    with zipfile.ZipFile(buf, 'a', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('config.yml', config_yml)

    return Response(
        content=buf.getvalue(),
        media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename="discord-task-agent.zip"', 'Cache-Control': 'no-store'},
    )


class _TextTooLarge(Exception):
    pass


async def _receive_text(ws: WebSocket) -> str:
    message = await ws.receive()
    if message['type'] == 'websocket.disconnect':
        raise WebSocketDisconnect(message.get('code', 1000))
    text = message.get('text')
    if text is None:
        raise _TextTooLarge('binary frames are not accepted')
    if len(text) > MAX_WS_MESSAGE_BYTES or len(text.encode('utf-8')) > MAX_WS_MESSAGE_BYTES:
        raise _TextTooLarge('message too large')
    return text


async def _receive_json(ws: WebSocket) -> dict | None:
    raw = await _receive_text(ws)
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return msg if isinstance(msg, dict) else None


@router.get('/status')
async def get_status(current_user=Depends(get_current_user)):
    user_id, _ = _user_ids(current_user)
    return manager.status_snapshot(user_id)


async def _command(current_user, action: str, device_id: int | None = None):
    user_id, _ = _user_ids(current_user)
    if not await manager.send_command(user_id, action, device_id):
        raise HTTPException(status_code=404, detail='Agent is not connected')
    return {'ok': True}


@router.post('/start')
async def start_agent(current_user=Depends(get_current_user)):
    return await _command(current_user, 'start')


@router.post('/stop')
async def stop_agent(current_user=Depends(get_current_user)):
    return await _command(current_user, 'stop')


@router.websocket('/ws')
async def agent_websocket(ws: WebSocket):
    # Agent authenticates with its own long-lived token in the Authorization header.
    # Rejecting before accept() makes the handshake fail with HTTP 403.
    auth = ws.headers.get('authorization', '')
    claims = verify_agent_token(auth[7:]) if auth.lower().startswith('bearer ') else None
    if claims is None:
        await ws.close(code=CLOSE_UNAUTHORIZED, reason='invalid agent token')
        return

    conn = await manager.connect_agent(ws, user_id=claims['sub'], discord_id=claims['did'])
    bucket = TokenBucket(AGENT_RATE, AGENT_BURST)
    try:
        while True:
            msg = await _receive_json(ws)
            if not bucket.allow():
                await ws.close(code=1008, reason='rate limit exceeded')
                break
            if msg is not None:
                await manager.handle_agent_message(conn, msg)
    except _TextTooLarge as e:
        await manager._close_quietly(ws, 1009, str(e))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[agent] Connection error for user {conn.user_id}: {e}")
        await manager._close_quietly(ws, 1011, 'internal error')
    finally:
        manager.disconnect_agent(conn)


@router.websocket('/ws/frontend')
async def frontend_websocket(ws: WebSocket):
    # Browsers can't set headers on a WebSocket, so the JWT arrives as the first message.
    await ws.accept()
    try:
        first = await asyncio.wait_for(_receive_json(ws), AUTH_TIMEOUT)
    except (asyncio.TimeoutError, _TextTooLarge, WebSocketDisconnect):
        first = None
    claims = None
    if first and first.get('type') == 'auth' and isinstance(first.get('token'), str):
        claims = verify_user_jwt(first['token'])
    if claims is None:
        await manager._close_quietly(ws, CLOSE_UNAUTHORIZED, 'unauthorized')
        return

    user_id = claims['sub']
    manager.add_frontend(user_id, ws)
    bucket = TokenBucket(FRONTEND_RATE, FRONTEND_BURST)
    try:
        await ws.send_json(manager.status_snapshot(user_id))
        while True:
            msg = await _receive_json(ws)
            if not bucket.allow():
                await ws.close(code=1008, reason='rate limit exceeded')
                break
            if msg is None or msg.get('type') != 'command':
                continue
            params = msg.get('params') if isinstance(msg.get('params'), dict) else {}
            device_id = params.get('device_id')
            await manager.send_command(
                user_id,
                str(msg.get('action')),
                device_id if isinstance(device_id, int) and not isinstance(device_id, bool) else None,
            )
    except _TextTooLarge as e:
        await manager._close_quietly(ws, 1009, str(e))
    except WebSocketDisconnect:
        pass
    finally:
        manager.remove_frontend(user_id, ws)
