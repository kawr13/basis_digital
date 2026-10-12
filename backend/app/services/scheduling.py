"""Планировщик синхронизации с 1С.

Интервал и сам факт «синхронизировать по расписанию» администратор меняет в UI,
поэтому job нельзя один раз завести при старте и забыть: `reschedule()` приводит
расписание в соответствие текущим настройкам. Вызывается из lifespan (при старте)
и из PUT /api/admin/settings (сразу после сохранения).
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..db import SessionLocal
from . import app_settings

log = logging.getLogger("statcab.scheduler")

# Стабильный id: по нему находим job, чтобы перепланировать или снять.
SYNC_JOB_ID = "onec_sync"


def sync_job() -> None:
    """Загрузка среза в отдельной сессии — её зовёт APScheduler из своего потока."""
    from ..onec.sync import run_sync

    db = SessionLocal()
    try:
        run = run_sync(db)
        log.info("Плановая синхронизация: %s, строк %d", run.status, run.rows)
    finally:
        db.close()


def reschedule(scheduler: Any, db: Session) -> dict[str, Any]:
    """Привести job синхронизации в соответствие настройкам.

    sync.enabled=false → job снимается; true → заводится (или меняет интервал).
    Возвращает короткий отчёт для ответа админу.
    """
    enabled = bool(app_settings.get(db, "sync.enabled"))
    minutes = int(app_settings.get(db, "sync.interval_minutes"))
    report: dict[str, Any] = {"scheduler": scheduler is not None,
                              "enabled": enabled, "interval_minutes": minutes, "job": None}

    if scheduler is None:
        report["job"] = "scheduler-off"
        return report

    running = bool(getattr(scheduler, "running", False))
    if not running:                       # планировщик ещё не запущен — лезть в jobstore нельзя
        report["job"] = "scheduler-not-running"
        return report

    job = scheduler.get_job(SYNC_JOB_ID)
    if not enabled:
        if job:
            scheduler.remove_job(SYNC_JOB_ID)
            log.info("Синхронизация по расписанию выключена")
        report["job"] = "removed" if job else "none"
        return report

    if job:
        changed = _interval_minutes(job) != minutes
        if changed:
            scheduler.reschedule_job(SYNC_JOB_ID, trigger="interval", minutes=minutes)
            log.info("Интервал синхронизации: %d мин", minutes)
        report["job"] = "rescheduled" if changed else "kept"
    else:
        scheduler.add_job(sync_job, "interval", minutes=minutes, id=SYNC_JOB_ID,
                          max_instances=1, coalesce=True)
        log.info("Синхронизация по расписанию: каждые %d мин", minutes)
        report["job"] = "added"
    return report


def _interval_minutes(job: Any) -> int | None:
    """Текущий интервал job-а в минутах (для сравнения с настройкой)."""
    seconds = getattr(getattr(job, "trigger", None), "interval", None)
    if seconds is None:
        return None
    try:
        return int(seconds.total_seconds() // 60)
    except AttributeError:
        return None
