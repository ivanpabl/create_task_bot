from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import uuid4
from datetime import datetime

from services.web.database import get_db
from services.web.models import Message
from services.web.schemas import MessageCreate
from services.web.dependencies import verify_api_key

router = APIRouter(prefix='/api/messages', tags=['messages'])


@router.post('')
async def create_message(
    data: MessageCreate,
    db: AsyncSession = Depends(get_db),
    authorization: str = Header(None),
):
    await verify_api_key(authorization)

    message = Message(
        id=uuid4(),
        message_id=data.message_id,
        content=data.content,
        author=data.author,
        author_id=data.author_id,
        channel=data.channel,
        timestamp=data.timestamp,
        source=data.source,
        source_message_id=data.source_message_id,
        created_at=datetime.utcnow(),
    )
    db.add(message)
    await db.commit()
    return {'ok': True, 'id': str(message.id)}
