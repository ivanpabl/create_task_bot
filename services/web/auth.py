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


TOKEN_TYPE_USER = 'user'
TOKEN_TYPE_AGENT = 'agent'
AGENT_TOKEN_TTL = timedelta(days=365)


def create_jwt(user_id: UUID) -> str:
    payload = {
        'sub': str(user_id),
        'typ': TOKEN_TYPE_USER,
        'exp': datetime.utcnow() + timedelta(days=7),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm='HS256')


def create_agent_token(user_id: UUID | str, discord_id: str) -> str:
    """Long-lived credential for the desktop agent. Accepted only by the agent WebSocket."""
    payload = {
        'sub': str(user_id),
        'did': str(discord_id),
        'typ': TOKEN_TYPE_AGENT,
        'exp': datetime.utcnow() + AGENT_TOKEN_TTL,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm='HS256')


def verify_jwt(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=['HS256'])
        return payload
    except JWTError:
        return None


def verify_user_jwt(token: str) -> dict | None:
    """Browser session token. Tokens issued before 'typ' existed are treated as user tokens."""
    payload = verify_jwt(token)
    if payload is None or not payload.get('sub'):
        return None
    if payload.get('typ', TOKEN_TYPE_USER) != TOKEN_TYPE_USER:
        return None
    return payload


def verify_agent_token(token: str) -> dict | None:
    payload = verify_jwt(token)
    if payload is None or payload.get('typ') != TOKEN_TYPE_AGENT:
        return None
    if not payload.get('sub') or not payload.get('did'):
        return None
    return payload
