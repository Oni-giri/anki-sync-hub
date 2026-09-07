from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OwnerSetup(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=1024)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class SyncUserCreate(OwnerSetup):
    pass


class SyncPasswordUpdate(BaseModel):
    password: str = Field(min_length=12, max_length=1024)


class SyncUserEnabledUpdate(BaseModel):
    enabled: bool


class MCPTokenCreate(BaseModel):
    sync_user_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=80)
    scopes: set[Literal["read", "write", "destructive"]] = Field(
        default_factory=lambda: {"read", "write"}
    )
