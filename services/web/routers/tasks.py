from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID
from typing import Optional

from services.web.database import get_db
from services.web.models import Task, User
from services.web.schemas import TaskOut, TaskUpdate
from services.web.dependencies import get_current_user

router = APIRouter(prefix='/api/tasks', tags=['tasks'])


@router.get('', response_model=list[TaskOut])
async def list_tasks(
    status: Optional[str] = None,
    urgency: Optional[str] = None,
    source: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Task).where(Task.user_id == current_user.id)
    if status:
        query = query.where(Task.status == status)
    if urgency:
        query = query.where(Task.urgency == urgency)
    if source:
        query = query.where(Task.source == source)
    query = query.order_by(Task.urgency.desc())
    result = await db.execute(query)
    return result.scalars().all()


@router.patch('/{task_id}', response_model=TaskOut)
async def update_task(
    task_id: UUID,
    data: TaskUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Task).where(Task.id == task_id, Task.user_id == current_user.id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail='Task not found')

    if data.status is not None:
        task.status = data.status
    if data.assignee is not None:
        task.assignee = data.assignee

    await db.commit()
    await db.refresh(task)
    return task
