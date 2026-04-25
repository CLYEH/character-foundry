"""Pydantic DTOs for the Task resource.

Mirrors planning/backend/api-shape.md §6.6. Kept as a Pydantic model rather
than a TypedDict so FastAPI emits an OpenAPI schema both UI and agent
clients can consume.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.task import Task

TaskStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
TaskType = Literal[
    "create_checkpoint",
    "create_alias",
    "create_motion",
    "export_zip",
    "copy_character",
]
EntityType = Literal["checkpoint", "alias", "motion", "character", "export"]
CancelOutcome = Literal[
    "cancelled_immediately",
    "cancel_pending",
    "too_late_completed",
    "too_late_failed",
]


class TaskDTO(BaseModel):
    id: uuid.UUID
    status: TaskStatus
    task_type: TaskType
    entity_type: EntityType | None = None
    entity_id: uuid.UUID | None = None
    queue_position: int | None = None
    progress: float | None = None
    estimated_duration_ms: int | None = None
    cancel_requested: bool = False
    cancel_requested_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: datetime

    @classmethod
    def from_model(cls, task: Task, *, queue_position: int | None = None) -> TaskDTO:
        return cls(
            id=task.id,
            status=task.status,  # type: ignore[arg-type]
            task_type=task.task_type,  # type: ignore[arg-type]
            entity_type=task.entity_type,  # type: ignore[arg-type]
            entity_id=task.entity_id,
            queue_position=queue_position if task.status == "queued" else None,
            progress=task.progress,
            estimated_duration_ms=task.estimated_duration_ms,
            cancel_requested=task.cancel_requested,
            cancel_requested_at=task.cancel_requested_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
            result=task.result,
            error=task.error,
            created_at=task.created_at,
        )


class TaskResponse(BaseModel):
    """Envelope for `GET /v1/tasks/{id}`."""

    task: TaskDTO


class TaskListResponse(BaseModel):
    items: list[TaskDTO]


class CancelTaskResponse(BaseModel):
    task: TaskDTO
    cancel_outcome: CancelOutcome


class TaskSseEvent(BaseModel):
    """Schema for what gets serialized over SSE.

    Matches planning/backend/api-shape.md §3.1 SSE event shape. Frontend
    treats this as the source of truth for the streaming contract.
    """

    status: TaskStatus
    queue_position: int | None = None
    progress: float | None = None
    partial_preview_url: str | None = None
    message: str | None = None
    cancel_requested: bool | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    # Optional metadata that helps SSE clients reconcile against polling.
    task_id: uuid.UUID | None = Field(default=None)
