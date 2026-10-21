import secrets
import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from services.web.auth import create_agent_token
from services.web.dependencies import get_current_user
from services.web.ratelimit import SlidingWindowLimiter

router = APIRouter(prefix='/api/pairing', tags=['pairing'])

# No 0/O/1/I/L — the code is typed by hand. 31^8 ≈ 8.5e11 combinations.
CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
CODE_LENGTH = 8
CODE_TTL = timedelta(minutes=10)

# Failed claims per client IP. Requires uvicorn --proxy-headers with a correct
# FORWARDED_ALLOW_IPS behind a reverse proxy, otherwise all clients share one key.
_claim_failures = SlidingWindowLimiter(limit=10, window=600)

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


def _normalize_code(code: str) -> str:
    return ''.join(ch for ch in code.upper() if ch.isalnum())


@router.get('/code')
async def get_pairing_code(current_user=Depends(get_current_user)):
    _ensure_cleanup()

    discord_id = current_user.discord_id if hasattr(current_user, 'discord_id') else current_user.get('discord_id')
    user_id = str(current_user.id) if hasattr(current_user, 'id') else current_user.get('id')

    # Clean old codes for this user
    for k, v in list(_pairing_codes.items()):
        if v['user_id'] == user_id:
            _pairing_codes.pop(k, None)

    code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    _pairing_codes[code] = {
        'discord_id': discord_id,
        'user_id': user_id,
        'expires_at': datetime.utcnow() + CODE_TTL,
    }
    return {'code': code, 'expires_in': int(CODE_TTL.total_seconds())}


class ClaimRequest(BaseModel):
    code: str = Field(..., max_length=32)


# POST with the code in the body: keeps it out of access logs and caches.
@router.post('/claim')
async def claim_pairing_code(body: ClaimRequest, request: Request):
    client_ip = request.client.host if request.client else 'unknown'
    if _claim_failures.is_limited(client_ip):
        raise HTTPException(status_code=429, detail='Too many attempts, try again later')

    entry = _pairing_codes.pop(_normalize_code(body.code), None)
    if entry is None or entry['expires_at'] < datetime.utcnow():
        _claim_failures.hit(client_ip)
        raise HTTPException(status_code=404, detail='Invalid or expired pairing code')

    return {
        'discord_id': entry['discord_id'],
        'agent_token': create_agent_token(entry['user_id'], entry['discord_id']),
    }
