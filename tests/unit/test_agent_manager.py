"""AgentManager: call sessions, dedup of replayed messages, dashboard commands."""
import asyncio

import pytest

from services.web import agent_manager as am


@pytest.fixture
def summaries(manager, monkeypatch):
    """Capture sessions handed to summarization instead of calling the LLM."""
    queued = []
    monkeypatch.setattr(manager, '_enqueue_summary',
                        lambda session, user_id, discord_id: queued.append(session) if session.transcriptions else None)
    return queued


@pytest.fixture
async def conn(manager, fake_ws):
    ws = fake_ws()
    c = await manager.connect_agent(ws, user_id='u1', discord_id='d1')
    yield c
    manager._cancel_timer(c)


def transcription(text, **extra):
    return {'type': 'transcription', 'text': text, 'timestamp': '12:00:00', **extra}


class TestCallSession:
    async def test_first_transcription_opens_session(self, manager, conn):
        await manager.handle_agent_message(conn, transcription('привет'))
        assert conn.active_session is not None
        assert conn.active_session.texts() == ['привет']

    @pytest.mark.parametrize('text', ['', '   ', None, 123, ['список']])
    async def test_invalid_text_is_ignored(self, manager, conn, text):
        await manager.handle_agent_message(conn, transcription(text))
        assert conn.active_session is None

    async def test_long_transcription_is_truncated(self, manager, conn):
        await manager.handle_agent_message(conn, transcription('а' * (am.MAX_TRANSCRIPTION_CHARS + 1)))
        assert len(conn.active_session.texts()[0]) == am.MAX_TRANSCRIPTION_CHARS

    async def test_call_end_sends_session_to_summary(self, manager, conn, summaries):
        await manager.handle_agent_message(conn, transcription('раз'))
        await manager.handle_agent_message(conn, transcription('два'))
        await manager.handle_agent_message(conn, {'type': 'call_end'})
        assert conn.active_session is None
        assert [s.texts() for s in summaries] == [['раз', 'два']]

    async def test_call_start_closes_previous_session(self, manager, conn, summaries):
        await manager.handle_agent_message(conn, transcription('старый звонок'))
        await manager.handle_agent_message(conn, {'type': 'call_start'})
        assert len(summaries) == 1

    async def test_empty_session_is_not_summarized(self, manager, conn, summaries):
        await manager.handle_agent_message(conn, {'type': 'call_end'})
        assert summaries == []

    async def test_session_closes_at_transcription_limit(self, manager, conn, summaries, monkeypatch):
        monkeypatch.setattr(am, 'MAX_SESSION_TRANSCRIPTIONS', 3)
        for i in range(2):
            await manager.handle_agent_message(conn, transcription(f'фраза {i}'))
        assert summaries == []                       # limit - 1: still open
        await manager.handle_agent_message(conn, transcription('фраза 2'))
        assert len(summaries) == 1                   # limit reached: closed
        assert conn.active_session is None

    async def test_session_closes_at_char_limit(self, manager, conn, summaries, monkeypatch):
        monkeypatch.setattr(am, 'MAX_SESSION_CHARS', 10)
        await manager.handle_agent_message(conn, transcription('а' * 9))
        assert summaries == []
        await manager.handle_agent_message(conn, transcription('б'))
        assert len(summaries) == 1

    async def test_silence_timeout_closes_session(self, manager, conn, summaries, monkeypatch):
        monkeypatch.setattr(am, 'SESSION_TIMEOUT', 0.05)
        await manager.handle_agent_message(conn, transcription('и тишина'))
        await asyncio.sleep(0.1)
        assert len(summaries) == 1
        assert conn.active_session is None

    async def test_new_speech_resets_timeout(self, manager, conn, summaries, monkeypatch):
        monkeypatch.setattr(am, 'SESSION_TIMEOUT', 0.1)
        await manager.handle_agent_message(conn, transcription('раз'))
        await asyncio.sleep(0.06)
        await manager.handle_agent_message(conn, transcription('два'))
        await asyncio.sleep(0.06)                    # 0.12s since first, 0.06s since last
        assert summaries == []
        await asyncio.sleep(0.08)
        assert len(summaries) == 1


class TestReplayDedup:
    async def test_ack_is_sent_for_sequenced_message(self, manager, conn):
        await manager.handle_agent_message(conn, transcription('раз', stream_id='s', seq=1))
        assert {'type': 'ack', 'seq': 1} in conn.ws.sent

    async def test_replayed_message_is_acked_but_not_stored(self, manager, conn):
        for _ in range(2):
            await manager.handle_agent_message(conn, transcription('раз', stream_id='s', seq=1))
        assert conn.active_session.texts() == ['раз']
        assert conn.ws.sent.count({'type': 'ack', 'seq': 1}) == 2

    async def test_older_seq_is_dropped(self, manager, conn):
        await manager.handle_agent_message(conn, transcription('пятый', stream_id='s', seq=5))
        await manager.handle_agent_message(conn, transcription('третий', stream_id='s', seq=3))
        assert conn.active_session.texts() == ['пятый']

    async def test_new_stream_restarts_numbering(self, manager, conn):
        await manager.handle_agent_message(conn, transcription('a', stream_id='s1', seq=5))
        await manager.handle_agent_message(conn, transcription('b', stream_id='s2', seq=1))
        assert conn.active_session.texts() == ['a', 'b']

    @pytest.mark.parametrize('seq', [None, '1', True, 1.5])
    async def test_non_integer_seq_is_not_deduplicated_or_acked(self, manager, conn, seq):
        for _ in range(2):
            await manager.handle_agent_message(conn, transcription('раз', stream_id='s', seq=seq))
        assert conn.active_session.texts() == ['раз', 'раз']
        assert not any(m.get('type') == 'ack' for m in conn.ws.sent)


class TestReconnect:
    async def test_newer_connection_inherits_session(self, manager, conn, fake_ws):
        await manager.handle_agent_message(conn, transcription('до переподключения'))
        new = await manager.connect_agent(fake_ws(), user_id='u1', discord_id='d1')
        await asyncio.sleep(0)
        assert new.active_session.texts() == ['до переподключения']
        assert conn.ws.closed[0] == am.CLOSE_REPLACED
        manager._cancel_timer(new)

    async def test_session_parked_on_disconnect_is_resumed(self, manager, conn, fake_ws):
        await manager.handle_agent_message(conn, transcription('звонок'))
        manager.disconnect_agent(conn)
        assert manager.get_agent('u1') is None
        new = await manager.connect_agent(fake_ws(), user_id='u1', discord_id='d1')
        assert new.active_session.texts() == ['звонок']
        manager._cancel_timer(new)

    async def test_parked_session_is_summarized_after_grace(self, manager, conn, summaries, monkeypatch):
        monkeypatch.setattr(am, 'RECONNECT_GRACE', 0.05)
        await manager.handle_agent_message(conn, transcription('звонок'))
        manager.disconnect_agent(conn)
        await asyncio.sleep(0.1)
        assert len(summaries) == 1


class TestStatusAndDevices:
    async def test_register_updates_status_and_devices(self, manager, conn):
        await manager.handle_agent_message(conn, {
            'type': 'register', 'status': 'listening', 'device_id': 2,
            'devices': [{'id': 1, 'name': 'Mic'}, {'id': 2, 'name': 'Speakers'}],
        })
        snap = manager.status_snapshot('u1')
        assert snap['connected'] is True
        assert snap['status'] == 'listening'
        assert snap['device_id'] == 2
        assert [d['id'] for d in snap['devices']] == [1, 2]

    async def test_unknown_status_is_ignored(self, manager, conn):
        await manager.handle_agent_message(conn, {'type': 'status', 'status': 'hacked'})
        assert manager.status_snapshot('u1')['status'] == 'idle'

    def test_snapshot_for_disconnected_user(self, manager):
        assert manager.status_snapshot('nobody') == {
            'type': 'status', 'connected': False, 'status': 'idle',
            'device_id': None, 'devices': [], 'user_id': None,
        }

    @pytest.mark.parametrize('devices, expected_ids', [
        ('not a list', []),
        ([{'id': 1}, {'id': '2'}, {'id': True}, {'name': 'no id'}, 'junk'], [1]),
    ])
    def test_clean_devices_drops_invalid_entries(self, devices, expected_ids):
        assert [d['id'] for d in am._clean_devices(devices)] == expected_ids

    def test_clean_devices_limits_count_and_name_length(self):
        devices = [{'id': i, 'name': 'x' * 500} for i in range(am.MAX_DEVICES + 5)]
        cleaned = am._clean_devices(devices)
        assert len(cleaned) == am.MAX_DEVICES
        assert len(cleaned[0]['name']) == 200


class TestCommands:
    async def test_start_forwards_current_device(self, manager, conn):
        conn.device_id = 7
        assert await manager.send_command('u1', 'start')
        assert conn.ws.sent[-1] == {'type': 'command', 'action': 'start', 'device_id': 7}

    @pytest.mark.parametrize('action', ['shutdown', 'exec', ''])
    async def test_unknown_command_is_rejected(self, manager, conn, action):
        before = len(conn.ws.sent)
        assert not await manager.send_command('u1', action)
        assert len(conn.ws.sent) == before

    async def test_select_device_requires_id(self, manager, conn):
        assert not await manager.send_command('u1', 'select_device')
        assert await manager.send_command('u1', 'select_device', 3)

    async def test_command_without_agent(self, manager):
        assert not await manager.send_command('nobody', 'start')

    async def test_dashboard_gets_only_own_agent_events(self, manager, conn, fake_ws):
        mine, foreign = fake_ws(), fake_ws()
        manager.add_frontend('u1', mine)
        manager.add_frontend('u2', foreign)
        await manager.handle_agent_message(conn, transcription('секрет'))
        assert any(m.get('text') == 'секрет' for m in mine.sent)
        assert foreign.sent == []
