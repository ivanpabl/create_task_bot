import os

# Settings are read at import time, so the test environment must be in place
# before any `services.*` module is imported. Env vars take priority over .env.
os.environ['POSTGRES_DSN'] = ''
os.environ['ALERT_WEBHOOK_URL'] = ''
os.environ['JWT_SECRET'] = 'test-jwt-secret'
os.environ['BACKEND_API_KEY'] = 'test-api-key'
os.environ['BACKEND_URL'] = 'https://api.example.test'

import pytest
from fastapi.testclient import TestClient

from services import json_store
from services.web import json_user_store
from services.web.auth import create_agent_token, create_jwt
from services.web.agent_manager import AgentManager
from services.web.routers import pairing

API_KEY = 'test-api-key'


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, monkeypatch):
    """JSON stores and in-memory state must never leak between tests or touch real files."""
    monkeypatch.setattr(json_user_store, 'STORE_PATH', tmp_path / 'users_store.json')
    monkeypatch.setattr(json_store, 'STORE_PATH', tmp_path / 'tasks_store.json')
    pairing._pairing_codes.clear()
    pairing._claim_failures._events.clear()
    # Avoid spawning the background cleanup loop inside TestClient's short-lived event loop.
    monkeypatch.setattr(pairing, '_cleanup_started', True)
    yield
    pairing._pairing_codes.clear()


@pytest.fixture
def client():
    # Not used as a context manager: lifespan (poller, DB init, alerts) is not started.
    from services.web.main import app
    return TestClient(app)


@pytest.fixture
def user():
    return json_user_store.find_or_create_user('111222333', 'tester')


@pytest.fixture
def other_user():
    return json_user_store.find_or_create_user('999888777', 'stranger')


@pytest.fixture
def auth_headers(user):
    return {'Authorization': f'Bearer {create_jwt(user["id"])}'}


@pytest.fixture
def agent_token(user):
    return create_agent_token(user['id'], user['discord_id'])


@pytest.fixture
def manager():
    return AgentManager()


class FakeWebSocket:
    """Records what the server sends; enough of the WebSocket API for AgentManager."""

    def __init__(self):
        self.sent: list[dict] = []
        self.accepted = False
        self.closed: tuple[int, str] | None = None

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=1000, reason=''):
        self.closed = (code, reason)


@pytest.fixture
def fake_ws():
    return FakeWebSocket
