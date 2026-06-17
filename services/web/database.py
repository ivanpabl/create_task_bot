from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

_engine = None
_session_factory = None


def init_postgres(dsn: str):
    global _engine, _session_factory
    _engine = create_async_engine(dsn, echo=False)
    _session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


def get_engine():
    return _engine


def get_session_factory():
    return _session_factory


async def get_db() -> AsyncSession:
    if _session_factory is None:
        raise RuntimeError("Database not initialized")
    async with _session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
