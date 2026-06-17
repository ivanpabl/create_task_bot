from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from authlib.integrations.base_client.errors import MismatchingStateError
import httpx

from services.web.database import get_db, get_session_factory
from services.web.models import User
from services.web.schemas import UserOut
from services.web.auth import oauth, create_jwt
from services.web.config import settings
from services.web.dependencies import get_current_user
from services.web.json_user_store import find_or_create_user as json_find_or_create, get_user_by_id as json_get_user

router = APIRouter(prefix='/auth', tags=['auth'])


@router.get('/discord')
async def discord_login(request: Request):
    return await oauth.discord.authorize_redirect(request, settings.discord_redirect_uri)


@router.get('/callback')
async def auth_callback(request: Request):
    try:
        token = await oauth.discord.authorize_access_token(request)
    except MismatchingStateError:
        raise HTTPException(
            status_code=400,
            detail='Session expired or invalid state. Попробуй войти заново.'
        )
    if token is None:
        raise HTTPException(status_code=400, detail='OAuth failed')

    access_token = token.get('access_token')
    refresh_token = token.get('refresh_token')

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            'https://discord.com/api/users/@me',
            headers={'Authorization': f'Bearer {access_token}'},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail='Failed to fetch Discord user')
        discord_user = resp.json()

    discord_id = discord_user['id']
    username = discord_user.get('global_name') or discord_user['username']

    # Try DB first, fall back to JSON
    factory = get_session_factory()
    db_available = factory is not None
    if db_available:
        try:
            async with factory() as session:
                result = await session.execute(select(User).where(User.discord_id == discord_id))
                user = result.scalar_one_or_none()

                if user is None:
                    user = User(
                        discord_id=discord_id,
                        username=username,
                        access_token=access_token,
                        refresh_token=refresh_token,
                        token_expires_at=None,
                    )
                    session.add(user)
                else:
                    user.username = username
                    user.access_token = access_token
                    user.refresh_token = refresh_token

                await session.commit()
                await session.refresh(user)

                jwt_token = create_jwt(user.id)
                return HTMLResponse(f'''<!DOCTYPE html><html><body>
<script>localStorage.setItem('jwt_token','{jwt_token}');window.location.href='/';</script>
</body></html>''')
        except Exception:
            db_available = False

    # JSON fallback
    print("[auth] PostgreSQL unavailable, using JSON user store")
    json_user = json_find_or_create(discord_id, username, access_token, refresh_token)
    jwt_token = create_jwt(json_user['id'])
    return HTMLResponse(f'''<!DOCTYPE html><html><body>
<script>localStorage.setItem('jwt_token','{jwt_token}');window.location.href='/';</script>
</body></html>''')


@router.get('/me', response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user
