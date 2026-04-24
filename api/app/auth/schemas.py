"""Pydantic request/response bodies for the auth endpoints.

Lives next to the auth service so tests can import `LoginResponse` etc.
without pulling the whole FastAPI app.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class UserPublic(BaseModel):
    id: uuid.UUID
    name: str
    email: EmailStr
    team_id: uuid.UUID
    created_at: datetime


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    user: UserPublic


class RefreshResponse(BaseModel):
    access_token: str
    expires_in: int


class MeResponse(BaseModel):
    user: UserPublic


class LogoutResponse(BaseModel):
    ok: bool = True
