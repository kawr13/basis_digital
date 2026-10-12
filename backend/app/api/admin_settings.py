"""Админка: настройки времени выполнения, интеграция с 1С и журнал загрузок.

Всё под `require_roles(Role.ADMIN)`. Секреты (пароль учётки чтения 1С и токен
push) наружу не отдаются — вместо значения маска `***` и флаг `..._set`.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_roles
from ..models import Role, SyncRun, User
from ..onec.sync import get_source, run_sync
from ..schemas import OneCCheck, OneCTestOut, SettingsEnvelope, SyncRunOut, SyncRunResult
from ..services import app_settings
from ..services import scheduling

log = logging.getLogger("statcab.admin.settings")

router = APIRouter(prefix="/api/admin", tags=["admin-settings"])

admin_only = require_roles(Role.ADMIN)


def _envelope(db: Session, scheduler_report: dict[str, Any] | None = None) -> SettingsEnvelope:
    return SettingsEnvelope(
        settings=app_settings.masked(db),
        defaults=app_settings.DEFAULTS,
        overridden=app_settings.overridden(db),
        specs=app_settings.specs(),
        scheduler=scheduler_report,
    )


# ------------------------------------------------------------------ настройки ----

@router.get("/settings", response_model=SettingsEnvelope)
def read_settings(_: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Текущие значения (секреты — маской), defaults и список перебитых ключей."""
    return _envelope(db)


@router.put("/settings", response_model=SettingsEnvelope)
def update_settings(payload: dict[str, Any], request: Request,
                    actor: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Частичный патч настроек. Ошибки валидации → 422 с русским текстом.

    После сохранения сразу перепланируем синхронизацию: интервал и наличие
    job-а меняются без перезапуска сервиса.
    """
    try:
        app_settings.apply(db, payload, actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    report = scheduling.reschedule(getattr(request.app.state, "scheduler", None), db)
    return _envelope(db, report)


@router.post("/settings/reset", response_model=SettingsEnvelope)
def reset_settings(keys: list[str], request: Request,
                   actor: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Вернуть отдельные ключи к значениям из .env/DEFAULTS (удалить запись из БД)."""
    try:
        app_settings.reset(db, keys, actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    report = scheduling.reschedule(getattr(request.app.state, "scheduler", None), db)
    return _envelope(db, report)


# --------------------------------------------------------------------- 1С ----

@router.post("/onec/test", response_model=OneCTestOut)
def test_onec_connection(_: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Проверка связи с 1С: для http — четыре GET по очереди, для mock — сам срез.

    Наружу не летит ни одно исключение: всё, что сломалось, оседает в `error`
    конкретной проверки.
    """
    try:
        source = get_source(db)
    except Exception as exc:  # noqa: BLE001 — источник может быть не настроен
        return OneCTestOut(source="не настроен", ok=False, checks=[OneCCheck(
            path="(источник не собран)", ok=False, status=None, ms=0,
            error=f"{type(exc).__name__}: {exc}"[:300])])

    probe = getattr(source, "probe", None)
    if probe is None:
        return OneCTestOut(source=source.name, ok=False, checks=[OneCCheck(
            path="(источник не умеет проверку)", ok=False, status=None, ms=0,
            error="для этого источника проверка не реализована")])

    try:
        checks = [OneCCheck(**c) for c in probe(days=7)]
    except Exception as exc:  # noqa: BLE001
        log.exception("Проверка связи с 1С упала")
        checks = [OneCCheck(path="(проверка не выполнена)", ok=False, status=None, ms=0,
                            error=f"{type(exc).__name__}: {exc}"[:300])]

    return OneCTestOut(source=source.name, ok=bool(checks) and all(c.ok for c in checks),
                       checks=checks)


# ------------------------------------------------------------- журнал загрузок ----

@router.post("/sync/run", response_model=SyncRunResult)
def manual_sync(days: int | None = Query(default=None, ge=1, le=366),
                actor: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Пинком загрузить витрину из 1С (админ). days — иначе из настройки sync.window_days."""
    run = run_sync(db, days=days)
    return SyncRunResult(
        id=run.id, started_at=run.started_at, finished_at=run.finished_at,
        source=run.source, status=run.status, rows=run.rows, error=run.error,
        counts={"rows": run.rows},
    )


@router.get("/sync/runs", response_model=list[SyncRunOut])
def sync_history(limit: int = Query(default=20, ge=1, le=200),
                 _: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Последние загрузки (включая push) — свежие сверху."""
    return list(db.scalars(select(SyncRun).order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
                           .limit(limit)))
