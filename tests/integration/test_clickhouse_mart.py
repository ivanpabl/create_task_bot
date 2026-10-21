"""PostgreSQL -> ClickHouse tasks mart: reconciliation of counts, duplicates and aggregates."""
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from services.analytics.clickhouse import (
    MART_TABLE, ClickHouseClient, ensure_schema, sync_tasks, urgency_stats,
)
from services.database import Task

pytestmark = [pytest.mark.postgres, pytest.mark.clickhouse]

CH_URL = os.getenv('TEST_CLICKHOUSE_URL')

if not CH_URL:
    pytest.skip('TEST_CLICKHOUSE_URL is not set', allow_module_level=True)


@pytest.fixture
async def ch():
    client = ClickHouseClient(CH_URL, os.getenv('TEST_CLICKHOUSE_USER', 'default'),
                              os.getenv('TEST_CLICKHOUSE_PASSWORD', ''))
    await client.execute(f'DROP TABLE IF EXISTS {MART_TABLE}')
    await ensure_schema(client)
    yield client
    await client.execute(f'DROP TABLE IF EXISTS {MART_TABLE}')


@pytest.fixture
def seed(db_factory, make_user):
    """Two users with a known mix of urgencies and statuses."""
    async def make():
        me, other = await make_user(), await make_user()
        specs = [(me, 'high', 'open'), (me, 'high', 'done'), (me, 'medium', 'open'),
                 (me, 'low', 'in_progress'), (other, 'low', 'open'), (other, 'medium', 'done')]
        async with db_factory() as s:
            s.add_all([Task(id=uuid4(), user_id=u.id, content=f'задача {i}', urgency=urg,
                            status=st, source='bot_server') for i, (u, urg, st) in enumerate(specs)])
            await s.commit()
        return me, other
    return make


async def ch_count(ch, final=True, where=''):
    rows = await ch.query(f"SELECT count() AS n FROM {MART_TABLE} {'FINAL' if final else ''} {where}")
    return int(rows[0]['n'])


async def pg_group(db_factory, column):
    async with db_factory() as s:
        rows = (await s.execute(select(column, func.count()).group_by(column))).all()
    return {k: n for k, n in rows}


async def ch_group(ch, column):
    rows = await ch.query(f'SELECT {column} AS k, count() AS n FROM {MART_TABLE} FINAL GROUP BY k')
    return {r['k']: int(r['n']) for r in rows}


class TestReconciliation:
    async def test_row_count_matches(self, db_factory, ch, seed):
        await seed()
        sent = await sync_tasks(db_factory, ch)
        assert sent == 6
        assert await ch_count(ch) == 6

    async def test_empty_source(self, db_factory, ch):
        assert await sync_tasks(db_factory, ch) == 0
        assert await ch_count(ch) == 0

    @pytest.mark.parametrize('column', ['urgency', 'status', 'source'])
    async def test_aggregates_match(self, db_factory, ch, seed, column):
        await seed()
        await sync_tasks(db_factory, ch)
        assert await ch_group(ch, column) == await pg_group(db_factory, getattr(Task, column))

    async def test_every_id_is_present_once(self, db_factory, ch, seed):
        await seed()
        await sync_tasks(db_factory, ch)
        async with db_factory() as s:
            pg_ids = {str(i) for i in (await s.execute(select(Task.id))).scalars()}
        rows = await ch.query(f'SELECT toString(id) AS id, count() AS n FROM {MART_TABLE} FINAL GROUP BY id')
        assert {r['id'] for r in rows} == pg_ids
        assert all(int(r['n']) == 1 for r in rows)

    async def test_content_is_copied_verbatim(self, db_factory, ch, make_user):
        user = await make_user()
        text = 'Ёлка 🚀 "кавычки" \\ обратный слэш\nвторая строка'
        async with db_factory() as s:
            s.add(Task(id=uuid4(), user_id=user.id, content=text))
            await s.commit()
        await sync_tasks(db_factory, ch)
        assert (await ch.query(f'SELECT content FROM {MART_TABLE} FINAL'))[0]['content'] == text


class TestReplacingMergeTree:
    async def test_repeated_sync_does_not_duplicate(self, db_factory, ch, seed):
        await seed()
        for _ in range(3):
            await sync_tasks(db_factory, ch)
        assert await ch_count(ch) == 6

    async def test_raw_parts_hold_versions_until_merge(self, db_factory, ch, seed):
        await seed()
        await ch.execute(f'SYSTEM STOP MERGES {MART_TABLE}')   # keep the test deterministic
        await sync_tasks(db_factory, ch)
        await sync_tasks(db_factory, ch)
        assert await ch_count(ch, final=False) == 12        # two unmerged parts
        await ch.execute(f'SYSTEM START MERGES {MART_TABLE}')
        await ch.execute(f'OPTIMIZE TABLE {MART_TABLE} FINAL')
        assert await ch_count(ch, final=False) == 6         # collapsed after merge

    async def test_latest_status_wins(self, db_factory, ch, seed):
        me, _ = await seed()
        await sync_tasks(db_factory, ch)
        async with db_factory() as s:
            await s.execute(update(Task).where(Task.user_id == me.id).values(status='done'))
            await s.commit()
        await sync_tasks(db_factory, ch)

        rows = await ch.query(f"SELECT status, count() AS n FROM {MART_TABLE} FINAL "
                              f"WHERE user_id = toUUID('{me.id}') GROUP BY status")
        assert {r['status']: int(r['n']) for r in rows} == {'done': 4}

    async def test_task_without_user_goes_to_null_bucket(self, db_factory, ch):
        async with db_factory() as s:
            s.add(Task(id=uuid4(), user_id=None, content='без владельца'))
            await s.commit()
        await sync_tasks(db_factory, ch)
        assert await ch_count(ch, where="WHERE user_id = toUUID('00000000-0000-0000-0000-000000000000')") == 1


class TestUrgencyStats:
    async def test_per_user_stats(self, db_factory, ch, seed):
        me, other = await seed()
        await sync_tasks(db_factory, ch)
        assert await urgency_stats(ch, me.id) == {'high': 2, 'medium': 1, 'low': 1}
        assert await urgency_stats(ch, other.id) == {'low': 1, 'medium': 1}

    async def test_unknown_user(self, db_factory, ch, seed):
        await seed()
        await sync_tasks(db_factory, ch)
        assert await urgency_stats(ch, uuid4()) == {}

    @pytest.mark.parametrize('bad', ["' OR 1=1 --", 'not-a-uuid'])
    async def test_user_id_is_validated(self, ch, bad):
        with pytest.raises(ValueError):
            await urgency_stats(ch, bad)
