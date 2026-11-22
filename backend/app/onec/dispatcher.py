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

from sqlalchemy import delete, insert, select, tuple_
from sqlalchemy.orm import Session

from ..models import (
    FactDispatcherShift, FactPlatformBalance, FactRailArrival, FactRailDelivery,
    FactRailDeparture, FactRailOperation, FactTruckOperation,
    FactVesselArrival, FactVesselOperation,
)

SHIFTS = ("08-20", "20-08")

BULK_CHUNK = 500

# Детали смены: при повторном импорте переукладываются целиком (см. `apply_snapshot`).
_DETAIL_MODELS = (FactVesselOperation, FactRailOperation, FactTruckOperation,
                  FactVesselArrival, FactRailArrival, FactRailDeparture,
                  FactRailDelivery, FactPlatformBalance)


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

def _bulk_insert(db: Session, model, rows: list[dict]) -> None:
    """Пачка вставок: один round-trip на 500 строк вместо поштучного `db.add`.

    Core-вставка не кладёт объекты в identity map — детали смены до коммита
    никто не читает ORM-объектами, а `_purge_empty_shifts` выбирает их SELECT-ом
    и в той же транзакции видит уже вставленные строки.
    """
    for i in range(0, len(rows), BULK_CHUNK):
        db.execute(insert(model), rows[i:i + BULK_CHUNK])


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
    shifts = snapshot.shifts
    src = snapshot.source

    # Шапки смен: один SELECT на все сутки среза вместо SELECT на каждый лист.
    # Дубликат смены внутри среза обновляет ту же pending-вставку — повторного
    # листа на одни сутки быть не может, но источник это умеет.
    dates = {rec.report_date for rec in shifts}
    known: dict[tuple, FactDispatcherShift] = {}
    if dates:
        known = {(r.report_date, r.shift): r for r in db.scalars(
            select(FactDispatcherShift).where(FactDispatcherShift.report_date.in_(dates)))}
    new_shifts: dict[tuple, dict] = {}
    for rec in shifts:
        keys, values = _shift_values(rec, src)
        row = known.get((rec.report_date, rec.shift))
        if row is not None:
            for name, value in values.items():
                setattr(row, name, value)
        elif (rec.report_date, rec.shift) in new_shifts:
            new_shifts[(rec.report_date, rec.shift)].update(values)
        else:
            new_shifts[(rec.report_date, rec.shift)] = {**keys, **values}
        counts["shift"] += 1
    _bulk_insert(db, FactDispatcherShift, list(new_shifts.values()))

    # Детали смены чистим целиком и кладём заново: в листе строки могут
    # «съехать» (судно ушло, добавился подрядчик), а ключ «груз» не уникален.
    # По одному DELETE на таблицу для всех пар (сутки, смена) среза — вместо
    # SELECT + DELETE на каждую строку; чужие смены фильтр tuple_ не трогает.
    if shifts:
        pairs = list({(rec.report_date, rec.shift) for rec in shifts})
        for model in _DETAIL_MODELS:
            db.execute(delete(model).where(
                tuple_(model.report_date, model.shift).in_(pairs)))

    rows: dict[type, list[dict]] = {model: [] for model in _DETAIL_MODELS}
    for rec in shifts:
        day, shift = rec.report_date, rec.shift
        for v in rec.vessels:
            rows[FactVesselOperation].append({
                "report_date": day, "shift": shift, "berth": v.berth[:32],
                "vessel": v.vessel[:128], "cargo_group": v.cargo_group[:64],
                "plan_unload_t": v.plan_unload_t, "plan_load_t": v.plan_load_t,
                "plan_total_t": v.plan_total_t, "plan_shift_t": v.plan_shift_t,
                "fact_shift_t": v.fact_shift_t, "on_board_t": v.on_board_t,
                "remaining_t": v.remaining_t, "productivity_tph": v.productivity_tph,
                "moves": v.moves, "shift_hours": v.shift_hours,
                "downtime_hours": v.downtime_hours, "machine_hours": v.machine_hours,
                "plan_finish_at": v.plan_finish_at, "berthed_at": v.berthed_at,
                "started_at": v.started_at, "note": v.note, "source": src,
            })
        for r in rec.rails:
            rows[FactRailOperation].append({
                "report_date": day, "shift": shift, "cargo_group": r.cargo_group[:64],
                "station_start": r.station_start, "port_start_loaded": r.port_start_loaded,
                "port_start_empty": r.port_start_empty, "processed": r.processed,
                "port_end_loaded": r.port_end_loaded, "port_end_empty": r.port_end_empty,
                "source": src,
            })
        for t in rec.trucks:
            rows[FactTruckOperation].append({
                "report_date": day, "shift": shift, "cargo_group": t.cargo_group[:64],
                "imp_unload": t.imp_unload, "exp_unload": t.exp_unload, "imp_load": t.imp_load,
                "exp_load": t.exp_load, "direct_vessel": t.direct_vessel, "total": t.total,
                "source": src,
            })
        for a in rec.arrivals:
            rows[FactVesselArrival].append({
                "report_date": day, "shift": shift, "vessel": a.vessel[:128], "eta": a.eta,
                "berth": a.berth[:32], "work_kind": a.work_kind[:32],
                "cargo_group": a.cargo_group[:64], "plan_t": a.plan_t,
                "plan_raw": a.plan_raw[:64], "agent": a.agent[:64], "source": src,
            })
        for r in rec.rail_arrivals:
            rows[FactRailArrival].append({
                "report_date": day, "shift": shift, "station": r.station[:64],
                "cargo_group": r.cargo_group[:64], "train_index": r.train_index[:64],
                "cars": r.cars, "work_kind": r.work_kind[:16], "wagon_type": r.wagon_type[:32],
                "note": r.note[:128], "source": src,
            })
        for r in rec.rail_out:
            rows[FactRailDeparture].append({
                "report_date": day, "shift": shift, "row_no": r.row_no, "move_date": r.move_date,
                "plan_time": r.plan_time[:8], "semi_wagons": r.semi_wagons, "covered": r.covered,
                "flatcars": r.flatcars, "total": r.total, "accepted_by": r.accepted_by[:64],
                "fact_cars": r.fact_cars, "fact_date": r.fact_date, "fact_time": r.fact_time[:8],
                "source": src,
            })
        for r in rec.rail_in:
            rows[FactRailDelivery].append({
                "report_date": day, "shift": shift, "row_no": r.row_no, "move_date": r.move_date,
                "plan_time": r.plan_time[:8], "settle_time": r.settle_time[:8],
                "load_semi": r.load_semi, "load_covered": r.load_covered, "load_flat": r.load_flat,
                "empty_semi": r.empty_semi, "empty_covered": r.empty_covered,
                "empty_flat": r.empty_flat, "total": r.total, "source": src,
            })
        for pl in rec.platforms:
            rows[FactPlatformBalance].append({
                "report_date": day, "shift": shift, "owner": pl.owner[:64],
                "total_cars": pl.total_cars, "remaining": pl.remaining,
                "terminal": pl.terminal, "sent": pl.sent,
                "arrived_loaded": pl.arrived_loaded, "source": src,
            })

    counts["vessel"] = len(rows[FactVesselOperation])
    counts["rail"] = len(rows[FactRailOperation])
    counts["truck"] = len(rows[FactTruckOperation])
    counts["arrival"] = len(rows[FactVesselArrival])
    counts["rail_arrival"] = len(rows[FactRailArrival])
    counts["rail_out"] = len(rows[FactRailDeparture])
    counts["rail_in"] = len(rows[FactRailDelivery])
    counts["platform"] = len(rows[FactPlatformBalance])
    for model, items in rows.items():
        _bulk_insert(db, model, items)

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
