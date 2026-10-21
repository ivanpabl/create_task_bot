from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from uuid import UUID

from services.web.database import get_session_factory
from services.web.models import User
from services.web.auth import verify_user_jwt
from services.web.config import settings
from services.web.json_user_store import get_user_by_id as json_get_user

bearer_scheme = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> User | dict:
    payload = verify_user_jwt(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid token')

    user_id = payload.get('sub')
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid token payload')

    # Try DB first
    factory = get_session_factory()
    if factory is not None:
        try:
            async with factory() as session:
                result = await session.execute(select(User).where(User.id == UUID(user_id)))
                user = result.scalar_one_or_none()
                if user:
                    return user
        except Exception:
            pass

    # JSON fallback
    json_user = json_get_user(user_id)
    if json_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')

    return json_user


async def verify_api_key(authorization: str | None = None) -> bool:
    if authorization and authorization == f'Bearer {settings.backend_api_key}':
        return True
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Invalid API key')
