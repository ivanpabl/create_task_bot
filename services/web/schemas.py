from pydantic import BaseModel
from typing import Optional
from uuid import UUID

from services.database import TaskStatus


class MessageCreate(BaseModel):
    message_id: str
    content: str
    author: str
    author_id: str
    channel: str
    timestamp: str
    source: str = 'bot_server'
    source_message_id: Optional[str] = None


class TranscriptionCreate(BaseModel):
    guild_id: str
    channel_id: str
    channel_name: str
    user_id: str
    user_name: str
    content: str
    source: str = 'desktop_loopback'
    internal_user_id: Optional[str] = None


class TaskOut(BaseModel):
    id: UUID
    user_id: Optional[UUID] = None
    source: Optional[str] = None
    content: str
    context: Optional[str] = None
    author: Optional[str] = None
    assignee: Optional[str] = None
    deadline: Optional[str] = None
    urgency: str = 'low'
    status: str = 'open'
    channel: Optional[str] = None
    guild_id: Optional[str] = None
    keywords: Optional[list] = None
    source_message_id: Optional[str] = None
    source_transcription_id: Optional[str] = None

    model_config = {'from_attributes': True}


class TaskUpdate(BaseModel):
    status: Optional[TaskStatus] = None
    assignee: Optional[str] = None


class UserOut(BaseModel):
    id: UUID
    discord_id: str
    username: str

    model_config = {'from_attributes': True}
