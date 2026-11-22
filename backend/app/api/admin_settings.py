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

from ..db import SessionLocal, EngineSwapError, get_db, masked_url
from ..deps import client_ip, require_roles
from ..models import AuditLog, Role, SyncRun, User
from ..onec.sync import SYNC_BUSY, get_source, try_sync
from ..schemas import (DbCandidate, DbConfigOut, DbTestOut, OneCCheck, OneCTestOut, RestartOut,
                       SettingsEnvelope, SyncRunOut, SyncRunResult)
from ..services import app_settings, dbconfig, lifecycle, scheduling
from ..services import job_lock

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
    """Пинком загрузить витрину из 1С (админ). days — иначе из настройки sync.window_days.

    Пока загрузка идёт, любой повторный пинк (и плановый, и ручной) получает 409:
    вторая выгрузка пила бы витрину одновременно с первой.
    """
    run = try_sync(db, days=days)
    if run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, SYNC_BUSY)
    return SyncRunResult(
        id=run.id, started_at=run.started_at, finished_at=run.finished_at,
        source=run.source, status=run.status, rows=run.rows, error=run.error,
        warnings=run.warnings, counts={"rows": run.rows},
    )


@router.get("/sync/runs", response_model=list[SyncRunOut])
def sync_history(limit: int = Query(default=20, ge=1, le=200),
                 _: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Последние загрузки (включая push) — свежие сверху."""
    return list(db.scalars(select(SyncRun).order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
                           .limit(limit)))


# ------------------------------------------------- база данных кабинета (п. 26–28) --
# Пароль наружу не отдаётся ни в одном ответе: вместо него маска и флаг
# `password_set`. Каждое действие оставляет след в аудите вместе с IP.

def _audit(db: Session, actor: User, request: Request, action: str, detail: str) -> None:
    db.add(AuditLog(user_id=actor.id, login=actor.login, action=action,
                    ip=client_ip(request), detail=detail[:500]))
    db.commit()


@router.get("/db", response_model=DbConfigOut)
def read_db(_: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Части текущей базы, её источник и версия сервера."""
    return DbConfigOut(**dbconfig.public(db))


@router.post("/db/test", response_model=DbTestOut)
def test_db(payload: DbCandidate, request: Request,
            actor: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Проверить кандидата: подключиться, `SELECT 1`, отпустить. Ничего не меняем.

    Именно для «сначала убедиться» эта кнопка и существует: админ обязан видеть,
    что база отвечает, ДО того как кабинет на неё переедет.
    """
    candidate = dbconfig.with_password(db, payload.model_dump())
    result = dbconfig.test(candidate)
    _audit(db, actor, request, "admin.db_test",
           f"{dbconfig.masked(candidate)}: " +
           (f"ok ({result['version']})" if result["ok"] else f"отказ — {result['error']}"))
    return DbTestOut(**result)


@router.put("/db", response_model=DbConfigOut)
def apply_db(payload: DbCandidate, request: Request,
             actor: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Сохранить части и переехать на новую базу без перезапуска сервиса.

    Успех — только если новая база ответила и схема на ней подготовлена. Иначе
    кабинет остаётся на прежней базе и отвечает 400 с объяснением: опечатка в
    порте не имеет права оставлять порт без кабинета.

    Внимание: начатые в эту секунду запросы падают — соединения прежней базы
    закрываются. Об этом предупреждает форма.
    """
    candidate = dbconfig.with_password(db, payload.model_dump())
    shown = dbconfig.masked(candidate)
    try:
        dbconfig.apply(db, candidate, actor)
    except ValueError as exc:                       # конфигурация собрана неверно
        _audit(db, actor, request, "admin.db_apply", f"{shown}: отказ — {exc}")
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except EngineSwapError as exc:                  # база не ответила / нет прав на DDL
        _audit(db, actor, request, "admin.db_apply", f"{shown}: отказ — {exc}")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    # Сессия запроса привязана к прежней базе — аудит переезда пишем там же, где
    # лежит указатель на новую (bootstrap-база), иначе запись о переезде уедет
    # в базу, которую кабинет уже не использует.
    _audit(db, actor, request, "admin.db_apply", f"{shown}: применено")
    # Расписание перечитывает НОВАЯ база: интервал синхронизации лежит в её
    # настройках, а сессия запроса привязана к прежней.
    with SessionLocal() as fresh:
        scheduling.reschedule(getattr(request.app.state, "scheduler", None), fresh)
    return DbConfigOut(**dbconfig.public(db))


@router.delete("/db", response_model=DbConfigOut)
def revert_db(request: Request, actor: User = Depends(admin_only),
              db: Session = Depends(get_db)):
    """Вернуться к `DATABASE_URL` из окружения.

    Если к прежней базе подключиться не удалось (например, сервер уже выключен),
    кабинет остаётся на текущей базе и отвечает 400 — тогда помогает перезапуск
    сервиса: он стартует на bootstrap-URL и игнорит указатель только после
    «вернуть ENV».
    """
    try:
        dbconfig.revert(db, actor)
    except EngineSwapError as exc:
        _audit(db, actor, request, "admin.db_revert", f"отказ — {exc}")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    with SessionLocal() as fresh:
        fresh.add(AuditLog(user_id=actor.id, login=actor.login, action="admin.db_revert",
                           ip=client_ip(request), detail=f"вернулись к {masked_url()}"))
        fresh.commit()
        # Интервал снова читается из базы окружения — той, куда вернулись.
        scheduling.reschedule(getattr(request.app.state, "scheduler", None), fresh)
    return DbConfigOut(**dbconfig.public(db))


@router.post("/service/restart", status_code=202, response_model=RestartOut)
def restart_service(request: Request, actor: User = Depends(admin_only),
                    db: Session = Depends(get_db)):
    """Перезапустить сервис: процесс завершается, супервизор поднимает заново.

    Пока идёт загрузка витрины или импорт отчёта диспетчера — 409: рвать
    посередине нельзя, а супервизор поднимет кабинет на полуслове.
    """
    busy = job_lock.info(db, job_lock.VITRINA)
    if busy:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Сейчас идёт загрузка данных — перезапросите перезапуск, "
                            "когда она закончится.")
    _audit(db, actor, request, "admin.service_restart", "перезапуск по запросу администратора")
    lifecycle.schedule_exit()
    return RestartOut(status="restarting",
                      note="Процесс завершается; Docker или systemd поднимут кабинет "
                           "через несколько секунд.")
