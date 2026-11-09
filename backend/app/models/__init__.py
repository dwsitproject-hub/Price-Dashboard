"""SQLAlchemy ORM models. The schema itself is owned by Alembic migrations (SQL-first)."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (ARRAY, BigInteger, Boolean, Date, DateTime, ForeignKey, Integer,
                        LargeBinary, Numeric, SmallInteger, String, Text, text)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ═════════════════════════════════ iam ═════════════════════════════════════
class Role(Base):
    __tablename__ = "roles"
    __table_args__ = {"schema": "iam"}
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    permissions: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    is_system: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    modules: Mapped[list[RoleModule]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class Module(Base):
    __tablename__ = "modules"
    __table_args__ = {"schema": "iam"}
    code: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(24), server_default="analytics")
    release: Mapped[str] = mapped_column(String(4), server_default="R1")
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    icon: Mapped[str | None] = mapped_column(String(32))
    is_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class RoleModule(Base):
    __tablename__ = "role_modules"
    __table_args__ = {"schema": "iam"}
    role_id: Mapped[int] = mapped_column(ForeignKey("iam.roles.id", ondelete="CASCADE"), primary_key=True)
    module_code: Mapped[str] = mapped_column(ForeignKey("iam.modules.code", ondelete="CASCADE"),
                                             primary_key=True)


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "iam"}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()"))
    email: Mapped[str] = mapped_column(String(254))
    display_name: Mapped[str] = mapped_column(String(120))
    role_id: Mapped[int] = mapped_column(ForeignKey("iam.roles.id"))
    auth_provider: Mapped[str] = mapped_column(String(8), server_default="local")
    password_hash: Mapped[str | None] = mapped_column(Text)
    must_change_password: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    hub_sub: Mapped[str | None] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(12), server_default="active")
    failed_login_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_method: Mapped[str | None] = mapped_column(String(8))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("iam.users.id"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("iam.users.id"))

    role: Mapped[Role] = relationship(lazy="joined")
    overrides: Mapped[list[UserModuleOverride]] = relationship(
        cascade="all, delete-orphan", lazy="selectin",
        primaryjoin="User.id == UserModuleOverride.user_id")


class UserModuleOverride(Base):
    __tablename__ = "user_module_overrides"
    __table_args__ = {"schema": "iam"}
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("iam.users.id", ondelete="CASCADE"),
                                               primary_key=True)
    module_code: Mapped[str] = mapped_column(ForeignKey("iam.modules.code", ondelete="CASCADE"), primary_key=True)
    effect: Mapped[str] = mapped_column(String(5))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("iam.users.id"))


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = {"schema": "iam"}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("iam.users.id", ondelete="CASCADE"))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    csrf_hash: Mapped[bytes] = mapped_column(LargeBinary)
    auth_method: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(32))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    user: Mapped[User] = relationship(lazy="joined")


class OidcLoginState(Base):
    __tablename__ = "oidc_login_states"
    __table_args__ = {"schema": "iam"}
    state_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    code_verifier_enc: Mapped[str] = mapped_column(Text)
    nonce: Mapped[str] = mapped_column(String(128))
    next_path: Mapped[str | None] = mapped_column(String(512))
    ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ═════════════════════════════════ ops ═════════════════════════════════════
class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = {"schema": "ops"}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("iam.users.id"))
    actor_email: Mapped[str | None] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(8), server_default="success")
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))


class AppSetting(Base):
    __tablename__ = "app_settings"
    __table_args__ = {"schema": "ops"}
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[object] = mapped_column(JSONB)
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("iam.users.id"))


class IntegrationToken(Base):
    __tablename__ = "integration_tokens"
    __table_args__ = {"schema": "ops"}
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    token_enc: Mapped[str] = mapped_column(Text)
    obtained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = {"schema": "ops"}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()"))
    kind: Mapped[str] = mapped_column(String(16))
    trigger: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(12), server_default="queued")
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("iam.users.id"))
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rows_fetched: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    rows_upserted: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    series_computed: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    data_version: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    warnings_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    steps: Mapped[list[PipelineRunStep]] = relationship(order_by="PipelineRunStep.seq", lazy="selectin",
                                                        cascade="all, delete-orphan")


class PipelineRunStep(Base):
    __tablename__ = "pipeline_run_steps"
    __table_args__ = {"schema": "ops"}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ops.pipeline_runs.id",
                                                                          ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(SmallInteger)
    step: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(12))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))


class DqFinding(Base):
    __tablename__ = "dq_findings"
    __table_args__ = {"schema": "ops"}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ops.pipeline_runs.id",
                                                                          ondelete="CASCADE"))
    check_code: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))
    ric: Mapped[str | None] = mapped_column(String(32))
    obs_date: Mapped[date | None] = mapped_column(Date)
    detail: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))


# ═════════════════════════════════ market (reference tables) ═══════════════
class Product(Base):
    __tablename__ = "products"
    __table_args__ = {"schema": "market"}
    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    oi_display_name: Mapped[str | None] = mapped_column(String(80))
    exchange: Mapped[str | None] = mapped_column(String(32))
    native_unit: Mapped[str] = mapped_column(String(24))
    conversion_type: Mapped[str] = mapped_column(String(8))
    conversion_factor: Mapped[float | None] = mapped_column(Numeric(12, 6))
    conversion_fx_ric: Mapped[str | None] = mapped_column(String(32))
    oi_contracts: Mapped[int] = mapped_column(SmallInteger)
    oi_display_order: Mapped[int | None] = mapped_column(SmallInteger)
    sort_order: Mapped[int] = mapped_column(SmallInteger)


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = {"schema": "market"}
    ric: Mapped[str] = mapped_column(String(32), primary_key=True)
    product_code: Mapped[str | None] = mapped_column(ForeignKey("market.products.code"))
    category: Mapped[str] = mapped_column(String(24))
    contract_no: Mapped[int | None] = mapped_column(SmallInteger)
    display_name: Mapped[str] = mapped_column(String(120))
    native_unit: Mapped[str] = mapped_column(String(24))
    conversion_type: Mapped[str] = mapped_column(String(8))
    conversion_factor: Mapped[float | None] = mapped_column(Numeric(12, 6))
    conversion_fx_ric: Mapped[str | None] = mapped_column(String(32))
    price_basis: Mapped[str] = mapped_column(String(8))
    universe: Mapped[str] = mapped_column(String(12))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Series(Base):
    __tablename__ = "series"
    __table_args__ = {"schema": "market"}
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    module_code: Mapped[str] = mapped_column(ForeignKey("iam.modules.code"))
    group_name: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(16))
    family: Mapped[str | None] = mapped_column(String(32))
    tenor: Mapped[str | None] = mapped_column(String(4))
    unit: Mapped[str] = mapped_column(String(24))
    local_unit: Mapped[str | None] = mapped_column(String(24))
    definition: Mapped[dict] = mapped_column(JSONB)
    sort_order: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


__all__ = ["Base", "Role", "Module", "RoleModule", "User", "UserModuleOverride",
           "Session", "OidcLoginState", "AuditLog", "AppSetting", "IntegrationToken", "PipelineRun",
           "PipelineRunStep", "DqFinding", "Product", "Instrument", "Series"]
