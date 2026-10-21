"""HTTP auth guards across the API."""
import pytest

from services.web.dependencies import verify_api_key
from tests.conftest import API_KEY

USER_ENDPOINTS = [
    ('get', '/auth/me'),
    ('get', '/api/agent/status'),
    ('get', '/api/agent/config'),
    ('post', '/api/agent/start'),
    ('get', '/api/pairing/code'),
]


def test_health(client):
    resp = client.get('/health')
    assert resp.status_code == 200
    assert resp.json() == {'status': 'ok'}


def test_me_returns_current_user(client, user, auth_headers):
    resp = client.get('/auth/me', headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {'id': user['id'], 'discord_id': '111222333', 'username': 'tester'}


def test_me_does_not_leak_oauth_tokens(client, auth_headers):
    body = client.get('/auth/me', headers=auth_headers).json()
    assert 'access_token' not in body and 'refresh_token' not in body


@pytest.mark.parametrize('method, path', USER_ENDPOINTS)
class TestUserEndpointsRequireAuth:
    def test_no_header(self, client, method, path):
        assert getattr(client, method)(path).status_code == 401

    def test_garbage_token(self, client, method, path):
        resp = getattr(client, method)(path, headers={'Authorization': 'Bearer not-a-jwt'})
        assert resp.status_code == 401

    def test_agent_token_is_not_accepted(self, client, agent_token, method, path):
        resp = getattr(client, method)(path, headers={'Authorization': f'Bearer {agent_token}'})
        assert resp.status_code == 401


def test_token_for_deleted_user(client):
    from uuid import uuid4
    from services.web.auth import create_jwt
    resp = client.get('/auth/me', headers={'Authorization': f'Bearer {create_jwt(uuid4())}'})
    assert resp.status_code == 404


class TestApiKey:
    async def test_valid_key(self):
        assert await verify_api_key(f'Bearer {API_KEY}') is True

    @pytest.mark.parametrize('header', [None, '', API_KEY, f'Basic {API_KEY}', 'Bearer wrong', f'bearer {API_KEY}'])
    async def test_invalid_key(self, header):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await verify_api_key(header)
        assert exc.value.status_code == 403
