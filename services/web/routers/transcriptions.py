from uuid import UUID, uuid4
from datetime import datetime

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from services.web.database import get_db
from services.web.models import Transcription, Task
from services.web.schemas import TranscriptionCreate
from services.web.dependencies import verify_api_key
from services.nlp_engine import extract_tasks

router = APIRouter(prefix='/api/transcriptions', tags=['transcriptions'])


@router.post('')
async def create_transcription(
    data: TranscriptionCreate,
    db: AsyncSession = Depends(get_db),
    authorization: str = Header(None),
):
    await verify_api_key(authorization)

    transcription = Transcription(
        id=uuid4(),
        guild_id=data.guild_id,
        channel_id=data.channel_id,
        channel_name=data.channel_name,
        user_id_str=data.user_id,
        user_name=data.user_name,
        content=data.content,
        timestamp=datetime.utcnow().isoformat(),
        source=data.source,
        created_at=datetime.utcnow(),
    )
    db.add(transcription)
    await db.commit()

    # Extract tasks if an internal user_id is provided
    task_count = 0
    if data.internal_user_id:
        user_uuid = UUID(data.internal_user_id)
        extracted = extract_tasks([{
            'content': data.content,
            'author': data.user_name,
            'channel': data.channel_name,
            'timestamp': datetime.utcnow().isoformat(),
        }])
        for t in extracted:
            task = Task(
                id=uuid4(),
                user_id=user_uuid,
                source=data.source,
                content=t['content'],
                author=t['author'],
                urgency=t['urgency'],
                keywords=t['keywords'],
                channel=data.channel_name,
                dm_sent='pending',
            )
            db.add(task)
            task_count += 1
        if task_count:
            await db.commit()

    return {'ok': True, 'id': str(transcription.id), 'tasks_created': task_count}
