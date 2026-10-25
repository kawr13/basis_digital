"""Загрузка среза из 1С в витрину кабинета.

Два пути попадания данных в витрину, и оба идут через одну функцию `apply_snapshot`:
  1. pull — кабинет сам ходит в HTTP-сервис 1С (`run_sync`, по расписанию или «пинком»);
  2. push — 1С сама присылает срез на POST /api/onec/push (`api/onec_push.py`).

Загрузка идемпотентна: строки ищутся по уникальному ключу среза
(snapshot_at × склад × группа груза / день), повторный запуск обновляет их,
а не множит.
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    FactCargoBalance, FactProcessingDynamics, FactWarehouseCapacity, SyncRun, Warehouse, utcnow,
)
from .base import Snapshot, StatsSource

log = logging.getLogger("statcab.sync")


def get_source(db: Session | None = None) -> StatsSource:
    """Источник и его параметры — из стора настроек (БД → .env → DEFAULTS).

    `db` обязателен, если нужно учесть настройки из админки; без него берём .env.
    """
    if db is not None:
        from ..services import app_settings

        cfg = {key: app_settings.get(db, key) for key in (
            "onec.source", "onec.base_url", "onec.login", "onec.password", "onec.timeout_sec",
            "onec.probe_timeout_sec")}
    else:
        s = get_settings()
        cfg = {"onec.source": s.one_c_source, "onec.base_url": s.one_c_base_url,
               "onec.login": s.one_c_login, "onec.password": s.one_c_password,
               "onec.timeout_sec": s.one_c_timeout_sec,
               "onec.probe_timeout_sec": s.one_c_probe_timeout_sec}

    kind = str(cfg.get("onec.source") or "mock").strip().lower()
    if kind == "http":
        from .http_source import HttpSource

        return HttpSource(base_url=cfg.get("onec.base_url"), login=cfg.get("onec.login"),
                          password=cfg.get("onec.password"), timeout=cfg.get("onec.timeout_sec"),
                          probe_timeout=cfg.get("onec.probe_timeout_sec"))
    from .mock import MockSource

    return MockSource()


def _warehouses_map(db: Session, snap: Snapshot) -> dict[str, int]:
    """Код склада из 1С → id в витрине. Новых складов заводим, существующие обновляем."""
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


def apply_snapshot(db: Session, snap: Snapshot) -> dict[str, int]:
    """Идемпотентно положить срез в витрину. Возвращает количество строк по разделам.

    Коммит не делаем — решает вызывающий (run_sync / push), чтобы срез либо лёг
    целиком, либо не лёг вовсе.
    """
    wh_ids = _warehouses_map(db, snap)
    counts = {"warehouses": len(snap.warehouses), "cargo": 0, "capacity": 0, "dynamics": 0}

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
        counts["cargo"] += 1

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
        counts["capacity"] += 1

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
        counts["dynamics"] += 1

    return counts


def window_days(db: Session) -> int:
    """За какой период тянуть динамику — из настроек (по умолчанию 30 суток)."""
    from ..services import app_settings

    try:
        return int(app_settings.get(db, "sync.window_days"))
    except (KeyError, TypeError, ValueError):
        return int(app_settings.DEFAULTS["sync.window_days"])


def _source_kind(db: Session) -> str:
    """Каким источник настроен — даже если он не собрался (для журнала)."""
    from ..services import app_settings

    try:
        return str(app_settings.get(db, "onec.source")).strip().lower()
    except (KeyError, TypeError, ValueError):
        return get_settings().one_c_source


def run_sync(db: Session, days: int | None = None) -> SyncRun:
    """Один цикл загрузки pull-ом. Идемпотентен: повторный запуск обновляет те же строки."""
    if days is None:
        days = window_days(db)

    try:
        source = get_source(db)
    except Exception as exc:  # noqa: BLE001 — например, адрес 1С не задан
        run = SyncRun(source=_source_kind(db), status="error",
                      started_at=utcnow(), finished_at=utcnow(),
                      error=f"{type(exc).__name__}: {exc}"[:1000])
        db.add(run)
        db.commit()
        log.error("Источник 1С не настроен: %s", exc)
        return run

    run = SyncRun(source=source.name, status="running")
    db.add(run)
    db.commit()

    try:
        snap = source.fetch(days=days)
        counts = apply_snapshot(db, snap)
        rows = sum(counts.values())
        run.status, run.rows, run.finished_at = "ok", rows, utcnow()
        db.commit()
        log.info("Синхронизация %s: строк %d (%s)", source.name, rows, counts)
        return run
    except Exception as exc:  # noqa: BLE001 — источник внешний, валим в журнал, не роняем сервис
        db.rollback()
        run.status, run.error, run.finished_at = "error", f"{type(exc).__name__}: {exc}"[:1000], utcnow()
        db.add(run)
        db.commit()
        log.exception("Синхронизация с 1С упала")
        return run


# ------------------------------------------------------------------ блокировка --
# Загрузка на процесс одна. apply_snapshot ищет строку по ключу и под двумя
# параллельными прогонами способен наплодить дубликатов, а планировщик и ручная
# кнопка в админке друг про друга ничего не знают. Тому, кто пришёл на занятую
# блокировку, очередь не делают: try_sync отвечает None, а эндпоинт превращает
# это в 409 с текстом SYNC_BUSY.
_SYNC_LOCK = threading.Lock()

SYNC_BUSY = ("Синхронизация уже выполняется: дождитесь, пока кабинет закончит текущую "
             "загрузку из 1С (она видна в журнале «Последние загрузки»).")


def try_sync(db: Session, days: int | None = None) -> SyncRun | None:
    """run_sync, если никто другой не грузит; None — когда загрузка уже идёт.

    Блокировка местная, на процесс. От двух планировщиков в разных воркерах
    uvicorn она не спасает — там нужен внешний мьютекс (advisory-лок PostgreSQL
    или отдельный сервис синхронизации), это за рамками пункта.
    """
    if not _SYNC_LOCK.acquire(blocking=False):
        return None
    try:
        return run_sync(db, days=days)
    finally:
        _SYNC_LOCK.release()
