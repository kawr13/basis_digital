"""Загрузка среза из 1С в витрину кабинета."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    FactCargoBalance, FactProcessingDynamics, FactWarehouseCapacity, SyncRun, Warehouse, utcnow,
)
from .base import Snapshot, StatsSource

log = logging.getLogger("statcab.sync")


def get_source() -> StatsSource:
    kind = (get_settings().one_c_source or "mock").lower()
    if kind == "http":
        from .http_source import HttpSource

        return HttpSource()
    from .mock import MockSource

    return MockSource()


def _warehouses_map(db: Session, snap: Snapshot) -> dict[str, int]:
    known = {w.code: w.id for w in db.scalars(select(Warehouse))}
    for rec in snap.warehouses:
        if rec.code in known:
            wh = db.get(Warehouse, known[rec.code])
            wh.name, wh.kind, wh.capacity, wh.unit, wh.is_active = rec.name, rec.kind, rec.capacity, rec.unit, True
        else:
            wh = Warehouse(code=rec.code, name=rec.name, kind=rec.kind,
                           capacity=rec.capacity, unit=rec.unit)
            db.add(wh)
            db.flush()
            known[rec.code] = wh.id
    return known


def run_sync(db: Session, days: int = 30) -> SyncRun:
    """Один цикл загрузки. Идемпотентен: повторный запуск обновляет те же строки."""
    source = get_source()
    run = SyncRun(source=source.name, status="running")
    db.add(run)
    db.commit()

    try:
        snap = source.fetch(days=days)
        wh_ids = _warehouses_map(db, snap)
        rows = 0

        for rec in snap.cargo:
            wid = wh_ids.get(rec.warehouse_code)
            if not wid:
                continue
            row = db.scalar(select(FactCargoBalance).where(
                FactCargoBalance.snapshot_at == snap.snapshot_at,
                FactCargoBalance.warehouse_id == wid,
                FactCargoBalance.cargo_group == rec.cargo_group))
            if not row:
                row = FactCargoBalance(snapshot_at=snap.snapshot_at, warehouse_id=wid,
                                       cargo_group=rec.cargo_group)
                db.add(row)
            row.qty, row.weight_t = rec.qty, rec.weight_t
            row.overdue_weight_t, row.avg_age_days = rec.overdue_weight_t, rec.avg_age_days
            rows += 1

        for rec in snap.capacity:
            wid = wh_ids.get(rec.warehouse_code)
            if not wid:
                continue
            row = db.scalar(select(FactWarehouseCapacity).where(
                FactWarehouseCapacity.snapshot_at == snap.snapshot_at,
                FactWarehouseCapacity.warehouse_id == wid))
            if not row:
                row = FactWarehouseCapacity(snapshot_at=snap.snapshot_at, warehouse_id=wid)
                db.add(row)
            row.used, row.capacity, row.unit = rec.used, rec.capacity, rec.unit
            rows += 1

        for rec in snap.dynamics:
            wid = wh_ids.get(rec.warehouse_code)
            if not wid:
                continue
            row = db.scalar(select(FactProcessingDynamics).where(
                FactProcessingDynamics.bucket_date == rec.bucket_date,
                FactProcessingDynamics.warehouse_id == wid))
            if not row:
                row = FactProcessingDynamics(bucket_date=rec.bucket_date, warehouse_id=wid)
                db.add(row)
            row.inbound_t, row.outbound_t, row.processed_t = rec.inbound_t, rec.outbound_t, rec.processed_t
            row.docs_count, row.avg_dwell_hours = rec.docs_count, rec.avg_dwell_hours
            rows += 1

        run.status, run.rows, run.finished_at = "ok", rows, utcnow()
        db.commit()
        log.info("Синхронизация %s: строк %d", source.name, rows)
        return run
    except Exception as exc:  # noqa: BLE001 — источник внешний, валим в журнал, не роняем сервис
        db.rollback()
        run.status, run.error, run.finished_at = "error", f"{type(exc).__name__}: {exc}"[:1000], utcnow()
        db.add(run)
        db.commit()
        log.exception("Синхронизация с 1С упала")
        return run
