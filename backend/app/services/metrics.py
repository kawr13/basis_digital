"""Слой чтения витрины статистики.

Все запросы фильтруются по складам, доступным пользователю (scope).
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    FactCargoBalance, FactProcessingDynamics, FactWarehouseCapacity, SyncRun, Warehouse, utcnow,
)

WARNING_AT = 0.85   # порог «внимание» по загрузке склада
CRITICAL_AT = 0.95  # порог «критично»


def allowed_warehouse_ids(db: Session, user) -> list[int]:
    """Пустой scope = все активные склады."""
    if user.all_warehouses:
        return list(db.scalars(select(Warehouse.id).where(Warehouse.is_active.is_(True))))
    return [wid for wid in user.warehouse_ids if wid]


def _latest(db: Session, model, ids: list[int]):
    if not ids:
        return None
    return db.scalar(select(func.max(model.snapshot_at)).where(model.warehouse_id.in_(ids)))


def _status(util: float) -> str:
    if util >= CRITICAL_AT:
        return "critical"
    if util >= WARNING_AT:
        return "warning"
    return "ok"


def cargo_balance(db: Session, ids: list[int]) -> dict:
    snap = _latest(db, FactCargoBalance, ids)
    if not snap:
        return {"snapshot_at": None, "total_weight_t": 0.0, "overdue_weight_t": 0.0, "rows": []}

    rows = db.execute(
        select(FactCargoBalance, Warehouse.name)
        .join(Warehouse, Warehouse.id == FactCargoBalance.warehouse_id)
        .where(FactCargoBalance.snapshot_at == snap, FactCargoBalance.warehouse_id.in_(ids))
        .order_by(FactCargoBalance.weight_t.desc())
    ).all()

    out = [{
        "warehouse_id": f.warehouse_id,
        "warehouse": name,
        "cargo_group": f.cargo_group,
        "qty": round(f.qty, 2),
        "weight_t": round(f.weight_t, 2),
        "overdue_weight_t": round(f.overdue_weight_t, 2),
        "avg_age_days": round(f.avg_age_days, 1),
    } for f, name in rows]

    return {
        "snapshot_at": snap,
        "total_weight_t": round(sum(r["weight_t"] for r in out), 2),
        "overdue_weight_t": round(sum(r["overdue_weight_t"] for r in out), 2),
        "rows": out,
    }


def warehouse_capacity(db: Session, ids: list[int]) -> dict:
    snap = _latest(db, FactWarehouseCapacity, ids)
    if not snap:
        return {"snapshot_at": None, "rows": []}

    rows = db.execute(
        select(FactWarehouseCapacity, Warehouse)
        .join(Warehouse, Warehouse.id == FactWarehouseCapacity.warehouse_id)
        .where(FactWarehouseCapacity.snapshot_at == snap, FactWarehouseCapacity.warehouse_id.in_(ids))
    ).all()

    out = []
    for fact, wh in rows:
        capacity = fact.capacity or wh.capacity or 0
        util = (fact.used / capacity) if capacity > 0 else 0.0
        out.append({
            "warehouse_id": wh.id,
            "warehouse": wh.name,
            "code": wh.code,
            "used": round(fact.used, 1),
            "capacity": round(capacity, 1),
            "free": round(max(capacity - fact.used, 0), 1),
            "utilization": round(util, 3),
            "unit": fact.unit or wh.unit,
            "status": _status(util),
        })
    out.sort(key=lambda r: r["utilization"], reverse=True)
    return {"snapshot_at": snap, "rows": out}


def processing_dynamics(db: Session, ids: list[int], days: int = 14) -> dict:
    if not ids:
        return {"days": days, "points": [], "processed_total": 0.0,
                "processed_prev": 0.0, "delta_pct": None, "avg_dwell_hours": 0.0}

    today = date.today()
    start = today - timedelta(days=days - 1)
    prev_start = start - timedelta(days=days)
    prev_end = start - timedelta(days=1)

    daily = db.execute(
        select(
            FactProcessingDynamics.bucket_date,
            func.sum(FactProcessingDynamics.inbound_t),
            func.sum(FactProcessingDynamics.outbound_t),
            func.sum(FactProcessingDynamics.processed_t),
            func.sum(FactProcessingDynamics.docs_count),
            func.avg(FactProcessingDynamics.avg_dwell_hours),
        )
        .where(FactProcessingDynamics.warehouse_id.in_(ids),
               FactProcessingDynamics.bucket_date >= prev_start,
               FactProcessingDynamics.bucket_date <= today)
        .group_by(FactProcessingDynamics.bucket_date)
        .order_by(FactProcessingDynamics.bucket_date)
    ).all()

    points, total, prev_total, dwell = [], 0.0, 0.0, []
    for d, inbound, outbound, processed, docs, avg_dwell in daily:
        row = {
            "bucket_date": d,
            "inbound_t": round(inbound or 0, 1),
            "outbound_t": round(outbound or 0, 1),
            "processed_t": round(processed or 0, 1),
            "docs_count": int(docs or 0),
            "avg_dwell_hours": round(avg_dwell or 0, 1),
        }
        if row["avg_dwell_hours"]:
            dwell.append(row["avg_dwell_hours"])
        if d >= start:
            points.append(row)
            total += row["processed_t"]
        else:
            prev_total += row["processed_t"]

    delta = None
    if prev_total > 0:
        delta = round((total - prev_total) / prev_total * 100, 1)

    return {
        "days": days,
        "points": points,
        "processed_total": round(total, 1),
        "processed_prev": round(prev_total, 1),
        "delta_pct": delta,
        "avg_dwell_hours": round(sum(dwell) / len(dwell), 1) if dwell else 0.0,
    }


def summary(db: Session, ids: list[int]) -> dict:
    cargo = cargo_balance(db, ids)
    cap = warehouse_capacity(db, ids)
    dyn = processing_dynamics(db, ids, days=14)
    dyn7 = processing_dynamics(db, ids, days=7)

    utils = [r["utilization"] for r in cap["rows"] if r["capacity"] > 0]
    today = date.today()
    today_t = next((p["processed_t"] for p in dyn["points"] if p["bucket_date"] == today), 0.0)

    snap_times = [t for t in (cargo["snapshot_at"], cap["snapshot_at"]) if t]
    last_sync = db.scalar(select(func.max(SyncRun.finished_at)))

    return {
        "generated_at": utcnow(),
        "data_as_of": max(snap_times) if snap_times else last_sync,
        "warehouses": len(cap["rows"]) or len(ids),
        "cargo_weight_t": cargo["total_weight_t"],
        "overdue_weight_t": cargo["overdue_weight_t"],
        "utilization": round(sum(utils) / len(utils), 3) if utils else 0.0,
        "processed_today_t": today_t,
        "processed_7d_t": dyn7["processed_total"],
        "dwell_hours": dyn["avg_dwell_hours"],
        "delta_pct": dyn["delta_pct"],
    }
