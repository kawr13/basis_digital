"""Pydantic-схемы запросов/ответов."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, computed_field, Field

from .services.vessels import length_cells as berth_length_cells


# ------------------------------------------------------------------ auth ----

class LoginRequest(BaseModel):
    login: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class VesselRightsOut(BaseModel):
    """Права на «Постановку судов» в том виде, в каком их посчитал бэкенд."""

    view: bool
    edit: bool
    delete: bool


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
    # право на «Постановку судов»: пусто = по роли, иначе none|view|edit|manage
    vessel_right: str | None = None
    # личные вкладки: null = ограничений нет (наследуется роль), иначе список id
    sections: list[str] | None = None
    # посчитанные права: фронт прячет вкладку и кнопку по ним, не дожидаясь
    # /api/settings/public — иначе запрещённая вкладка успевает мигнуть
    vessel_rights: VesselRightsOut = VesselRightsOut(view=False, edit=False, delete=False)


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
    # "" / null = по роли; иначе none | view | edit | manage
    vessel_right: str | None = None
    # null = личных ограничений нет; пустой список запрещён — это локаут
    sections: list[str] | None = None


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    warehouse_ids: list[int] | None = None
    vessel_right: str | None = None
    # null снимает личные вкладки (возврат «по роли»); пустой список — 400
    sections: list[str] | None = None


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


# -------------------------------------------------- постановка судов ----


class BerthOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    depth: float | None
    length_m: int | None
    lanes: int
    sort_order: int
    is_active: bool

    @computed_field  # шкала длины: фронт не дублирует правило «10 м на ячейку»
    @property
    def length_cells(self) -> int:
        return berth_length_cells(self)


class BerthUpsert(BaseModel):
    # код печатают в плане и ищут глазами: без пробелов и пунктуации он сливается
    code: str = Field(min_length=2, max_length=32,
                      pattern=r"^[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё .\-/]*$")
    depth: float | None = Field(default=None, ge=0, le=100)
    length_m: int | None = Field(default=None, ge=1, le=2000)
    lanes: int = Field(default=1, ge=1, le=12)
    sort_order: int = 0
    is_active: bool | None = None


class PlacementIn(BaseModel):
    """Строка плана. Границы — начало слота (00/06/12/18), включительно."""

    berth_id: int
    # устарело: шкала внутри причала теперь метровая (offset_m + length_m)
    lane: int = Field(default=1, ge=1, le=12)
    # длина судна и отметка швартовки в метрах от начала причала
    length_m: int | None = Field(default=None, ge=1, le=500)
    offset_m: int = Field(default=0, ge=0, le=5000)
    vessel: str = Field(min_length=2, max_length=128)
    volume: str | None = Field(default=None, max_length=64)
    cargo: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=1000)
    starts_at: datetime
    ends_at: datetime
    # перешвартовка: копия, созданная из этой постановки (стрелка на фронте)
    moved_from_id: int | None = None
    # судно сейчас на другом терминале (красная рамка); без флага — в проливе
    at_other_terminal: bool = False
    # цвет полосы вручную; пусто — автоматом по грузу
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class PlacementUpdate(BaseModel):
    berth_id: int | None = None
    lane: int | None = Field(default=None, ge=1, le=12)
    length_m: int | None = Field(default=None, ge=1, le=500)
    offset_m: int | None = Field(default=None, ge=0, le=5000)
    vessel: str | None = Field(default=None, min_length=2, max_length=128)
    volume: str | None = Field(default=None, max_length=64)
    cargo: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=1000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    at_other_terminal: bool | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class PlacementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    berth_id: int
    lane: int
    length_m: int | None
    offset_m: int
    vessel: str
    volume: str | None
    cargo: str | None
    note: str | None
    starts_at: datetime
    ends_at: datetime
    moved_from_id: int | None
    at_other_terminal: bool
    color: str | None
    # «действительный» вид при чтении (в БД не хранится): дата перестановки
    # наступила — копия обычная (committed), оригинал обрывается на старте копии
    committed: bool = False
    effective_ends_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
