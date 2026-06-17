import secrets
import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from services.web.dependencies import get_current_user

router = APIRouter(prefix='/api/pairing', tags=['pairing'])

# in-memory store: code -> {discord_id, user_id, expires_at}
_pairing_codes: dict[str, dict] = {}

# Background cleanup task
async def _cleanup_expired():
    while True:
        now = datetime.utcnow()
        expired = [k for k, v in _pairing_codes.items() if v['expires_at'] < now]
        for k in expired:
            _pairing_codes.pop(k, None)
        await asyncio.sleep(60)

_cleanup_started = False


def _ensure_cleanup():
    global _cleanup_started
    if not _cleanup_started:
        _cleanup_started = True
        asyncio.create_task(_cleanup_expired())


@router.get('/code')
async def get_pairing_code(current_user=Depends(get_current_user)):
    _ensure_cleanup()

    discord_id = current_user.discord_id if hasattr(current_user, 'discord_id') else current_user.get('discord_id')
    user_id = str(current_user.id) if hasattr(current_user, 'id') else current_user.get('id')
    backend_url = getattr(current_user, 'backend_url', '')

    # Clean old codes for this user
    for k, v in list(_pairing_codes.items()):
        if v['user_id'] == user_id:
            _pairing_codes.pop(k, None)

    code = secrets.token_hex(3).upper()[:6]
    _pairing_codes[code] = {
        'discord_id': discord_id,
        'user_id': user_id,
        'expires_at': datetime.utcnow() + timedelta(minutes=10),
    }
    return {'code': code, 'expires_in': 600}


@router.get('/claim/{code}')
async def claim_pairing_code(code: str):
    code = code.upper().strip()
    entry = _pairing_codes.pop(code, None)
    if entry is None:
        raise HTTPException(status_code=404, detail='Invalid or expired pairing code')
    if entry['expires_at'] < datetime.utcnow():
        raise HTTPException(status_code=410, detail='Pairing code expired')
    return {
        'discord_id': entry['discord_id'],
        'user_id': entry['user_id'],
    }
