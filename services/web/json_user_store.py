import json
import uuid
from pathlib import Path
from datetime import datetime

STORE_PATH = Path(__file__).resolve().parents[2] / 'users_store.json'


def _load() -> list[dict]:
    if not STORE_PATH.exists():
        return []
    try:
        with open(STORE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save(users: list[dict]):
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STORE_PATH, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def find_or_create_user(discord_id: str, username: str, access_token: str | None = None, refresh_token: str | None = None) -> dict:
    users = _load()
    for u in users:
        if u['discord_id'] == discord_id:
            if username:
                u['username'] = username
            if access_token:
                u['access_token'] = access_token
            if refresh_token:
                u['refresh_token'] = refresh_token
            _save(users)
            return u

    new_user = {
        'id': str(uuid.uuid4()),
        'discord_id': discord_id,
        'username': username,
        'access_token': access_token,
        'refresh_token': refresh_token,
        'token_expires_at': None,
        'created_at': datetime.utcnow().isoformat(),
    }
    users.append(new_user)
    _save(users)
    return new_user


def get_user_by_id(user_id: str) -> dict | None:
    users = _load()
    for u in users:
        if u['id'] == user_id:
            return u
    return None


def get_user_by_discord_id(discord_id: str) -> dict | None:
    users = _load()
    for u in users:
        if u['discord_id'] == discord_id:
            return u
    return None
