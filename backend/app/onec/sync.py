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
from typing import Iterable

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    FactCargoBalance, FactProcessingDynamics, FactWarehouseCapacity, SyncRun, Warehouse, utcnow,
)
from ..services import job_lock
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
    """Код склада из 1С → id в витрине. Новых складов заводим, существующие обновляем.

    Один SELECT на все склады и один flush на новые: `db.get` на каждый код из
    среза — это лишний round-trip к базе на каждый склад.
    """
    known = {w.code: w for w in db.scalars(select(Warehouse))}
    fresh: list[Warehouse] = []
    for rec in snap.warehouses:
        wh = known.get(rec.code)
        if wh is not None:
            wh.name, wh.kind, wh.capacity, wh.unit, wh.is_active = rec.name, rec.kind, rec.capacity, rec.unit, True
        else:
            wh = Warehouse(code=rec.code, name=rec.name, kind=rec.kind,
                           capacity=rec.capacity, unit=rec.unit)
            db.add(wh)
            known[rec.code] = wh
            fresh.append(wh)
    if fresh:
        db.flush()  # id новых складов нужен фактовым разделам ниже
    return {code: wh.id for code, wh in known.items()}


BULK_CHUNK = 500


def _bulk_apply(db: Session, model, items: list[tuple[tuple, dict]],
                existing: Iterable, key_of) -> None:
    """Идемпотентная укладка раздела: один SELECT существующих строк + вставки пачкой.

    Уникального индекса на натуральный ключ в схеме нет — ON CONFLICT неприменим,
    поэтому соответствие «ключ → строка» собирается в памяти: существующие
    правятся через ORM-объекты, новые копятся в `pending` и уходят `insert`
    батчами. Дубликат ключа внутри среза обновляет ту же запись pending —
    одна строка с последним значением. Коммит решает вызывающий.
    """
    known = {key_of(row): row for row in existing}
    pending: dict[tuple, dict] = {}
    for key, values in items:
        row = known.get(key)
        if row is not None:
            for name, value in values.items():
                setattr(row, name, value)
        elif key in pending:
            pending[key].update(values)
        else:
            pending[key] = dict(values)
    rows = list(pending.values())
    for i in range(0, len(rows), BULK_CHUNK):
        db.execute(insert(model), rows[i:i + BULK_CHUNK])


def _apply_cargo(db: Session, snap: Snapshot, wh_ids: dict[str, int]) -> int:
    """Остатки грузов: ключ (snapshot_at × склад × группа), snapshot_at в срезе один."""
    items: list[tuple[tuple, dict]] = []
    wids: set[int] = set()
    for rec in snap.cargo:
        wid = wh_ids.get(rec.warehouse_code)
        if not wid:
            continue
        wids.add(wid)
        items.append((
            (snap.snapshot_at, wid, rec.cargo_group),
            {"snapshot_at": snap.snapshot_at, "warehouse_id": wid, "cargo_group": rec.cargo_group,
             "qty": rec.qty, "weight_t": rec.weight_t,
             "overdue_weight_t": rec.overdue_weight_t, "avg_age_days": rec.avg_age_days}))
    if not items:
        return 0
    _bulk_apply(
        db, FactCargoBalance, items,
        db.scalars(select(FactCargoBalance).where(
            FactCargoBalance.snapshot_at == snap.snapshot_at,
            FactCargoBalance.warehouse_id.in_(wids))),
        lambda row: (row.snapshot_at, row.warehouse_id, row.cargo_group))
    return len(items)


def _apply_capacity(db: Session, snap: Snapshot, wh_ids: dict[str, int]) -> int:
    """Ёмкость: ключ (snapshot_at × склад)."""
    items: list[tuple[tuple, dict]] = []
    wids: set[int] = set()
    for rec in snap.capacity:
        wid = wh_ids.get(rec.warehouse_code)
        if not wid:
            continue
        wids.add(wid)
        items.append((
            (snap.snapshot_at, wid),
            {"snapshot_at": snap.snapshot_at, "warehouse_id": wid,
             "used": rec.used, "capacity": rec.capacity, "unit": rec.unit}))
    if not items:
        return 0
    _bulk_apply(
        db, FactWarehouseCapacity, items,
        db.scalars(select(FactWarehouseCapacity).where(
            FactWarehouseCapacity.snapshot_at == snap.snapshot_at,
            FactWarehouseCapacity.warehouse_id.in_(wids))),
        lambda row: (row.snapshot_at, row.warehouse_id))
    return len(items)


def _apply_dynamics(db: Session, snap: Snapshot, wh_ids: dict[str, int]) -> int:
    """Динамика: ключ (день × склад); предварительный SELECT ограничен разбросом
    дней и складами самого среза — иначе он тянул бы строки чужих периодов."""
    items: list[tuple[tuple, dict]] = []
    wids: set[int] = set()
    dates = []
    for rec in snap.dynamics:
        wid = wh_ids.get(rec.warehouse_code)
        if not wid:
            continue
        wids.add(wid)
        dates.append(rec.bucket_date)
        items.append((
            (rec.bucket_date, wid),
            {"bucket_date": rec.bucket_date, "warehouse_id": wid,
             "inbound_t": rec.inbound_t, "outbound_t": rec.outbound_t,
             "processed_t": rec.processed_t, "docs_count": rec.docs_count,
             "avg_dwell_hours": rec.avg_dwell_hours}))
    if not items:
        return 0
    _bulk_apply(
        db, FactProcessingDynamics, items,
        db.scalars(select(FactProcessingDynamics).where(
            FactProcessingDynamics.bucket_date >= min(dates),
            FactProcessingDynamics.bucket_date <= max(dates),
            FactProcessingDynamics.warehouse_id.in_(wids))),
        lambda row: (row.bucket_date, row.warehouse_id))
    return len(items)


def apply_snapshot(db: Session, snap: Snapshot) -> dict[str, int]:
    """Идемпотентно положить срез в витрину. Возвращает количество строк по разделам.

    Коммит не делаем — решает вызывающий (run_sync / push), чтобы срез либо лёг
    целиком, либо не лёг вовсе. Разделы пишутся пакетно (см. `_bulk_apply`),
    поэтому счётчики — это число строк среза, обработанных по известным складам.
    """
    wh_ids = _warehouses_map(db, snap)
    return {"warehouses": len(snap.warehouses),
            "cargo": _apply_cargo(db, snap, wh_ids),
            "capacity": _apply_capacity(db, snap, wh_ids),
            "dynamics": _apply_dynamics(db, snap, wh_ids)}


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


def warnings_text(warnings: list[str] | None) -> str | None:
    """Предупреждения разбора одной строкой для журнала; None — когда всё чисто."""
    if not warnings:
        return None
    return " | ".join(warnings)[:2000]


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
        # Витрина легла, но не вся: что именно 1С отдала неразборчивым, видно
        # в журнале загрузок (п. 18 TASKS2.md).
        run.warnings = warnings_text(snap.warnings)
        db.commit()
        log.info("Синхронизация %s: строк %d (%s)", source.name, rows, counts)
        if snap.warnings:
            log.warning("Синхронизация %s: разбор с предупреждениями (%d) — %s",
                        source.name, len(snap.warnings), run.warnings)
        return run
    except Exception as exc:  # noqa: BLE001 — источник внешний, валим в журнал, не роняем сервис
        db.rollback()
        run.status, run.error, run.finished_at = "error", f"{type(exc).__name__}: {exc}"[:1000], utcnow()
        db.add(run)
        db.commit()
        log.exception("Синхронизация с 1С упала")
        return run


# ------------------------------------------------------------------ блокировка --
# Загрузка на кабинет одна: apply_snapshot ищет строку по уникальному ключу и под
# двумя параллельными прогонами способен наплодить дубликатов, а планировщик,
# ручная кнопка и push из 1С друг про друга ничего не знают. Тому, кто пришёл на
# занятую блокировку, очередь не делают: try_sync отвечает None, эндпоинт
# превращает это в 409 с текстом SYNC_BUSY.
#
# Замок в два уровня. Локальный — дешёвый, ловит наложение внутри процесса и не
# трогает базу. Аренда job_lock — общая между процессами: при UVICORN_WORKERS>1
# планировщик заводится в каждом воркере, и одного локального замка не хватает
# (п. 8 TASKS2.md).
_SYNC_LOCK = threading.Lock()

SYNC_BUSY = ("Синхронизация уже выполняется: дождитесь, пока кабинет закончит текущую "
             "загрузку из 1С (она видна в журнале «Последние загрузки»).")


def try_sync(db: Session, days: int | None = None) -> SyncRun | None:
    """run_sync, если никто другой не грузит; None — когда загрузка уже идёт."""
    if not _SYNC_LOCK.acquire(blocking=False):
        return None
    try:
        with job_lock.held(db, job_lock.VITRINA, note="pull-синхронизация 1С") as free:
            if not free:
                return None
            return run_sync(db, days=days)
    finally:
        _SYNC_LOCK.release()


def reconcile_hung_runs(db: Session) -> int:
    """Дописать финал загрузкам, которые оборвались вместе с процессом.

    Статус «running» честный — пока жив процесс, его написавший. Если процесс
    умер посреди цикла, строка висит вечно: дашборд показывает «идёт загрузка»,
    а админ считает кабинет зависшим. Закрываем их ошибкой при старте (п. 16
    TASKS2.md) — до первой новой загрузки, чтобы не спутать с живой.
    """
    hung = db.scalars(select(SyncRun).where(SyncRun.status == "running")).all()
    for run in hung:
        run.status = "error"
        run.finished_at = utcnow()
        run.error = ("Загрузка прервана остановкой кабинета: процесс завершился, "
                     "не завершив цикл. Витрина — на момент последней успешной загрузки.")
    if hung:
        db.commit()
        log.warning("Закрыто зависших загрузок прошлого запуска: %d", len(hung))
    return len(hung)
