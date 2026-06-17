import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Column, String, DateTime, Text, JSON, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, relationship
from dotenv import load_dotenv
import os
import enum

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

DB_PATH = Path(__file__).resolve().parent.parent / 'messages.db'

# ── PostgreSQL async engine ──────────────────────────────────────────────

POSTGRES_DSN = os.getenv('POSTGRES_DSN')
_async_engine = None
_async_session_factory = None


class Base(DeclarativeBase):
    pass


class TaskStatus(str, enum.Enum):
    open = 'open'
    in_progress = 'in_progress'
    done = 'done'


class SourceType(str, enum.Enum):
    bot_server = 'bot_server'
    oauth2_dm = 'oauth2_dm'
    bot_voice = 'bot_voice'
    desktop_loopback = 'desktop_loopback'


class User(Base):
    __tablename__ = 'users'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    discord_id = Column(String, unique=True, nullable=False)
    username = Column(String, nullable=False)
    access_token = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)

    messages = relationship('Message', back_populates='user')
    transcriptions = relationship('Transcription', back_populates='user')
    tasks = relationship('Task', back_populates='user')


class Message(Base):
    __tablename__ = 'messages'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    message_id = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    author = Column(String, nullable=False)
    author_id = Column(String, nullable=False)
    channel = Column(String, nullable=False)
    timestamp = Column(String, nullable=False)
    source = Column(String, default=SourceType.bot_server.value)
    source_message_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship('User', back_populates='messages')


class Transcription(Base):
    __tablename__ = 'transcriptions'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    guild_id = Column(String, nullable=False)
    channel_id = Column(String, nullable=False)
    channel_name = Column(String, nullable=False)
    user_id_str = Column(String, nullable=False)
    user_name = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    timestamp = Column(String, nullable=False)
    source = Column(String, default=SourceType.bot_voice.value)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship('User', back_populates='transcriptions')


class Task(Base):
    __tablename__ = 'tasks'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    source = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    context = Column(Text, nullable=True)
    author = Column(String, nullable=True)
    assignee = Column(String, nullable=True)
    deadline = Column(String, nullable=True)
    urgency = Column(String, default='low')
    status = Column(String, default=TaskStatus.open.value)
    channel = Column(String, nullable=True)
    guild_id = Column(String, nullable=True)
    keywords = Column(JSON, nullable=True)
    source_message_id = Column(String, nullable=True)
    source_transcription_id = Column(String, nullable=True)
    dm_sent = Column(String, default='pending')  # pending | sent | failed

    user = relationship('User', back_populates='tasks')


def get_async_engine():
    global _async_engine
    if _async_engine is None and POSTGRES_DSN:
        _async_engine = create_async_engine(POSTGRES_DSN, echo=False)
    return _async_engine


def get_async_session_factory():
    global _async_session_factory
    if _async_session_factory is None and POSTGRES_DSN:
        engine = get_async_engine()
        _async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return _async_session_factory


async def init_db():
    if POSTGRES_DSN:
        try:
            engine = get_async_engine()
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            print("[db] PostgreSQL tables created")
        except Exception as e:
            print(f"[db] PostgreSQL not available: {e}")
            print("[db] Running without database — tasks won't be persisted")
    else:
        print("[db] No POSTGRES_DSN configured")


async def get_session() -> AsyncSession:
    factory = get_async_session_factory()
    if factory is None:
        raise RuntimeError("POSTGRES_DSN not configured")
    async with factory() as session:
        yield session


# ── SQLite legacy (backward compat) ──────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db_sqlite():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL,
            content TEXT NOT NULL,
            author TEXT NOT NULL,
            author_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_message_id
            ON messages(message_id)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transcriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def save_message(message_id: str, content: str, author: str, author_id: str, channel: str, timestamp: str):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO messages
                (message_id, content, author, author_id, channel, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, content, author, author_id, channel, timestamp),
        )
        conn.commit()
    finally:
        conn.close()


def get_messages_since(after_time: datetime) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE timestamp >= ? ORDER BY timestamp ASC",
            (after_time.isoformat(),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def save_transcription(guild_id: str, channel_id: str, channel_name: str, user_id: str, user_name: str, content: str):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO transcriptions
                (guild_id, channel_id, channel_name, user_id, user_name, content, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, channel_name, user_id, user_name, content,
             datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_transcriptions_since(after_time: datetime) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM transcriptions WHERE timestamp >= ? ORDER BY timestamp ASC",
            (after_time.isoformat(),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
