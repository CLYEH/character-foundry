"""Input schemas for the `task.*` MCP tools (T-088).

Outputs reuse the existing `app.schemas.task` envelopes (`TaskResponse`,
`TaskListResponse`, `CancelTaskResponse`) so the MCP wire shape mirrors
`/v1/tasks/*` exactly — see `app/mcp/tools/task.py`.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.schemas.task import TaskStatus


class TaskGetInput(BaseModel):
    """Input for `task.get` (wraps `GET /v1/tasks/{task_id}`)."""

    task_id: uuid.UUID = Field(..., description="The task id to fetch.")


class TaskListInput(BaseModel):
    """Input for `task.list` (wraps `GET /v1/tasks`).

    `status` is the `TaskStatus` literal (not a free string) so agents see
    the valid filter values in the tool schema. `limit` mirrors the route's
    1–200 bound.
    """

    status: TaskStatus | None = Field(
        default=None,
        description="Optional status filter (queued / running / completed / failed / cancelled).",
    )
    limit: int = Field(default=50, ge=1, le=200, description="Max tasks to return (1–200).")


class TaskCancelInput(BaseModel):
    """Input for `task.cancel` (wraps `POST /v1/tasks/{task_id}/cancel`)."""

    task_id: uuid.UUID = Field(..., description="The task id to cancel.")
