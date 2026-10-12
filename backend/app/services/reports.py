"""Отчёты: срез по произвольному периоду и по выбранным складам.

В отличие от метрик дашборда (последний срез + 14 суток), отчёты дают разрез
«склад × день» за выбранный период и выгрузку CSV для Excel.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    FactCargoBalance, FactProcessingDynamics, FactWarehouseCapacity, Warehouse,
)

DEFAULT_DAYS = 30
MAX_DAYS = 366


def clamp_period(date_from: date | None, date_to: date | None,
                 default_days: int | None = None) -> tuple[date, date]:
    """Никогда не пускаем в запрос «всю историю»: максимум год.

    default_days — период по умолчанию из настроек кабинета (reports.default_days);
    если его не передали, живёт константа DEFAULT_DAYS.
    """
    try:
        days = int(default_days) if default_days else DEFAULT_DAYS
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    days = max(1, min(days, MAX_DAYS))
    end = date_to or date.today()
    start = date_from or (end - timedelta(days=days - 1))
    if start > end:
        start, end = end, start
    if (end - start).days + 1 > MAX_DAYS:
        start = end - timedelta(days=MAX_DAYS - 1)
    return start, end


def daily_processing(db: Session, ids: list[int], start: date, end: date) -> list[dict]:
    """Строки «склад × день». Пустые дни пропускаем: отчёт читается легче."""
    if not ids:
        return []
    rows = db.execute(
        select(
            FactProcessingDynamics.bucket_date,
            Warehouse.id,
            Warehouse.name,
            Warehouse.code,
            FactProcessingDynamics.inbound_t,
            FactProcessingDynamics.outbound_t,
            FactProcessingDynamics.processed_t,
            FactProcessingDynamics.docs_count,
            FactProcessingDynamics.avg_dwell_hours,
        )
        .join(Warehouse, Warehouse.id == FactProcessingDynamics.warehouse_id)
        .where(FactProcessingDynamics.warehouse_id.in_(ids),
               FactProcessingDynamics.bucket_date >= start,
               FactProcessingDynamics.bucket_date <= end)
        .order_by(FactProcessingDynamics.bucket_date.desc(), Warehouse.name)
    ).all()

    return [{
        "bucket_date": d,
        "warehouse_id": wid,
        "warehouse": name,
        "code": code,
        "inbound_t": round(inbound or 0, 1),
        "outbound_t": round(outbound or 0, 1),
        "processed_t": round(processed or 0, 1),
        "docs_count": int(docs or 0),
        "avg_dwell_hours": round(dwell or 0, 1),
    } for d, wid, name, code, inbound, outbound, processed, docs, dwell in rows]


def processing_totals(db: Session, ids: list[int], start: date, end: date) -> dict:
    if not ids:
        return {"inbound_t": 0.0, "outbound_t": 0.0, "processed_t": 0.0,
                "docs_count": 0, "avg_dwell_hours": 0.0, "days": 0}
    row = db.execute(
        select(
            func.sum(FactProcessingDynamics.inbound_t),
            func.sum(FactProcessingDynamics.outbound_t),
            func.sum(FactProcessingDynamics.processed_t),
            func.sum(FactProcessingDynamics.docs_count),
            func.avg(FactProcessingDynamics.avg_dwell_hours),
            func.count(func.distinct(FactProcessingDynamics.bucket_date)),
        ).where(FactProcessingDynamics.warehouse_id.in_(ids),
                FactProcessingDynamics.bucket_date >= start,
                FactProcessingDynamics.bucket_date <= end)
    ).one()
    return {
        "inbound_t": round(row[0] or 0, 1),
        "outbound_t": round(row[1] or 0, 1),
        "processed_t": round(row[2] or 0, 1),
        "docs_count": int(row[3] or 0),
        "avg_dwell_hours": round(row[4] or 0, 1),
        "days": int(row[5] or 0),
    }


def per_warehouse_processing(db: Session, ids: list[int], start: date, end: date) -> list[dict]:
    """Итоги по складам за период — «кто больше всех обработал»."""
    if not ids:
        return []
    rows = db.execute(
        select(
            Warehouse.id, Warehouse.name, Warehouse.code,
            func.sum(FactProcessingDynamics.processed_t),
            func.sum(FactProcessingDynamics.inbound_t),
            func.sum(FactProcessingDynamics.outbound_t),
            func.sum(FactProcessingDynamics.docs_count),
            func.avg(FactProcessingDynamics.avg_dwell_hours),
        )
        .join(Warehouse, Warehouse.id == FactProcessingDynamics.warehouse_id)
        .where(FactProcessingDynamics.warehouse_id.in_(ids),
               FactProcessingDynamics.bucket_date >= start,
               FactProcessingDynamics.bucket_date <= end)
        .group_by(Warehouse.id, Warehouse.name, Warehouse.code)
        .order_by(func.sum(FactProcessingDynamics.processed_t).desc())
    ).all()
    return [{
        "warehouse_id": wid, "warehouse": name, "code": code,
        "processed_t": round(p or 0, 1), "inbound_t": round(i or 0, 1),
        "outbound_t": round(o or 0, 1), "docs_count": int(d or 0),
        "avg_dwell_hours": round(dw or 0, 1),
    } for wid, name, code, p, i, o, d, dw in rows]


def capacity_trend(db: Session, ids: list[int], start: date, end: date) -> list[dict]:
    """Загрузка складов по срезам: видно, когда склад упирался в ёмкость."""
    if not ids:
        return []
    # границы периода биндим datetime-объектами: CAST строки по-разному ведёт себя
    # в SQLite и PostgreSQL, а сравнивать нужно с datetime-колонкой
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end + timedelta(days=1), time.min)
    rows = db.execute(
        select(FactWarehouseCapacity, Warehouse)
        .join(Warehouse, Warehouse.id == FactWarehouseCapacity.warehouse_id)
        .where(FactWarehouseCapacity.warehouse_id.in_(ids),
               FactWarehouseCapacity.snapshot_at >= start_dt,
               FactWarehouseCapacity.snapshot_at < end_dt)
        .order_by(FactWarehouseCapacity.snapshot_at.desc())
        .limit(2000)
    ).all()

    out = []
    for fact, wh in rows:
        capacity = fact.capacity or wh.capacity or 0
        util = (fact.used / capacity) if capacity > 0 else 0.0
        out.append({
            "snapshot_at": fact.snapshot_at,
            "warehouse_id": wh.id,
            "warehouse": wh.name,
            "code": wh.code,
            "used": round(fact.used, 1),
            "capacity": round(capacity, 1),
            "utilization": round(util, 3),
            "unit": fact.unit or wh.unit,
        })
    return out


def cargo_report(db: Session, ids: list[int]) -> list[dict]:
    """Остатки по последнему срезу, с итогом по складу."""
    if not ids:
        return []
    snap = db.scalar(select(func.max(FactCargoBalance.snapshot_at))
                     .where(FactCargoBalance.warehouse_id.in_(ids)))
    if not snap:
        return []
    rows = db.execute(
        select(FactCargoBalance, Warehouse)
        .join(Warehouse, Warehouse.id == FactCargoBalance.warehouse_id)
        .where(FactCargoBalance.snapshot_at == snap, FactCargoBalance.warehouse_id.in_(ids))
        .order_by(Warehouse.name, FactCargoBalance.weight_t.desc())
    ).all()
    return [{
        "snapshot_at": snap,
        "warehouse_id": wh.id,
        "warehouse": wh.name,
        "code": wh.code,
        "cargo_group": f.cargo_group,
        "qty": round(f.qty, 1),
        "weight_t": round(f.weight_t, 1),
        "overdue_weight_t": round(f.overdue_weight_t, 1),
        "avg_age_days": round(f.avg_age_days, 1),
    } for f, wh in rows]
