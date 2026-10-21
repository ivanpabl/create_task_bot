"""WebSocket endpoints for the desktop agent and the dashboard."""
import json

import pytest
from starlette.websockets import WebSocketDisconnect

from services.web.agent_manager import CLOSE_UNAUTHORIZED, manager
from services.web.auth import create_jwt
from services.web.routers import agent as agent_router


@pytest.fixture(autouse=True)
def clean_manager(monkeypatch):
    manager.agents.clear()
    manager.frontends.clear()
    manager._parked.clear()
    manager._last_seq.clear()
    # Sessions must not reach the LLM from these tests.
    monkeypatch.setattr(manager, '_enqueue_summary', lambda *a: None)
    yield
    manager.agents.clear()
    manager.frontends.clear()


def agent_ws(client, token):
    return client.websocket_connect('/api/agent/ws', headers={'Authorization': f'Bearer {token}'})


class TestAgentSocket:
    @pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer junk'}, {'Authorization': 'Token abc'}])
    def test_rejects_without_valid_agent_token(self, client, headers):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect('/api/agent/ws', headers=headers):
                pass
        assert exc.value.code == CLOSE_UNAUTHORIZED

    def test_rejects_user_token(self, client, user):
        with pytest.raises(WebSocketDisconnect):
            with agent_ws(client, create_jwt(user['id'])):
                pass

    def test_register_is_visible_in_status_api(self, client, user, agent_token, auth_headers):
        with agent_ws(client, agent_token) as ws:
            ws.send_json({'type': 'register', 'status': 'listening', 'device_id': 1,
                          'devices': [{'id': 1, 'name': 'Mic'}], 'seq': 1, 'stream_id': 's'})
            assert ws.receive_json() == {'type': 'ack', 'seq': 1}

            status = client.get('/api/agent/status', headers=auth_headers).json()
            assert status['connected'] is True
            assert status['status'] == 'listening'
            assert status['devices'] == [{'id': 1, 'name': 'Mic'}]

        status = client.get('/api/agent/status', headers=auth_headers).json()
        assert status['connected'] is False

    def test_start_command_reaches_agent(self, client, agent_token, auth_headers):
        with agent_ws(client, agent_token) as ws:
            ws.send_json({'type': 'register', 'device_id': 4, 'seq': 1, 'stream_id': 's'})
            ws.receive_json()
            assert client.post('/api/agent/start', headers=auth_headers).json() == {'ok': True}
            assert ws.receive_json() == {'type': 'command', 'action': 'start', 'device_id': 4}

    def test_start_without_agent_is_404(self, client, auth_headers):
        resp = client.post('/api/agent/start', headers=auth_headers)
        assert resp.status_code == 404

    def test_invalid_json_is_skipped(self, client, agent_token):
        with agent_ws(client, agent_token) as ws:
            ws.send_text('{not json')
            ws.send_text('[1, 2, 3]')
            ws.send_json({'type': 'status', 'seq': 7, 'stream_id': 's'})
            assert ws.receive_json() == {'type': 'ack', 'seq': 7}

    def test_oversized_frame_closes_with_1009(self, client, agent_token):
        with agent_ws(client, agent_token) as ws:
            ws.send_text(json.dumps({'type': 'transcription', 'text': 'а' * agent_router.MAX_WS_MESSAGE_BYTES}))
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
        assert exc.value.code == 1009

    def test_rate_limit_closes_with_1008(self, client, agent_token, monkeypatch):
        monkeypatch.setattr(agent_router, 'AGENT_BURST', 3)
        monkeypatch.setattr(agent_router, 'AGENT_RATE', 0.0001)
        with agent_ws(client, agent_token) as ws:
            for _ in range(4):
                ws.send_json({'type': 'status'})
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
        assert exc.value.code == 1008


class TestDashboardSocket:
    def test_auth_message_returns_status(self, client, user):
        with client.websocket_connect('/api/agent/ws/frontend') as ws:
            ws.send_json({'type': 'auth', 'token': create_jwt(user['id'])})
            assert ws.receive_json()['type'] == 'status'

    @pytest.mark.parametrize('first', [
        {'type': 'hello'},
        {'type': 'auth'},
        {'type': 'auth', 'token': 'junk'},
        {'type': 'auth', 'token': 123},
    ])
    def test_bad_auth_closes_4401(self, client, first):
        with client.websocket_connect('/api/agent/ws/frontend') as ws:
            ws.send_json(first)
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
        assert exc.value.code == CLOSE_UNAUTHORIZED

    def test_agent_token_cannot_open_dashboard(self, client, agent_token):
        with client.websocket_connect('/api/agent/ws/frontend') as ws:
            ws.send_json({'type': 'auth', 'token': agent_token})
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()

    def test_transcription_streams_to_dashboard(self, client, user, agent_token):
        with client.websocket_connect('/api/agent/ws/frontend') as dash:
            dash.send_json({'type': 'auth', 'token': create_jwt(user['id'])})
            dash.receive_json()                                  # initial status
            with agent_ws(client, agent_token) as agent:
                assert dash.receive_json()['connected'] is True
                agent.send_json({'type': 'transcription', 'text': 'нужно сделать деплой', 'seq': 1, 'stream_id': 's'})
                agent.receive_json()                             # ack
                event = dash.receive_json()
                assert event['type'] == 'transcription'
                assert event['text'] == 'нужно сделать деплой'

    def test_dashboard_command_reaches_agent(self, client, user, agent_token):
        with agent_ws(client, agent_token) as agent:
            with client.websocket_connect('/api/agent/ws/frontend') as dash:
                dash.send_json({'type': 'auth', 'token': create_jwt(user['id'])})
                dash.receive_json()
                dash.send_json({'type': 'command', 'action': 'select_device', 'params': {'device_id': 2}})
                assert agent.receive_json() == {'type': 'command', 'action': 'select_device', 'device_id': 2}
