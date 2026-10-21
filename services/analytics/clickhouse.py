"""Analytics mart in ClickHouse, fed from PostgreSQL.

Tasks change after they are created (status, assignee), so every sync re-sends a
snapshot with a newer `version`. ReplacingMergeTree keeps the latest version per
(user_id, id) once parts merge; reads use FINAL to get that result right away.
"""
import asyncio
import json
import time
from uuid import UUID

import httpx
from sqlalchemy import select

from services.database import Task

NULL_USER = UUID(int=0)

MART_TABLE = 'tasks_mart'

CREATE_MART = f"""
CREATE TABLE IF NOT EXISTS {MART_TABLE} (
    id        UUID,
    user_id   UUID,
    source    LowCardinality(String),
    urgency   LowCardinality(String),
    status    LowCardinality(String),
    channel   String,
    content   String,
    dm_sent   LowCardinality(String),
    version   UInt64
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (user_id, id)
"""

SYNC_INTERVAL = 60


class ClickHouseClient:
    """Minimal client over the ClickHouse HTTP interface."""

    def __init__(self, url: str, user: str = 'default', password: str = '', database: str = 'default'):
        self.url = url.rstrip('/')
        self.auth = (user, password)
        self.database = database

    async def execute(self, sql: str, data: str | None = None) -> str:
        params = {'database': self.database}
        async with httpx.AsyncClient(timeout=30, auth=self.auth) as client:
            if data is None:
                resp = await client.post(self.url, params=params, content=sql)
            else:
                resp = await client.post(self.url, params={**params, 'query': sql}, content=data)
        if resp.status_code != 200:
            raise RuntimeError(f'ClickHouse error {resp.status_code}: {resp.text[:500]}')
        return resp.text

    async def query(self, sql: str) -> list[dict]:
        text = await self.execute(sql + ' FORMAT JSONEachRow')
        return [json.loads(line) for line in text.splitlines() if line]

    async def insert(self, table: str, rows: list[dict]):
        if rows:
            body = '\n'.join(json.dumps(r, ensure_ascii=False) for r in rows)
            await self.execute(f'INSERT INTO {table} FORMAT JSONEachRow', body)


def _to_mart_row(task: Task, version: int) -> dict:
    return {
        'id': str(task.id),
        'user_id': str(task.user_id or NULL_USER),
        'source': task.source or '',
        'urgency': task.urgency or 'low',
        'status': task.status or 'open',
        'channel': task.channel or '',
        'content': task.content,
        'dm_sent': task.dm_sent or 'pending',
        'version': version,
    }


async def ensure_schema(ch: ClickHouseClient):
    await ch.execute(CREATE_MART)


async def sync_tasks(session_factory, ch: ClickHouseClient) -> int:
    """Copy a snapshot of every task into the mart. Returns the number of rows sent."""
    version = time.time_ns()
    async with session_factory() as session:
        tasks = (await session.execute(select(Task))).scalars().all()
    rows = [_to_mart_row(t, version) for t in tasks]
    await ch.insert(MART_TABLE, rows)
    return len(rows)


async def urgency_stats(ch: ClickHouseClient, user_id: UUID | str) -> dict[str, int]:
    rows = await ch.query(
        f"SELECT urgency, count() AS n FROM {MART_TABLE} FINAL "
        f"WHERE user_id = toUUID('{UUID(str(user_id))}') GROUP BY urgency"
    )
    return {r['urgency']: int(r['n']) for r in rows}


async def run_sync_loop(session_factory, ch: ClickHouseClient):
    await ensure_schema(ch)
    while True:
        try:
            count = await sync_tasks(session_factory, ch)
            print(f"[clickhouse] Synced {count} task(s) to {MART_TABLE}")
        except Exception as e:
            print(f"[clickhouse] Sync error: {e}")
        await asyncio.sleep(SYNC_INTERVAL)
