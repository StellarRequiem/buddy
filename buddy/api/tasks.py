"""
GET  /tasks         — list all tasks
POST /tasks         — create a task
PUT  /tasks/{id}    — update status/result
GET  /tasks/{id}    — get single task
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from buddy.memory.store import create_task, update_task, list_tasks

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskCreate(BaseModel):
    title: str
    metadata: dict | None = None


class TaskUpdate(BaseModel):
    status: str
    result: str = ""


@router.get("")
async def get_tasks(status: str | None = None):
    return {"tasks": list_tasks(status=status)}


@router.post("", status_code=201)
async def post_task(body: TaskCreate):
    task_id = create_task(body.title, body.metadata)
    return {"id": task_id, "title": body.title, "status": "queued"}


@router.put("/{task_id}")
async def put_task(task_id: str, body: TaskUpdate):
    allowed = {"queued", "running", "done", "failed"}
    if body.status not in allowed:
        raise HTTPException(400, f"status must be one of {allowed}")
    update_task(task_id, body.status, body.result)
    return {"id": task_id, "status": body.status}
