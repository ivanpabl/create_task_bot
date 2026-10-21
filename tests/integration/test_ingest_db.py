"""Internal ingestion endpoints (bot -> API): API-key guard and what lands in PostgreSQL."""
import pytest
from sqlalchemy import func, select

from services.database import Message, Task, Transcription
from tests.conftest import API_KEY

pytestmark = pytest.mark.postgres

KEY = {'Authorization': f'Bearer {API_KEY}'}


def message_payload(**over):
    return {'message_id': 'm1', 'content': 'Нужно выкатить релиз', 'author': 'ivan',
            'author_id': '1', 'channel': 'general', 'timestamp': '2026-01-01T10:00:00', **over}


def transcription_payload(**over):
    return {'guild_id': 'g', 'channel_id': 'c', 'channel_name': 'voice', 'user_id': '1',
            'user_name': 'ivan', 'content': 'Срочно нужно починить оплату', **over}


async def count(factory, model):
    async with factory() as s:
        return await s.scalar(select(func.count()).select_from(model))


class TestMessages:
    async def test_message_is_stored_as_sent(self, api, db_factory):
        resp = await api.post('/api/messages', json=message_payload(), headers=KEY)

        assert resp.status_code == 200
        async with db_factory() as s:
            stored = (await s.execute(select(Message))).scalar_one()
        assert str(stored.id) == resp.json()['id']
        assert (stored.message_id, stored.content, stored.author, stored.channel, stored.source) == \
               ('m1', 'Нужно выкатить релиз', 'ivan', 'general', 'bot_server')
        assert stored.created_at is not None

    @pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer wrong'}])
    async def test_without_api_key_nothing_is_written(self, api, db_factory, headers):
        resp = await api.post('/api/messages', json=message_payload(), headers=headers)
        assert resp.status_code == 403
        assert await count(db_factory, Message) == 0

    @pytest.mark.parametrize('missing', ['message_id', 'content', 'author', 'channel'])
    async def test_required_fields(self, api, db_factory, missing):
        payload = message_payload()
        del payload[missing]
        assert (await api.post('/api/messages', json=payload, headers=KEY)).status_code == 422
        assert await count(db_factory, Message) == 0

    async def test_unicode_and_long_content_roundtrip(self, api, db_factory):
        content = 'Задача 🚀 ' + 'ё' * 10_000
        await api.post('/api/messages', json=message_payload(content=content), headers=KEY)
        async with db_factory() as s:
            assert await s.scalar(select(Message.content)) == content


class TestTranscriptions:
    async def test_stored_without_user_creates_no_tasks(self, api, db_factory):
        resp = await api.post('/api/transcriptions', json=transcription_payload(), headers=KEY)
        assert resp.json()['tasks_created'] == 0
        assert await count(db_factory, Transcription) == 1
        assert await count(db_factory, Task) == 0

    async def test_tasks_are_extracted_for_known_user(self, api, db_factory, make_user):
        user = await make_user()
        resp = await api.post('/api/transcriptions', headers=KEY,
                              json=transcription_payload(internal_user_id=str(user.id)))

        assert resp.json()['tasks_created'] == 1
        async with db_factory() as s:
            task = (await s.execute(select(Task))).scalar_one()
        assert task.user_id == user.id
        assert task.urgency == 'high'
        assert task.source == 'desktop_loopback'
        assert task.channel == 'voice'
        assert task.dm_sent == 'pending'
        assert 'нужно' in task.keywords

    async def test_small_talk_creates_no_tasks(self, api, db_factory, make_user):
        user = await make_user()
        resp = await api.post('/api/transcriptions', headers=KEY,
                              json=transcription_payload(content='Привет, как дела', internal_user_id=str(user.id)))
        assert resp.json()['tasks_created'] == 0
        assert await count(db_factory, Transcription) == 1

    async def test_without_api_key_nothing_is_written(self, api, db_factory):
        resp = await api.post('/api/transcriptions', json=transcription_payload())
        assert resp.status_code == 403
        assert await count(db_factory, Transcription) == 0
