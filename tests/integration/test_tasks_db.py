"""Tasks API against a real PostgreSQL: filters, isolation between users, persisted updates."""
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from services.database import Task
from tests.integration.conftest import bearer

pytestmark = pytest.mark.postgres


@pytest.fixture
def add_tasks(db_factory):
    async def add(user, *specs):
        async with db_factory() as s:
            tasks = [Task(id=uuid4(), user_id=user.id, content=spec.get('content', 'задача'),
                          urgency=spec.get('urgency', 'low'), status=spec.get('status', 'open'),
                          source=spec.get('source', 'bot_server')) for spec in specs]
            s.add_all(tasks)
            await s.commit()
            return tasks
    return add


class TestListTasks:
    async def test_returns_only_own_tasks(self, api, make_user, add_tasks):
        me, other = await make_user(), await make_user()
        await add_tasks(me, {'content': 'моя'})
        await add_tasks(other, {'content': 'чужая'}, {'content': 'тоже чужая'})

        resp = await api.get('/api/tasks', headers=bearer(me))

        assert resp.status_code == 200
        assert [t['content'] for t in resp.json()] == ['моя']

    async def test_empty_list(self, api, make_user):
        resp = await api.get('/api/tasks', headers=bearer(await make_user()))
        assert resp.json() == []

    @pytest.mark.parametrize('params, expected', [
        ({'status': 'done'}, {'b'}),
        ({'urgency': 'high'}, {'a'}),
        ({'source': 'desktop_loopback'}, {'c'}),
        ({'status': 'open', 'urgency': 'low'}, {'c'}),
        ({'status': 'nonexistent'}, set()),
    ])
    async def test_filters(self, api, make_user, add_tasks, params, expected):
        me = await make_user()
        await add_tasks(me,
                        {'content': 'a', 'urgency': 'high'},
                        {'content': 'b', 'status': 'done'},
                        {'content': 'c', 'source': 'desktop_loopback'})
        resp = await api.get('/api/tasks', params=params, headers=bearer(me))
        assert {t['content'] for t in resp.json()} == expected

    # Regression: ORDER BY urgency DESC sorted the strings alphabetically (medium, low, high).
    async def test_most_urgent_first(self, api, make_user, add_tasks):
        me = await make_user()
        await add_tasks(me, {'content': 'l', 'urgency': 'low'},
                        {'content': 'h', 'urgency': 'high'}, {'content': 'm', 'urgency': 'medium'})
        await add_tasks(me, {'content': 'h2', 'urgency': 'high'})
        resp = await api.get('/api/tasks', headers=bearer(me))
        assert [t['urgency'] for t in resp.json()] == ['high', 'high', 'medium', 'low']

    async def test_response_schema(self, api, make_user, add_tasks):
        me = await make_user()
        await add_tasks(me, {'content': 'x'})
        task = (await api.get('/api/tasks', headers=bearer(me))).json()[0]
        assert {'id', 'content', 'urgency', 'status', 'source', 'user_id'} <= task.keys()
        assert task['user_id'] == str(me.id)
        assert 'dm_sent' not in task


class TestUpdateTask:
    async def test_status_change_is_persisted(self, api, db_factory, make_user, add_tasks):
        me = await make_user()
        [task] = await add_tasks(me, {})

        resp = await api.patch(f'/api/tasks/{task.id}', json={'status': 'done'}, headers=bearer(me))

        assert resp.status_code == 200
        assert resp.json()['status'] == 'done'
        async with db_factory() as s:
            row = await s.scalar(select(Task.status).where(Task.id == task.id))
        assert row == 'done'

    async def test_partial_update_keeps_other_fields(self, api, db_factory, make_user, add_tasks):
        me = await make_user()
        [task] = await add_tasks(me, {'status': 'in_progress'})
        await api.patch(f'/api/tasks/{task.id}', json={'assignee': 'Пётр'}, headers=bearer(me))
        async with db_factory() as s:
            stored = await s.get(Task, task.id)
        assert (stored.assignee, stored.status) == ('Пётр', 'in_progress')

    async def test_cannot_update_foreign_task(self, api, db_factory, make_user, add_tasks):
        me, other = await make_user(), await make_user()
        [foreign] = await add_tasks(other, {})

        resp = await api.patch(f'/api/tasks/{foreign.id}', json={'status': 'done'}, headers=bearer(me))

        assert resp.status_code == 404
        async with db_factory() as s:
            assert await s.scalar(select(Task.status).where(Task.id == foreign.id)) == 'open'

    async def test_unknown_task(self, api, make_user):
        resp = await api.patch(f'/api/tasks/{uuid4()}', json={'status': 'done'}, headers=bearer(await make_user()))
        assert resp.status_code == 404

    async def test_invalid_uuid(self, api, make_user):
        resp = await api.patch('/api/tasks/not-a-uuid', json={'status': 'done'}, headers=bearer(await make_user()))
        assert resp.status_code == 422

    # Regression: TaskUpdate.status used to accept and store any string.
    @pytest.mark.parametrize('status', ['hacked', '', 'DONE', 1])
    async def test_unknown_status_is_rejected(self, api, db_factory, make_user, add_tasks, status):
        me = await make_user()
        [task] = await add_tasks(me, {})
        resp = await api.patch(f'/api/tasks/{task.id}', json={'status': status}, headers=bearer(me))
        assert resp.status_code == 422
        async with db_factory() as s:
            assert await s.scalar(select(Task.status).where(Task.id == task.id)) == 'open'

    @pytest.mark.parametrize('status', ['open', 'in_progress', 'done'])
    async def test_every_known_status_is_accepted(self, api, make_user, add_tasks, status):
        me = await make_user()
        [task] = await add_tasks(me, {})
        resp = await api.patch(f'/api/tasks/{task.id}', json={'status': status}, headers=bearer(me))
        assert resp.json()['status'] == status


class TestSchema:
    async def test_task_user_fk_is_enforced(self, db_factory):
        async with db_factory() as s:
            with pytest.raises(Exception, match='foreign key'):
                await s.execute(text(
                    "INSERT INTO tasks (id, user_id, content) VALUES (gen_random_uuid(), gen_random_uuid(), 'x')"))

    async def test_discord_id_is_unique(self, make_user):
        await make_user(discord_id='42')
        with pytest.raises(Exception, match='unique'):
            await make_user(discord_id='42')
