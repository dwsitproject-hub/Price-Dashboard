"""Pydantic request schemas (responses are plain dicts shaped by services)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

AuthProvider = Literal["local", "sso", "both"]


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class OverrideIn(BaseModel):
    module: str = Field(max_length=48)
    effect: Literal["grant", "deny"]


class UserCreateIn(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    role: str = Field(max_length=32)
    auth_provider: AuthProvider = "local"
    password: str | None = Field(default=None, max_length=128)
    overrides: list[OverrideIn] | None = None


class UserUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = Field(default=None, max_length=32)
    auth_provider: AuthProvider | None = None
    status: Literal["active", "disabled"] | None = None
    overrides: list[OverrideIn] | None = None


class RoleModulesIn(BaseModel):
    modules: list[str] = Field(max_length=100)


class SettingsIn(BaseModel):
    values: dict[str, bool | int | str]


class PipelineRunIn(BaseModel):
    kind: Literal["incremental", "full", "recompute"]
