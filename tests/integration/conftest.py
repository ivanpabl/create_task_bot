import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from services.database import Base, User
from services.web import database as web_db
from services.web.auth import create_jwt

TEST_DSN = os.getenv('TEST_POSTGRES_DSN')

pytestmark = pytest.mark.postgres


def pytest_collection_modifyitems(items):
    if TEST_DSN:
        return
    skip = pytest.mark.skip(reason='TEST_POSTGRES_DSN is not set')
    for item in items:
        if 'integration' in item.nodeid:
            item.add_marker(skip)


@pytest.fixture
async def db_factory(monkeypatch):
    """Fresh schema per test; the app's session factory points at the test database."""
    engine = create_async_engine(TEST_DSN, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(web_db, '_session_factory', factory)
    yield factory
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def api():
    from services.web.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        yield client


@pytest.fixture
def make_user(db_factory):
    async def make(discord_id=None, username='tester'):
        async with db_factory() as s:
            user = User(id=uuid4(), discord_id=discord_id or str(uuid4().int)[:18], username=username)
            s.add(user)
            await s.commit()
            return user
    return make


def bearer(user) -> dict:
    return {'Authorization': f'Bearer {create_jwt(user.id)}'}
