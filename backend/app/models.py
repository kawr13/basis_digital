"""Модели кабинета.

Две группы таблиц:
  1. Аутентификация/доступ: User, Warehouse, UserWarehouseScope, RefreshToken, AuditLog,
     плюс AppSetting — настройки времени выполнения, которые меняет администратор.
  2. Витрина статистики (куда складывает данные коннектор 1С): Fact* — плоские строки,
     которые 1С отдаёт как есть. Кабинет их только читает.
"""
from __future__ import annotations

import enum
from datetime import datetime, date, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    """Наивный UTC — одинаково работает и на SQLite, и на PostgreSQL."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(str, enum.Enum):
    ADMIN = "admin"        # администратор кабинета
    TOP = "top"            # топ-менеджмент: все склады, полная картина
    MANAGER = "manager"    # менеджер: только назначенные ему склады


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.MANAGER)

    password_hash: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Связь с учётной записью 1С (для подстановки прав/фильтров из 1С)
    one_c_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Отпечаток ЭЦП — задел на авторизацию по КриптоПро (шаг 2)
    ecnp_thumbprint: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Право на документ «Постановка судов» — по той же логике, что и доступ к складам:
    # NULL = «по роли» (настройки vessels.*_roles), иначе явное для человека:
    # none | view | edit | manage. Диспетчеру можно дать постановку, не трогая его роль.
    vessel_right: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Личные вкладки кабинета — id разделов через запятую (см. services/sections.py):
    # statistics,warehouses,reports,vessels. NULL = личных ограничений нет, человек
    # наследует роль и глобальную настройку ui.hidden_sections. Пустым список быть
    # не может: учётка без вкладок — это гарантированный локаут.
    sections: Mapped[str | None] = mapped_column(String(128), nullable=True)

    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    scopes: Mapped[list["UserWarehouseScope"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def all_warehouses(self) -> bool:
        """Пустой scope = доступ ко всем складам (топ-менеджмент)."""
        return len(self.scopes) == 0

    @property
    def warehouse_ids(self) -> list[int]:
        return [s.warehouse_id for s in self.scopes]


class Warehouse(Base):
    """Склад/терминал из 1С. Код — идентификатор в 1С."""

    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32), default="warehouse")  # warehouse | terminal | cold
    capacity: Mapped[float] = mapped_column(Float, default=0)           # в unit
    unit: Mapped[str] = mapped_column(String(16), default="т")          # т | м³ | TEU
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class UserWarehouseScope(Base):
    __tablename__ = "user_warehouse_scopes"
    __table_args__ = (UniqueConstraint("user_id", "warehouse_id", name="uq_user_wh"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="CASCADE"))

    user: Mapped[User] = relationship(back_populates="scopes")
    warehouse: Mapped[Warehouse] = relationship(lazy="selectin")


class RefreshToken(Base):
    """Refresh-токены храним хэшем: утечка БД не даёт угнать сессию."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replaced_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_user_ts", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    login: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64))   # login.ok | login.fail | logout | ...
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Витрина статистики. Пишет коннектор 1С, читает кабинет.
# ---------------------------------------------------------------------------

class FactCargoBalance(Base):
    """Остатки грузов на складе по состоянию на срез."""

    __tablename__ = "fact_cargo_balance"
    __table_args__ = (
        Index("ix_cb_snap_wh", "snapshot_at", "warehouse_id"),
        UniqueConstraint("snapshot_at", "warehouse_id", "cargo_group", name="uq_cb_snap"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="CASCADE"))
    cargo_group: Mapped[str] = mapped_column(String(64))   # номенклатурная группа / вид груза
    qty: Mapped[float] = mapped_column(Float, default=0)   # мест / паллет
    weight_t: Mapped[float] = mapped_column(Float, default=0)
    overdue_weight_t: Mapped[float] = mapped_column(Float, default=0)  # просроченное хранение
    avg_age_days: Mapped[float] = mapped_column(Float, default=0)


class FactWarehouseCapacity(Base):
    """Ёмкость склада: занято / всего."""

    __tablename__ = "fact_warehouse_capacity"
    __table_args__ = (
        Index("ix_wc_snap_wh", "snapshot_at", "warehouse_id"),
        UniqueConstraint("snapshot_at", "warehouse_id", name="uq_wc_snap"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="CASCADE"))
    used: Mapped[float] = mapped_column(Float, default=0)
    capacity: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(16), default="т")


class FactProcessingDynamics(Base):
    """Динамика обработки по дням: сколько приняли / отгрузили / обработали."""

    __tablename__ = "fact_processing_dynamics"
    __table_args__ = (
        Index("ix_pd_day_wh", "bucket_date", "warehouse_id"),
        UniqueConstraint("bucket_date", "warehouse_id", name="uq_pd_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bucket_date: Mapped[date] = mapped_column(Date, index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="CASCADE"))
    inbound_t: Mapped[float] = mapped_column(Float, default=0)     # принято, т
    outbound_t: Mapped[float] = mapped_column(Float, default=0)    # отгружено, т
    processed_t: Mapped[float] = mapped_column(Float, default=0)   # обработано, т
    docs_count: Mapped[int] = mapped_column(Integer, default=0)    # документов обработки
    avg_dwell_hours: Mapped[float] = mapped_column(Float, default=0)


# ---------------------------------------------------------------------------
# «Отчёт диспетчера» — суточная смена порта.
#
# Витрина строится по документу «Отчёт диспетчера.xlsm» (он же придёт из 1С по
# HTTP, когда его заведут): одна строка FactDispatcherShift = одна смена
# (сутки × смена 08-20 / 20-08), остальное — строки-детали этой смены. Ключ
# любой таблицы — (report_date, shift, …), поэтому повторный импорт того же
# файла не плодит дубликаты, а обновляет цифры.
# ---------------------------------------------------------------------------


class FactDispatcherShift(Base):
    """Шапка смены: персонал, бригада, подрядчики, ричстакеры, диспетчер."""
    __tablename__ = "fact_dispatcher_shift"
    __table_args__ = (
        UniqueConstraint("report_date", "shift", name="uq_ds_shift"),
        Index("ix_ds_date", "report_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    shift: Mapped[str] = mapped_column(String(8))          # 08-20 | 20-08
    sheet: Mapped[str] = mapped_column(String(64), default="")
    dispatcher: Mapped[str] = mapped_column(String(128), default="")

    crew_no: Mapped[int | None] = mapped_column(Integer, nullable=True)   # № бригады
    crew_present: Mapped[float] = mapped_column(Float, default=0)         # вышло основной бригадой
    crew_day_off: Mapped[float] = mapped_column(Float, default=0)         # вышло в выходной день
    crew_staff: Mapped[float] = mapped_column(Float, default=0)           # штат бригады
    # Подрядчики: [{"name": "ПортСервис", "count": 10}, …] — их в отчёте несколько
    # строк, а отдельная таблица ради двух чисел не нужна.
    contractors_json: Mapped[str] = mapped_column(Text, default="[]")

    present_total: Mapped[float] = mapped_column(Float, default=0)        # всего вышло, с подрядчиками
    staff_total: Mapped[float] = mapped_column(Float, default=0)          # штат
    sick: Mapped[float] = mapped_column(Float, default=0)
    injury: Mapped[float] = mapped_column(Float, default=0)               # с/с
    vacation: Mapped[float] = mapped_column(Float, default=0)
    other_absent: Mapped[float] = mapped_column(Float, default=0)         # донор / н-в
    truant: Mapped[float] = mapped_column(Float, default=0)
    day_off: Mapped[float] = mapped_column(Float, default=0)
    mob: Mapped[float] = mapped_column(Float, default=0)
    training: Mapped[float] = mapped_column(Float, default=0)

    # Ричстакеры: исправно / ремонт на начало и конец смены
    rs_ok_start: Mapped[float] = mapped_column(Float, default=0)
    rs_repair_start: Mapped[float] = mapped_column(Float, default=0)
    rs_ok_end: Mapped[float] = mapped_column(Float, default=0)
    rs_repair_end: Mapped[float] = mapped_column(Float, default=0)
    rs_total: Mapped[float] = mapped_column(Float, default=0)

    source: Mapped[str] = mapped_column(String(32), default="xlsm")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FactVesselOperation(Base):
    """Строка обработки судна за смену: план/факт, производительность, потери."""
    __tablename__ = "fact_vessel_operation"
    # Уникального ключа у строки нет: в одном листе одно и то же судно может
    # стоять на двух причалах. Строка витрины = строка листа, повторный импорт
    # сначала удаляет строки смены, потом кладёт их заново (см. apply_snapshot).
    __table_args__ = (
        Index("ix_vo_date", "report_date", "shift"),
        Index("ix_vo_vessel", "vessel"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    shift: Mapped[str] = mapped_column(String(8))
    berth: Mapped[str] = mapped_column(String(32), default="")   # РП - 1/2
    vessel: Mapped[str] = mapped_column(String(128))
    cargo_group: Mapped[str] = mapped_column(String(64), default="")

    plan_unload_t: Mapped[float] = mapped_column(Float, default=0)
    plan_load_t: Mapped[float] = mapped_column(Float, default=0)
    plan_total_t: Mapped[float] = mapped_column(Float, default=0)
    plan_shift_t: Mapped[float] = mapped_column(Float, default=0)   # план на смену
    fact_shift_t: Mapped[float] = mapped_column(Float, default=0)   # факт за смену, т
    on_board_t: Mapped[float] = mapped_column(Float, default=0)     # на борту / выгружено накопительно
    remaining_t: Mapped[float] = mapped_column(Float, default=0)    # остаток на борту

    productivity_tph: Mapped[float] = mapped_column(Float, default=0)
    moves: Mapped[int] = mapped_column(Integer, default=0)          # кол-во кранов/ходов
    # В отчёте W = сменное время (11 ч), X = потери, Y = W - X = машинное время.
    # Производительность в отчёте считается как факт / Y, поэтому держим здесь:
    # без Y нельзя ni посчитать средний темп, ni проверить чужие цифры.
    shift_hours: Mapped[float] = mapped_column(Float, default=0)
    downtime_hours: Mapped[float] = mapped_column(Float, default=0)
    machine_hours: Mapped[float] = mapped_column(Float, default=0)

    plan_finish_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    berthed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="xlsm")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FactRailOperation(Base):
    """Обработка вагонов за смену, строка = груз."""
    __tablename__ = "fact_rail_operation"
    __table_args__ = (Index("ix_ro_date", "report_date", "shift"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    shift: Mapped[str] = mapped_column(String(8))
    cargo_group: Mapped[str] = mapped_column(String(64))
    station_start: Mapped[float] = mapped_column(Float, default=0)   # на станции на начало смены
    port_start_loaded: Mapped[float] = mapped_column(Float, default=0)
    port_start_empty: Mapped[float] = mapped_column(Float, default=0)
    processed: Mapped[float] = mapped_column(Float, default=0)       # обработано вагонов
    port_end_loaded: Mapped[float] = mapped_column(Float, default=0)
    port_end_empty: Mapped[float] = mapped_column(Float, default=0)
    source: Mapped[str] = mapped_column(String(32), default="xlsm")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FactTruckOperation(Base):
    """Обработка автомашин за смену, строка = груз; пять направлений работ."""
    __tablename__ = "fact_truck_operation"
    __table_args__ = (Index("ix_to_date", "report_date", "shift"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    shift: Mapped[str] = mapped_column(String(8))
    cargo_group: Mapped[str] = mapped_column(String(64))
    imp_unload: Mapped[float] = mapped_column(Float, default=0)     # принял / выгрузка, импорт
    exp_unload: Mapped[float] = mapped_column(Float, default=0)     # растарка / выгрузка, экспорт
    imp_load: Mapped[float] = mapped_column(Float, default=0)       # затарка / погрузка, импорт
    exp_load: Mapped[float] = mapped_column(Float, default=0)       # затарка / погрузка, экспорт
    direct_vessel: Mapped[float] = mapped_column(Float, default=0)  # с т/х в а/м, прямой вариант
    total: Mapped[float] = mapped_column(Float, default=0)
    source: Mapped[str] = mapped_column(String(32), default="xlsm")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FactVesselArrival(Base):
    """«Суда на подходе»: план захода, который диспетчер держит в отчёте."""
    __tablename__ = "fact_vessel_arrival"
    __table_args__ = (
        Index("ix_va_date", "report_date", "shift"),
        Index("ix_va_eta", "eta"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    shift: Mapped[str] = mapped_column(String(8))
    eta: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    vessel: Mapped[str] = mapped_column(String(128))
    berth: Mapped[str] = mapped_column(String(32), default="")
    work_kind: Mapped[str] = mapped_column(String(32), default="")
    cargo_group: Mapped[str] = mapped_column(String(64), default="")
    plan_t: Mapped[float] = mapped_column(Float, default=0)
    plan_raw: Mapped[str] = mapped_column(String(64), default="")   # «129+130», «87+0/3539 тн»
    agent: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(32), default="xlsm")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FactRailArrival(Base):
    """«Ж/Д на подходе»: плановые поезда с грузом на станцию порта."""
    __tablename__ = "fact_rail_arrival"
    __table_args__ = (Index("ix_ra_date", "report_date", "shift"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    shift: Mapped[str] = mapped_column(String(8))
    station: Mapped[str] = mapped_column(String(64), default="")
    cargo_group: Mapped[str] = mapped_column(String(64), default="")
    cars: Mapped[float] = mapped_column(Float, default=0)
    work_kind: Mapped[str] = mapped_column(String(16), default="")
    wagon_type: Mapped[str] = mapped_column(String(32), default="")
    train_index: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(String(128), default="")
    source: Mapped[str] = mapped_column(String(32), default="xlsm")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FactRailDeparture(Base):
    """«ВЫВОД» листа: выводка вагонов из порта — план, факт и кто принял."""
    __tablename__ = "fact_rail_departure"
    __table_args__ = (Index("ix_rd_date", "report_date", "shift"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    shift: Mapped[str] = mapped_column(String(8))
    row_no: Mapped[int] = mapped_column(Integer, default=0)      # строка листа, для порядка
    move_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    plan_time: Mapped[str] = mapped_column(String(8), default="")   # «23:30» — план
    semi_wagons: Mapped[float] = mapped_column(Float, default=0)     # C — полувагоны
    covered: Mapped[float] = mapped_column(Float, default=0)         # D — крытые
    flatcars: Mapped[float] = mapped_column(Float, default=0)        # G — к/платформа
    total: Mapped[float] = mapped_column(Float, default=0)           # H — итого
    accepted_by: Mapped[str] = mapped_column(String(64), default="")  # I — кто принял
    fact_cars: Mapped[float] = mapped_column(Float, default=0)       # J — выведено по факту
    fact_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fact_time: Mapped[str] = mapped_column(String(8), default="")    # L — время по факту
    source: Mapped[str] = mapped_column(String(32), default="xlsm")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FactRailDelivery(Base):
    """«ВВОД» листа: постановка вагонов в порт, гружёные/порожние по типам."""
    __tablename__ = "fact_rail_delivery"
    __table_args__ = (Index("ix_rl_date", "report_date", "shift"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    shift: Mapped[str] = mapped_column(String(8))
    row_no: Mapped[int] = mapped_column(Integer, default=0)
    move_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    plan_time: Mapped[str] = mapped_column(String(8), default="")     # B — время план/факт
    settle_time: Mapped[str] = mapped_column(String(8), default="")   # C — время расстановки
    load_semi: Mapped[float] = mapped_column(Float, default=0)        # D — гружёные полувагоны
    load_covered: Mapped[float] = mapped_column(Float, default=0)     # G — крытые
    load_flat: Mapped[float] = mapped_column(Float, default=0)        # H — платформы
    empty_semi: Mapped[float] = mapped_column(Float, default=0)       # I — порожние полувагоны
    empty_covered: Mapped[float] = mapped_column(Float, default=0)    # J — крытые
    empty_flat: Mapped[float] = mapped_column(Float, default=0)       # K — платформы
    total: Mapped[float] = mapped_column(Float, default=0)            # L — итого
    source: Mapped[str] = mapped_column(String(32), default="xlsm")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FactPlatformBalance(Base):
    """«ПЛАТФОРМЫ, выведенные из аренды»: остаток платформ по владельцам (СМАРТ, ИТК)."""
    __tablename__ = "fact_platform_balance"
    __table_args__ = (Index("ix_pb_date", "report_date", "shift"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    shift: Mapped[str] = mapped_column(String(8))
    owner: Mapped[str] = mapped_column(String(64))
    total_cars: Mapped[float] = mapped_column(Float, default=0)       # всего
    remaining: Mapped[float] = mapped_column(Float, default=0)        # остаток
    terminal: Mapped[float] = mapped_column(Float, default=0)         # на терминале
    sent: Mapped[float] = mapped_column(Float, default=0)             # отправлено
    arrived_loaded: Mapped[float] = mapped_column(Float, default=0)   # прибыло гружёными
    source: Mapped[str] = mapped_column(String(32), default="xlsm")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SyncRun(Base):
    """Журнал загрузок из 1С — чтобы на дашборде честно писать «данные на ...»."""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="mock")
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | error
    rows: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserPreference(Base):
    """Персональные настройки интерфейса: раскладка дашборда, фильтры отчётов.

    value храним JSON-строкой: набор виджетов и их размеров со временем меняется,
    а мигрировать таблицу под каждое изменение не нужно.
    """

    __tablename__ = "user_preferences"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AppSetting(Base):
    """Настройки времени выполнения, которые администратор меняет в UI.

    Значение храним JSON-строкой: типы разные (строка, число, bool), а таблицу
    под каждый новый ключ мигрировать не нужно. Приоритет чтения:
    запись здесь → значение из .env → DEFAULTS в app/services/app_settings.py.
    """

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)          # JSON: 30, true, "http"
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)  # id администратора
    # Внешний ключ не ставим специально: учётку администратора могут удалить,
    # а историю «кто поменял настройку» терять нельзя.


# ---------------------------------------------------------------------------
# «Постановка судов» — ручной планировочный документ.
#
# В отличие от витрины статистики эти строки приходят НЕ из 1С: их заводит
# диспетчер руками. Поэтому они живут в отдельных таблицах и никогда не
# перезаписываются синхронизацией.
#
# Время дробится так же, как в бумажном плане: сутки = 4 слота по 6 часов
# (00-06, 06-12, 12-18, 18-00). starts_at/ends_at хранят НАЧАЛО первого и
# последнего занятого слота, то есть границы включительно.
# ---------------------------------------------------------------------------

class Berth(Base):
    """Причал: код, осадка и длина (метраж) в метрах."""

    __tablename__ = "berths"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)   # РП-1
    depth: Mapped[float | None] = mapped_column(Float, nullable=True)        # осадка, м
    length_m: Mapped[int | None] = mapped_column(Integer, nullable=True)     # метраж, м
    lanes: Mapped[int] = mapped_column(Integer, default=1)                   # сколько судов одновременно
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    placements: Mapped[list["VesselPlacement"]] = relationship(back_populates="berth")


class VesselPlacement(Base):
    """Одна строка плана: судно у причала с … по … (включительно)."""

    __tablename__ = "vessel_placements"
    __table_args__ = (
        Index("ix_placement_period", "starts_at", "ends_at"),
        Index("ix_placement_berth", "berth_id", "lane"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    berth_id: Mapped[int] = mapped_column(ForeignKey("berths.id", ondelete="RESTRICT"), index=True)
    # Устарело: раньше причал делился на «дорожки» (1..N). Теперь шкала другая —
    # длина причала сверху вниз, 10 м на ячейку. Колонку оставили, старые строки
    # никуда не деваются; на проверку конфликта она больше не влияет.
    lane: Mapped[int] = mapped_column(Integer, default=1)

    # Длина судна (м) и отметка, от которой оно швартуется (м от начала причала).
    # length_m = NULL — длина неизвестна: считаем, что судно занимает весь причал,
    # и оно конфликтует со всем, что встанет рядом по времени. Так честнее, чем
    # молча разрешить вторую постановку.
    length_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offset_m: Mapped[int] = mapped_column(Integer, default=0)

    vessel: Mapped[str] = mapped_column(String(128))         # CROWN SAPPHIRE
    volume: Mapped[str | None] = mapped_column(String(64), nullable=True)   # «5 500», «(100+40) + 4 000»
    cargo: Mapped[str | None] = mapped_column(String(64), nullable=True)    # БШ, БАНАНЫ-ПАЛЛЕТ, МЛК (БКК)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)           # сборник, (КМП-ЮМФ)…
    # цвет полосы вручную (#rrggbb); пусто — красим по грузу (cargoColor)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)

    starts_at: Mapped[datetime] = mapped_column(DateTime)    # начало первого слота
    ends_at: Mapped[datetime] = mapped_column(DateTime)      # начало последнего слота

    # Перешвартовка: строка — копия постановки `moved_from_id`. Фронт рисует
    # стрелку «оригинал → копия»; удалили оригинал — стрелка гаснет (SET NULL).
    moved_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("vessel_placements.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Судно сейчас физически на другом терминале — на плане такая полоса
    # в красной рамке. Без рамки — судно в проливе (идёт к нам).
    at_other_terminal: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    berth: Mapped[Berth] = relationship(back_populates="placements")
