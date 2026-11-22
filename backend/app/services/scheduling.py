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

# Уборка служебных записей: её не настраивают из UI, поэтому job заводится один
# раз при старте, а не через reschedule().
RETENTION_JOB_ID = "retention"
RETENTION_INTERVAL_HOURS = 24


def sync_job() -> None:
    """Загрузка среза в отдельной сессии — её зовёт APScheduler из своего потока."""
    from ..onec.sync import try_sync

    db = SessionLocal()
    try:
        # max_instances=1 у job-а спасает от наложения самой себя, но не от ручной
        # кнопки в админке: если витрину уже пишет чей-то пинк, цикл пропускаем —
        # следующий придёт по своему интервалу.
        run = try_sync(db)
        if run is None:
            log.info("Плановая синхронизация пропущена: уже идёт загрузка")
        else:
            log.info("Плановая синхронизация: %s, строк %d", run.status, run.rows)
    finally:
        db.close()


def retention_job() -> None:
    """Ночная уборка: журнал действий, протухшие сессии, старый журнал загрузок."""
    from . import retention

    db = SessionLocal()
    try:
        retention.purge(db)
    except Exception:                        # noqa: BLE001 — уборка не роняет планировщик
        log.exception("Ретention не доделан: служебные записи почистятся в следующий заход")
    finally:
        db.close()


def register_fixed_jobs(scheduler: Any) -> None:
    """Job-ы с постоянным расписанием — те, что не зависят от настроек стора."""
    scheduler.add_job(retention_job, "interval", hours=RETENTION_INTERVAL_HOURS,
                      id=RETENTION_JOB_ID, max_instances=1, coalesce=True,
                      replace_existing=True)


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


def state(scheduler: Any) -> dict[str, Any]:
    """Состояние планировщика для /readyz (п. 19 TASKS2.md): жив ли и что соберётся следующим.

    Незапущенный планировщик не трогаем: get_jobs() на нём бросает, а проба обязана
    отвечать телом, а не исключением.
    """
    if scheduler is None:
        return {"running": False, "jobs": []}
    running = bool(getattr(scheduler, "running", False))
    jobs: list[dict[str, Any]] = []
    if running:
        for job in scheduler.get_jobs():
            nxt = getattr(job, "next_run_time", None)
            jobs.append({"id": job.id, "next": nxt.isoformat() if nxt else None})
    jobs.sort(key=lambda j: j["id"])
    return {"running": running, "jobs": jobs}
