from authlib.integrations.starlette_client import OAuth
from starlette.config import Config
from datetime import datetime, timedelta
from jose import jwt, JWTError
from uuid import UUID

from services.web.config import settings

starlette_config = Config(environ={
    'DISCORD_CLIENT_ID': settings.discord_client_id,
    'DISCORD_CLIENT_SECRET': settings.discord_client_secret,
})

oauth = OAuth(starlette_config)
oauth.register(
    name='discord',
    authorize_url='https://discord.com/api/oauth2/authorize',
    authorize_params=None,
    access_token_url='https://discord.com/api/oauth2/token',
    access_token_params=None,
    client_kwargs={'scope': 'identify messages.read'},
)


def create_jwt(user_id: UUID) -> str:
    payload = {
        'sub': str(user_id),
        'exp': datetime.utcnow() + timedelta(days=7),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm='HS256')


def verify_jwt(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=['HS256'])
        return payload
    except JWTError:
        return None
