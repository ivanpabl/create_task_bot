import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Query, Depends
from fastapi.responses import FileResponse, PlainTextResponse, Response

from services.web.agent_manager import manager
from services.web.database import get_db
from services.web.dependencies import get_current_user
from services.web.config import settings

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


@router.get('/config')
async def download_config(current_user=Depends(get_current_user)):
    discord_id = current_user.discord_id if hasattr(current_user, 'discord_id') else current_user.get('discord_id')
    base_url = settings.backend_url.rstrip('/')

    config = f"""# Discord Task Bot — Desktop Agent Configuration
discord_id: "{discord_id}"
backend_url: "{base_url}"
whisper_model: medium
whisper_device: cpu
whisper_compute: int8
"""
    return PlainTextResponse(
        config,
        media_type='text/plain',
        headers={'Content-Disposition': 'attachment; filename="config.yml"'},
    )


@router.get('/download')
async def download_agent(current_user=Depends(get_current_user)):
    base = _load_base_zip()

    discord_id = current_user.discord_id if hasattr(current_user, 'discord_id') else current_user.get('discord_id')
    base_url = settings.backend_url.rstrip('/')

    config_yml = f"""# Discord Task Bot — Desktop Agent Configuration
discord_id: "{discord_id}"
backend_url: "{base_url}"
whisper_model: medium
whisper_device: cpu
whisper_compute: int8
"""

    buf = io.BytesIO(base)
    with zipfile.ZipFile(buf, 'a', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('config.yml', config_yml)

    return Response(
        content=buf.getvalue(),
        media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename="discord-task-agent.zip"'},
    )


@router.get('/status')
async def get_status():
    agents_list = [
        {
            'discord_id': d,
            'status': c.status,
            'device_id': c.device_id,
            'devices': c.devices,
            'user_id': c.user_id,
        }
        for d, c in manager.agents.items()
    ]
    return {'agents': agents_list, 'connected': len(agents_list) > 0}


@router.post('/start')
async def start_agent(discord_id: str = Query(...)):
    conn = manager._get(discord_id)
    if not conn:
        raise HTTPException(status_code=404, detail='Agent not found for this discord_id')
    await conn.ws.send_json({'type': 'command', 'action': 'start', 'device_id': conn.device_id})
    return {'ok': True}


@router.post('/stop')
async def stop_agent(discord_id: str = Query(...)):
    conn = manager._get(discord_id)
    if not conn:
        raise HTTPException(status_code=404, detail='Agent not found for this discord_id')
    await conn.ws.send_json({'type': 'command', 'action': 'stop'})
    return {'ok': True}


@router.websocket('/ws')
async def agent_websocket(ws: WebSocket, discord_id: str | None = None):
    if not discord_id:
        await ws.close(code=4001, reason='discord_id required')
        return

    await manager.connect_agent(ws, discord_id)
    db = None
    try:
        db_gen = get_db()
        db = await db_gen.__anext__()
    except Exception:
        print("[agent] No database available, tasks won't be saved")
        db = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                msg['discord_id'] = discord_id
                await manager.handle_agent_message(msg, db)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect_agent(discord_id)
        if db:
            try:
                await db.__aexit__(None, None, None)
            except Exception:
                pass


@router.websocket('/ws/frontend')
async def frontend_websocket(ws: WebSocket):
    await manager.connect_frontend(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                if msg.get('type') == 'command':
                    discord_id = msg.get('discord_id')
                    if discord_id:
                        conn = manager._get(discord_id)
                        if conn:
                            await conn.ws.send_json({'type': 'command', 'action': msg.get('action'), **msg.get('params', {})})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect_frontend(ws)
