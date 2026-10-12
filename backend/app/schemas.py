"""Pydantic-схемы запросов/ответов."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ------------------------------------------------------------------ auth ----

class LoginRequest(BaseModel):
    login: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    login: str
    full_name: str
    role: str
    email: str | None = None
    all_warehouses: bool
    warehouse_ids: list[int]
    last_login_at: datetime | None = None


class TokensOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


# ----------------------------------------------------------------- admin ----

class UserCreate(BaseModel):
    login: str = Field(min_length=2, max_length=64)
    full_name: str = Field(min_length=2, max_length=128)
    password: str = Field(min_length=8, max_length=128)
    role: str = "manager"
    email: str | None = None
    warehouse_ids: list[int] = []


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    warehouse_ids: list[int] | None = None


# --------------------------------------------------------------- metrics ----

class WarehouseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    kind: str
    capacity: float
    unit: str


class CargoRow(BaseModel):
    warehouse_id: int
    warehouse: str
    cargo_group: str
    qty: float
    weight_t: float
    overdue_weight_t: float
    avg_age_days: float


class CargoBalanceOut(BaseModel):
    snapshot_at: datetime | None
    total_weight_t: float
    overdue_weight_t: float
    rows: list[CargoRow]


class CapacityRow(BaseModel):
    warehouse_id: int
    warehouse: str
    code: str
    used: float
    capacity: float
    free: float
    utilization: float
    unit: str
    status: str  # ok | warning | critical


class CapacityOut(BaseModel):
    snapshot_at: datetime | None
    rows: list[CapacityRow]


class DynamicsPoint(BaseModel):
    bucket_date: date
    inbound_t: float
    outbound_t: float
    processed_t: float
    docs_count: int
    avg_dwell_hours: float


class DynamicsOut(BaseModel):
    days: int
    points: list[DynamicsPoint]
    processed_total: float
    processed_prev: float
    delta_pct: float | None
    avg_dwell_hours: float


class SummaryOut(BaseModel):
    generated_at: datetime
    data_as_of: datetime | None
    warehouses: int
    cargo_weight_t: float
    overdue_weight_t: float
    utilization: float
    processed_today_t: float
    processed_7d_t: float
    dwell_hours: float
    delta_pct: float | None


# -------------------------------------------------------------- settings ----

class PreferenceIn(BaseModel):
    """Значение настройки — произвольный JSON (раскладка дашборда, фильтры)."""

    value: Any


class PreferenceOut(BaseModel):
    key: str
    value: Any


# --------------------------------------------------- админ: настройки 1С ----


class SettingsEnvelope(BaseModel):
    """Ответ GET/PUT /api/admin/settings: значения + defaults + что перебито из .env."""

    settings: dict[str, Any]
    defaults: dict[str, Any]
    overridden: list[str]
    specs: dict[str, Any] = {}
    scheduler: dict[str, Any] | None = None


class OneCCheck(BaseModel):
    path: str
    ok: bool
    status: int | None = None
    ms: int = 0
    error: str | None = None


class OneCTestOut(BaseModel):
    source: str
    ok: bool
    checks: list[OneCCheck]


class SyncRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    finished_at: datetime | None = None
    source: str
    status: str
    rows: int
    error: str | None = None


class SyncRunResult(SyncRunOut):
    """Результат загрузки: итог + разбивка строк по разделам среза."""

    counts: dict[str, int] = {}


class PushOut(BaseModel):
    status: str
    source: str
    run_id: int
    snapshot_at: datetime
    rows: int
    counts: dict[str, int] = {}


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    login: str | None = None
    action: str
    ip: str | None = None
    detail: str | None = None


# -------------------------------------------------- админ: склады ----


WAREHOUSE_KINDS = ("warehouse", "cold", "terminal")


class WarehouseAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    kind: str
    capacity: float
    unit: str
    is_active: bool


class WarehouseCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    kind: str = "warehouse"
    capacity: float = Field(default=0, ge=0)
    unit: str = Field(default="т", max_length=16)
    is_active: bool = True


class WarehouseUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    kind: str | None = None
    capacity: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=16)
    is_active: bool | None = None
