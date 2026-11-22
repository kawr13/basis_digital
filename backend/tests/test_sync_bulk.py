"""Пакетная укладка срезов 1С в витрину (TASKS2.md, п. 17).

Почему проверки именно в таком виде: старый код делал SELECT на каждую строку
среза — здесь проверяется ПОВЕДЕНИЕ (что лежит в базе после применения среза,
в том числе повторного) и «пакетность» как число round-trip: счётчик на
`before_cursor_execute` обязан показывать константу от числа разделов, а не от
числа строк. Данные — свои коды складов и сутки 2099 года, убираем за собой:
витрина на весь прогон одна.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable

import pytest
from sqlalchemy import delete, event, func, select

from app.db import SessionLocal, engine
from app.models import (
    FactCargoBalance, FactDispatcherShift, FactPlatformBalance, FactProcessingDynamics,
    FactRailArrival, FactRailDelivery, FactRailDeparture, FactRailOperation,
    FactTruckOperation, FactVesselArrival, FactVesselOperation,
    FactWarehouseCapacity, Warehouse,
)
from app.onec.base import (
    CapacityRec, CargoRec, DynamicsRec, Snapshot, WarehouseRec,
)
from app.onec.dispatcher import (
    DispatcherSnapshot, PlatformRec, RailArrivalRec, RailDeliveryRec, RailDepartureRec,
    RailOpRec, ShiftRec, TruckOpRec, VesselArrivalRec, VesselOpRec,
    apply_snapshot as apply_dispatcher,
)
from app.onec.sync import apply_snapshot

SNAP_AT = datetime(2099, 6, 1, 12, 0)
CODES = [f"SBULK{i:02d}" for i in range(10)]
DAY_FROM = date(2099, 5, 1)
DISP_DAYS = [date(2099, 7, 1) + timedelta(days=i) for i in range(5)]
DISPATCHER_TABLES = (FactDispatcherShift, FactVesselOperation, FactRailOperation,
                     FactTruckOperation, FactVesselArrival, FactRailArrival,
                     FactRailDeparture, FactRailDelivery, FactPlatformBalance)


def _purge(db) -> None:
    """Убрать за тестом: витрина на весь прогон одна, мусор 2099 года никому не нужен."""
    wh = select(Warehouse.id).where(Warehouse.code.in_(CODES))
    for model in (FactCargoBalance, FactWarehouseCapacity, FactProcessingDynamics):
        db.execute(delete(model).where(model.warehouse_id.in_(wh)))
    db.execute(delete(Warehouse).where(Warehouse.code.in_(CODES)))
    for model in DISPATCHER_TABLES:
        db.execute(delete(model).where(model.report_date.in_(DISP_DAYS)))
    db.commit()


@pytest.fixture(scope="module", autouse=True)
def _schema():
    """Файл не дёргает HTTP — схему поднимает сам, чтобы прогоняться и один, и в наборе."""
    from app.db import init_db

    init_db()


@pytest.fixture()
def db():
    session = SessionLocal()
    _purge(session)
    try:
        yield session
    finally:
        _purge(session)
        session.close()


def _count_statements(fn: Callable[[], None]) -> int:
    """Сколько команд реально ушло в базу: round-trip считается один раз на execute."""
    calls: list[str] = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        calls.append(statement)

    event.listen(engine, "before_cursor_execute", _before)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    return len(calls)


def _snapshot(weight: float = 100.0, inbound: float = 1.0) -> Snapshot:
    """2 склада, по строке остатков/ёмкости на склад и 4 дня динамики на склад."""
    codes = CODES[:2]
    return Snapshot(
        snapshot_at=SNAP_AT,
        warehouses=[WarehouseRec(code=c, name=f"Тестовый {c}", capacity=1000.0) for c in codes],
        cargo=[CargoRec(warehouse_code=codes[0], cargo_group="зерно", qty=1.0, weight_t=weight),
               CargoRec(warehouse_code=codes[1], cargo_group="металл", qty=2.0, weight_t=20.0)],
        capacity=[CapacityRec(warehouse_code=c, used=10.0, capacity=100.0) for c in codes],
        dynamics=[DynamicsRec(warehouse_code=c, bucket_date=DAY_FROM + timedelta(days=i),
                              inbound_t=inbound, processed_t=2.0)
                  for c in codes for i in range(4)],
    )


def _big_snapshot(processed: float = 1.0) -> Snapshot:
    """Срез месячной давности порта в миниатюре: 10 складов × 20 дней = 200 строк динамики."""
    return Snapshot(
        snapshot_at=SNAP_AT,
        warehouses=[WarehouseRec(code=c, name=f"Тестовый {c}") for c in CODES],
        cargo=[CargoRec(warehouse_code=c, cargo_group=g, weight_t=1.0)
               for c in CODES for g in ("зерно", "металл", "удобрения")],
        capacity=[CapacityRec(warehouse_code=c, used=1.0, capacity=2.0) for c in CODES],
        dynamics=[DynamicsRec(warehouse_code=c, bucket_date=DAY_FROM + timedelta(days=i),
                              processed_t=processed)
                  for c in CODES for i in range(20)],
    )


def _rows_of(db, model, wh_ids: set[int]) -> list:
    return db.scalars(select(model).where(model.warehouse_id.in_(wh_ids))).all()


def _wh_ids(db) -> set[int]:
    return set(db.scalars(select(Warehouse.id).where(Warehouse.code.in_(CODES))))


# ------------------------------------------------------------------ витрина 1С ----

def test_apply_twice_updates_in_place(db):
    """Повторный срез обновляет те же строки, а не множит их."""
    counts1 = apply_snapshot(db, _snapshot(weight=100.0, inbound=1.0))
    db.commit()
    assert counts1 == {"warehouses": 2, "cargo": 2, "capacity": 2, "dynamics": 8}

    counts2 = apply_snapshot(db, _snapshot(weight=123.0, inbound=9.0))
    db.commit()
    assert counts2 == counts1  # счётчики — про строки среза, их тот же набор

    ids = _wh_ids(db)
    cargo = _rows_of(db, FactCargoBalance, ids)
    assert len(cargo) == 2
    grain = next(r for r in cargo if r.cargo_group == "зерно")
    assert grain.weight_t == 123.0

    capacity = _rows_of(db, FactWarehouseCapacity, ids)
    assert len(capacity) == 2 and all(r.used == 10.0 for r in capacity)

    dynamics = _rows_of(db, FactProcessingDynamics, ids)
    assert len(dynamics) == 8
    assert all(r.inbound_t == 9.0 for r in dynamics)


def test_unknown_warehouse_skipped_new_created(db):
    """Склад вне `snap.warehouses` — строка не кладывается; склад из среза заводится."""
    snap = _snapshot()
    snap.cargo.append(CargoRec(warehouse_code="SBULK-НЕТ", cargo_group="соль", weight_t=7.0))
    counts = apply_snapshot(db, snap)
    db.commit()
    assert counts["cargo"] == 2  # неизвестный склад — не строка витрины
    assert db.scalar(select(func.count()).select_from(FactCargoBalance)
                     .where(FactCargoBalance.snapshot_at == SNAP_AT)) == 2
    wh = db.scalar(select(Warehouse).where(Warehouse.code == CODES[0]))
    assert wh is not None and wh.name == f"Тестовый {CODES[0]}" and wh.is_active


def test_duplicate_keys_in_one_snapshot(db):
    """1С умеет отдать два дубля одного ключа в одном срезе — строка обязана остаться одна."""
    snap = Snapshot(
        snapshot_at=SNAP_AT,
        warehouses=[WarehouseRec(code=CODES[0], name="Тестовый дубль")],
        cargo=[CargoRec(warehouse_code=CODES[0], cargo_group="зерно", weight_t=1.0),
               CargoRec(warehouse_code=CODES[0], cargo_group="зерно", weight_t=2.0)],
        capacity=[CapacityRec(warehouse_code=CODES[0], used=1.0),
                  CapacityRec(warehouse_code=CODES[0], used=2.0)],
        dynamics=[DynamicsRec(warehouse_code=CODES[0], bucket_date=DAY_FROM, processed_t=1.0),
                  DynamicsRec(warehouse_code=CODES[0], bucket_date=DAY_FROM, processed_t=2.0)],
    )
    counts = apply_snapshot(db, snap)
    db.commit()
    assert counts == {"warehouses": 1, "cargo": 2, "capacity": 2, "dynamics": 2}

    ids = _wh_ids(db)
    cargo = _rows_of(db, FactCargoBalance, ids)
    assert len(cargo) == 1 and cargo[0].weight_t == 2.0
    capacity = _rows_of(db, FactWarehouseCapacity, ids)
    assert len(capacity) == 1 and capacity[0].used == 2.0
    dynamics = _rows_of(db, FactProcessingDynamics, ids)
    assert len(dynamics) == 1 and dynamics[0].processed_t == 2.0


def test_roundtrips_are_constant_not_per_row(db):
    """200 строк динамики не имеют права стоить 200 round-trip: потолок — константа от разделов."""
    def first() -> None:
        counts = apply_snapshot(db, _big_snapshot())
        assert counts["dynamics"] == 200 and counts["cargo"] == 30
        db.commit()

    n_first = _count_statements(first)
    assert n_first < 40, f"первый срез: {n_first} команд — кто-то вернулся к SELECT на строку"

    def second() -> None:
        counts = apply_snapshot(db, _big_snapshot(processed=99.0))
        assert counts["dynamics"] == 200
        db.commit()

    n_second = _count_statements(second)
    assert n_second < 40, f"повторный срез: {n_second} команд"
    assert db.scalar(select(func.count()).select_from(FactProcessingDynamics)
                     .where(FactProcessingDynamics.warehouse_id.in_(_wh_ids(db)))) == 200


# --------------------------------------------------------------- отчёт диспетчера ----

def _dispatcher_snapshot(fact: float = 100.0, day: date | None = None) -> DispatcherSnapshot:
    shift = ShiftRec(
        report_date=day or DISP_DAYS[0], shift="08-20", dispatcher="Иванов", present_total=10.0,
        vessels=[VesselOpRec(berth="РП - 1/2", vessel="СУДНО БУЛК", cargo_group="зерно",
                             fact_shift_t=fact)],
        rails=[RailOpRec(cargo_group="зерно", processed=5.0)],
        trucks=[TruckOpRec(cargo_group="зерно", total=7.0)],
        arrivals=[VesselArrivalRec(vessel="СУДНО СЛЕД", plan_t=9.0)],
        rail_arrivals=[RailArrivalRec(station="Котовск", cargo_group="зерно", cars=12.0)],
        rail_out=[RailDepartureRec(row_no=1, total=4.0)],
        rail_in=[RailDeliveryRec(row_no=1, total=6.0)],
        platforms=[PlatformRec(owner="СМАРТ", total_cars=3.0)],
    )
    return DispatcherSnapshot(shifts=[shift], source="bulk-test")


def test_dispatcher_apply_twice_updates_in_place(db):
    """Повторный импорт того же листа: ни одна таблица витрины не приросла, цифры новые."""
    expected = {"shift": 1, "vessel": 1, "rail": 1, "truck": 1, "arrival": 1,
                "rail_arrival": 1, "rail_out": 1, "rail_in": 1, "platform": 1}
    day = DISP_DAYS[0]
    assert apply_dispatcher(db, _dispatcher_snapshot(fact=100.0, day=day)) == expected
    assert apply_dispatcher(db, _dispatcher_snapshot(fact=250.0, day=day)) == expected

    for model in DISPATCHER_TABLES:
        assert db.scalar(select(func.count()).select_from(model)
                         .where(model.report_date == day)) == 1, model.__tablename__
    vessel = db.scalars(select(FactVesselOperation)
                        .where(FactVesselOperation.report_date == day)).one()
    assert vessel.fact_shift_t == 250.0


def test_dispatcher_keeps_shifts_outside_snapshot(db):
    """Чистка деталей — строго по парам (сутки, смена) из среза: ночная смена живёт отдельно."""
    day = DISP_DAYS[1]
    db.add(FactDispatcherShift(report_date=day, shift="20-08", dispatcher="Ночной"))
    db.add(FactVesselOperation(report_date=day, shift="20-08", vessel="НОЧНОЕ СУДНО"))
    db.commit()

    apply_dispatcher(db, _dispatcher_snapshot(day=day))  # в срезе только «08-20»

    assert db.scalar(select(func.count()).select_from(FactDispatcherShift)
                     .where(FactDispatcherShift.report_date == day,
                            FactDispatcherShift.shift == "20-08")) == 1
    assert db.scalar(select(func.count()).select_from(FactVesselOperation)
                     .where(FactVesselOperation.report_date == day,
                            FactVesselOperation.shift == "20-08")) == 1


def test_dispatcher_roundtrips_are_constant(db):
    """200 судовых строк за пять смен — то же доказательство пакетности, что и для синка."""
    snap = DispatcherSnapshot(
        shifts=[ShiftRec(report_date=day, shift="08-20",
                         vessels=[VesselOpRec(vessel=f"СУДНО {day:%d}-{i}", fact_shift_t=float(i))
                                  for i in range(40)])
                for day in DISP_DAYS],
        source="bulk-test")

    def apply() -> None:
        counts = apply_dispatcher(db, snap)
        assert counts["shift"] == 5 and counts["vessel"] == 200

    n = _count_statements(apply)
    assert n < 40, f"импорт отчёта: {n} команд — детали пишутся не пачкой"
