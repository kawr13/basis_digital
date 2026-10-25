"""Контракт данных «Отчёт диспетчера» и складывание их в витрину.

Почему отдельный контракт, а не сразу строки БД: источник сегодня — файл
`Отчет диспетчера.xlsm` (его разбирает `dispatcher_xlsm.py`), завтра — HTTP-сервис
1С, который отдаст ровно эти же плоские записи. Разбор файла и запись в витрину
разделены, поэтому для 1С останется только отдать `DispatcherSnapshot`.

Витрина идемпотентна: ключ каждой таблицы — (сутки, смена, …), повторный импорт
обновляет цифры, а не плодит дубликаты (см. `apply_snapshot`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    FactDispatcherShift, FactPlatformBalance, FactRailArrival, FactRailDelivery,
    FactRailDeparture, FactRailOperation, FactTruckOperation,
    FactVesselArrival, FactVesselOperation,
)

SHIFTS = ("08-20", "20-08")


# --------------------------------------------------------------------- записи ----

@dataclass
class VesselOpRec:
    berth: str = ""
    vessel: str = ""
    cargo_group: str = ""
    plan_unload_t: float = 0.0
    plan_load_t: float = 0.0
    plan_total_t: float = 0.0
    plan_shift_t: float = 0.0
    fact_shift_t: float = 0.0
    on_board_t: float = 0.0
    remaining_t: float = 0.0
    productivity_tph: float = 0.0
    moves: int = 0
    shift_hours: float = 0.0
    downtime_hours: float = 0.0
    machine_hours: float = 0.0
    plan_finish_at: datetime | None = None
    berthed_at: datetime | None = None
    started_at: datetime | None = None
    note: str = ""


@dataclass
class RailOpRec:
    cargo_group: str = ""
    station_start: float = 0.0
    port_start_loaded: float = 0.0
    port_start_empty: float = 0.0
    processed: float = 0.0
    port_end_loaded: float = 0.0
    port_end_empty: float = 0.0


@dataclass
class TruckOpRec:
    cargo_group: str = ""
    imp_unload: float = 0.0
    exp_unload: float = 0.0
    imp_load: float = 0.0
    exp_load: float = 0.0
    direct_vessel: float = 0.0
    total: float = 0.0


@dataclass
class VesselArrivalRec:
    eta: datetime | None = None
    vessel: str = ""
    berth: str = ""
    work_kind: str = ""
    cargo_group: str = ""
    plan_t: float = 0.0
    plan_raw: str = ""
    agent: str = ""


@dataclass
class RailArrivalRec:
    station: str = ""
    cargo_group: str = ""
    cars: float = 0.0
    work_kind: str = ""
    wagon_type: str = ""
    train_index: str = ""
    note: str = ""


@dataclass
class RailDepartureRec:
    """Строка блока «ВЫВОД»: выводка вагонов из порта."""
    row_no: int = 0
    move_date: date | None = None
    plan_time: str = ""
    semi_wagons: float = 0.0
    covered: float = 0.0
    flatcars: float = 0.0
    total: float = 0.0
    accepted_by: str = ""
    fact_cars: float = 0.0
    fact_date: date | None = None
    fact_time: str = ""


@dataclass
class RailDeliveryRec:
    """Строка блока «ВВОД»: постановка вагонов в порт."""
    row_no: int = 0
    move_date: date | None = None
    plan_time: str = ""
    settle_time: str = ""
    load_semi: float = 0.0
    load_covered: float = 0.0
    load_flat: float = 0.0
    empty_semi: float = 0.0
    empty_covered: float = 0.0
    empty_flat: float = 0.0
    total: float = 0.0


@dataclass
class PlatformRec:
    """Строка блока «платформы, выведенные из аренды» — один владелец."""
    owner: str = ""
    total_cars: float = 0.0
    remaining: float = 0.0
    terminal: float = 0.0
    sent: float = 0.0
    arrived_loaded: float = 0.0


@dataclass
class ShiftRec:
    """Одна смена порта = один лист отчёта диспетчера."""

    report_date: date
    shift: str                       # 08-20 | 20-08
    sheet: str = ""
    dispatcher: str = ""
    crew_no: int | None = None
    crew_present: float = 0.0
    crew_day_off: float = 0.0
    crew_staff: float = 0.0
    contractors: list[dict[str, Any]] = field(default_factory=list)
    present_total: float = 0.0
    staff_total: float = 0.0
    sick: float = 0.0
    injury: float = 0.0
    vacation: float = 0.0
    other_absent: float = 0.0
    truant: float = 0.0
    day_off: float = 0.0
    mob: float = 0.0
    training: float = 0.0
    rs_ok_start: float = 0.0
    rs_repair_start: float = 0.0
    rs_ok_end: float = 0.0
    rs_repair_end: float = 0.0
    rs_total: float = 0.0
    vessels: list[VesselOpRec] = field(default_factory=list)
    rails: list[RailOpRec] = field(default_factory=list)
    trucks: list[TruckOpRec] = field(default_factory=list)
    arrivals: list[VesselArrivalRec] = field(default_factory=list)
    rail_arrivals: list[RailArrivalRec] = field(default_factory=list)
    rail_out: list[RailDepartureRec] = field(default_factory=list)
    rail_in: list[RailDeliveryRec] = field(default_factory=list)
    platforms: list[PlatformRec] = field(default_factory=list)


@dataclass
class DispatcherSnapshot:
    """Весь отчёт (все листы) одним списком смен — то, что отдаёт источник."""

    shifts: list[ShiftRec] = field(default_factory=list)
    source: str = "xlsm"

    def keys(self) -> list[tuple[date, str]]:
        return [(s.report_date, s.shift) for s in self.shifts]


# ------------------------------------------------------------------- запись ----

def _upsert(db: Session, model, keys: dict, values: dict) -> None:
    """Одна строка по ключу: нет — вставить, есть — обновить значениями."""
    row = db.scalar(select(model).filter_by(**keys))
    if row is None:
        db.add(model(**keys, **values))
    else:
        for name, value in values.items():
            setattr(row, name, value)


def _shift_values(rec: ShiftRec, source: str) -> tuple[dict, dict]:
    keys = {"report_date": rec.report_date, "shift": rec.shift}
    values = {
        "sheet": rec.sheet[:64],
        "dispatcher": rec.dispatcher[:128],
        "crew_no": rec.crew_no,
        "crew_present": rec.crew_present,
        "crew_day_off": rec.crew_day_off,
        "crew_staff": rec.crew_staff,
        "contractors_json": json.dumps(rec.contractors, ensure_ascii=False),
        "present_total": rec.present_total,
        "staff_total": rec.staff_total,
        "sick": rec.sick,
        "injury": rec.injury,
        "vacation": rec.vacation,
        "other_absent": rec.other_absent,
        "truant": rec.truant,
        "day_off": rec.day_off,
        "mob": rec.mob,
        "training": rec.training,
        "rs_ok_start": rec.rs_ok_start,
        "rs_repair_start": rec.rs_repair_start,
        "rs_ok_end": rec.rs_ok_end,
        "rs_repair_end": rec.rs_repair_end,
        "rs_total": rec.rs_total,
        "source": source,
    }
    return keys, values


def apply_snapshot(db: Session, snapshot: DispatcherSnapshot, *, purge_empty: bool = False) -> dict:
    """Сложить снимок в витрину. Возвращает число строк по таблицам.

    Строки, которых нет в снимке, НЕ удаляем: отчёт за прошлые сутки могут
    досдать позже, а удалять уже показанные пользователю цифры опаснее, чем
    держать строку, которую источник передумал отдавать. `purge_empty=True`
    убирает только полностью пустые смены (лист был, данных в нём нет).
    """
    counts = {"shift": 0, "vessel": 0, "rail": 0, "truck": 0, "arrival": 0,
              "rail_arrival": 0, "rail_out": 0, "rail_in": 0, "platform": 0}

    for rec in snapshot.shifts:
        src = snapshot.source
        keys, values = _shift_values(rec, src)
        _upsert(db, FactDispatcherShift, keys, values)
        counts["shift"] += 1

        # Детали смены чистим целиком и кладём заново: в листе строки могут
        # «съехать» (судно ушло, добавился подрядчик), а ключ «груз» не уникален.
        day, shift = rec.report_date, rec.shift
        for model in (FactVesselOperation, FactRailOperation, FactTruckOperation,
                      FactVesselArrival, FactRailArrival, FactRailDeparture,
                      FactRailDelivery, FactPlatformBalance):
            for row in db.scalars(select(model).where(
                model.report_date == day, model.shift == shift  # type: ignore[attr-defined]
            )):
                db.delete(row)
        db.flush()

        # Детали смены — обычные вставки: уникального ключа у строки листа нет
        # (одно судно на двух причалах, один груз двумя строками), а строки смены
        # мы только что удалили.
        for v in rec.vessels:
            db.add(FactVesselOperation(
                report_date=day, shift=shift, berth=v.berth[:32],
                vessel=v.vessel[:128], cargo_group=v.cargo_group[:64],
                plan_unload_t=v.plan_unload_t, plan_load_t=v.plan_load_t,
                plan_total_t=v.plan_total_t, plan_shift_t=v.plan_shift_t,
                fact_shift_t=v.fact_shift_t, on_board_t=v.on_board_t,
                remaining_t=v.remaining_t, productivity_tph=v.productivity_tph,
                moves=v.moves, shift_hours=v.shift_hours,
                downtime_hours=v.downtime_hours, machine_hours=v.machine_hours,
                plan_finish_at=v.plan_finish_at, berthed_at=v.berthed_at,
                started_at=v.started_at, note=v.note, source=src,
            ))
            counts["vessel"] += 1

        for r in rec.rails:
            db.add(FactRailOperation(
                report_date=day, shift=shift, cargo_group=r.cargo_group[:64],
                station_start=r.station_start, port_start_loaded=r.port_start_loaded,
                port_start_empty=r.port_start_empty, processed=r.processed,
                port_end_loaded=r.port_end_loaded, port_end_empty=r.port_end_empty,
                source=src,
            ))
            counts["rail"] += 1

        for t in rec.trucks:
            db.add(FactTruckOperation(
                report_date=day, shift=shift, cargo_group=t.cargo_group[:64],
                imp_unload=t.imp_unload, exp_unload=t.exp_unload, imp_load=t.imp_load,
                exp_load=t.exp_load, direct_vessel=t.direct_vessel, total=t.total,
                source=src,
            ))
            counts["truck"] += 1

        for a in rec.arrivals:
            db.add(FactVesselArrival(
                report_date=day, shift=shift, vessel=a.vessel[:128], eta=a.eta,
                berth=a.berth[:32], work_kind=a.work_kind[:32],
                cargo_group=a.cargo_group[:64], plan_t=a.plan_t,
                plan_raw=a.plan_raw[:64], agent=a.agent[:64], source=src,
            ))
            counts["arrival"] += 1

        for r in rec.rail_arrivals:
            db.add(FactRailArrival(
                report_date=day, shift=shift, station=r.station[:64],
                cargo_group=r.cargo_group[:64], train_index=r.train_index[:64],
                cars=r.cars, work_kind=r.work_kind[:16], wagon_type=r.wagon_type[:32],
                note=r.note[:128], source=src,
            ))
            counts["rail_arrival"] += 1

        for r in rec.rail_out:
            db.add(FactRailDeparture(
                report_date=day, shift=shift, row_no=r.row_no, move_date=r.move_date,
                plan_time=r.plan_time[:8], semi_wagons=r.semi_wagons, covered=r.covered,
                flatcars=r.flatcars, total=r.total, accepted_by=r.accepted_by[:64],
                fact_cars=r.fact_cars, fact_date=r.fact_date, fact_time=r.fact_time[:8],
                source=src,
            ))
            counts["rail_out"] += 1

        for r in rec.rail_in:
            db.add(FactRailDelivery(
                report_date=day, shift=shift, row_no=r.row_no, move_date=r.move_date,
                plan_time=r.plan_time[:8], settle_time=r.settle_time[:8],
                load_semi=r.load_semi, load_covered=r.load_covered, load_flat=r.load_flat,
                empty_semi=r.empty_semi, empty_covered=r.empty_covered,
                empty_flat=r.empty_flat, total=r.total, source=src,
            ))
            counts["rail_in"] += 1

        for pl in rec.platforms:
            db.add(FactPlatformBalance(
                report_date=day, shift=shift, owner=pl.owner[:64],
                total_cars=pl.total_cars, remaining=pl.remaining,
                terminal=pl.terminal, sent=pl.sent,
                arrived_loaded=pl.arrived_loaded, source=src,
            ))
            counts["platform"] += 1

    if purge_empty:
        _purge_empty_shifts(db)

    db.commit()
    return counts


def _purge_empty_shifts(db: Session) -> None:
    """Смены без единой строки обработки — не статистика, а пустой лист."""
    used = {
        (d, s) for d, s in db.execute(
            select(FactVesselOperation.report_date, FactVesselOperation.shift)
        )
    } | {
        (d, s) for d, s in db.execute(
            select(FactTruckOperation.report_date, FactTruckOperation.shift)
        )
    }
    for rec in db.scalars(select(FactDispatcherShift)):
        if (rec.report_date, rec.shift) in used or rec.present_total:
            continue
        db.delete(rec)


def shift_labels() -> Iterable[str]:
    return SHIFTS
