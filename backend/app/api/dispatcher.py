"""Страница «Статистика»: данные суточного отчёта диспетчера.

Права. Витрина диспетчера — портовая: склады в ней не измерение (в отличие от
остатков 1С), поэтому фильтр по складам здесь применять нечего. Доступ — любой
авторизованный пользователь; админские действия (импорт файла) вынесены в
`admin_router` и закрыты ролью admin.

Импорт. Пока 1С не отдаёт отчёт по HTTP, файл `Отчет диспетчера.xlsm` загружает
администратор (`POST /api/admin/dispatcher/import`) или автостарт (настройка
`dispatcher.source_path`). Контракт тот же, что отдаст 1С, — `DispatcherSnapshot`.
"""
from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_roles
from ..models import (
    FactDispatcherShift, FactRailOperation, FactTruckOperation,
    FactVesselOperation, Role, User,
)
from ..onec.dispatcher import DispatcherSnapshot, apply_snapshot
from ..onec.dispatcher_xlsm import describe, parse_workbook
from ..services import app_settings
from ..services import dispatcher as svc

router = APIRouter(prefix="/api/dispatcher", tags=["dispatcher"])
admin_router = APIRouter(prefix="/api/admin/dispatcher", tags=["dispatcher-admin"])


def _period(db: Session, date_from: date | None, date_to: date | None) -> tuple[date, date]:
    """Период статистики: по умолчанию — последние N суток, но не раньше данных."""
    lo, hi = svc.period_bounds(db)
    days = int(app_settings.get(db, "dispatcher.default_period_days"))
    if date_from and date_to:
        if date_to < date_from:
            raise HTTPException(400, "Дата окончания раньше даты начала")
        return date_from, date_to
    end = date_to or hi or date.today()
    start = date_from or (end - timedelta(days=days - 1))
    if lo and start < lo:
        start = lo
    if start > end:
        start = end
    return start, end


@router.get("/meta")
def meta(user: User = Depends(current_user), db: Session = Depends(get_db)):
    lo, hi = svc.period_bounds(db)
    shifts = list(db.execute(
        select(FactDispatcherShift.shift, func.count(FactDispatcherShift.id))
        .group_by(FactDispatcherShift.shift)
    ))
    return {
        "date_min": lo,
        "date_max": hi,
        "default_period_days": int(app_settings.get(db, "dispatcher.default_period_days")),
        "shifts": [{"shift": s, "count": c} for s, c in sorted(shifts)],
        "updated_at": db.scalar(select(func.max(FactDispatcherShift.updated_at))),
        "sources": [{"source": src, "count": n} for src, n in db.execute(
            select(FactDispatcherShift.source, func.count(FactDispatcherShift.id))
            .group_by(FactDispatcherShift.source)
        )],
    }


@router.get("/overview")
def overview(date_from: date | None = Query(default=None, alias="from"),
             date_to: date | None = Query(default=None, alias="to"),
             shift: str | None = Query(default=None, pattern="^(all|08-20|20-08)$"),
             user: User = Depends(current_user), db: Session = Depends(get_db)):
    start, end = _period(db, date_from, date_to)
    payload = svc.overview(db, start, end, None if shift in (None, "all") else shift)
    payload["period"]["requested_from"] = date_from
    payload["period"]["requested_to"] = date_to
    return payload


@router.get("/day")
def day(day_: date = Query(alias="date"),
        shift: str | None = Query(default=None, pattern="^(all|08-20|20-08)$"),
        user: User = Depends(current_user), db: Session = Depends(get_db)):
    return svc.day_detail(db, day_, None if shift in (None, "all") else shift)


@router.get("/rows")
def rows_view(date_from: date | None = Query(default=None, alias="from"),
              date_to: date | None = Query(default=None, alias="to"),
              shift: str | None = Query(default=None, pattern="^(all|08-20|20-08)$"),
              kind: str = Query(..., pattern="^(vessel|berth|cargo|rail_cargo|truck_cargo|"
                                             "station|rail_move|platform)$"),
              key: str | None = Query(default=None, max_length=120),
              user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Строки витрины, из которых собран график: клик по бару = таблица цифр."""
    start, end = _period(db, date_from, date_to)
    try:
        return svc.detail_rows(db, start, end, None if shift in (None, "all") else shift,
                               kind, key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ------------------------------------------------------------------- импорт ----

class ImportIn(BaseModel):
    """Файл можно указать путём на сервере (dev) или прислать base64 (прод)."""

    path: str | None = Field(default=None, max_length=500)
    content_base64: str | None = None
    filename: str | None = Field(default=None, max_length=200)


def _counts(db: Session) -> dict:
    return {
        "shifts": db.scalar(select(func.count(FactDispatcherShift.id))) or 0,
        "vessels": db.scalar(select(func.count(FactVesselOperation.id))) or 0,
        "rails": db.scalar(select(func.count(FactRailOperation.id))) or 0,
        "trucks": db.scalar(select(func.count(FactTruckOperation.id))) or 0,
    }


def import_file(db: Session, source: bytes | str | Path, *, source_name: str = "xlsm") -> dict:
    """Разобрать файл и сложить в витрину. Ошибку разбора превращаем в 400."""
    try:
        snapshot: DispatcherSnapshot = parse_workbook(source)
    except Exception as exc:                       # битый файл, не xlsm, нет листов
        raise HTTPException(400, f"Не удалось разобрать файл: {type(exc).__name__}: {exc}") from exc
    if not snapshot.shifts:
        raise HTTPException(400, "В файле не найдено ни одного листа отчёта диспетчера")
    counts = apply_snapshot(db, snapshot)
    dates = [s.report_date for s in snapshot.shifts]
    return {"imported": counts, "described": describe(snapshot),
            "source": source_name,
            "date_min": min(dates), "date_max": max(dates),
            "total": _counts(db)}


@admin_router.post("/import")
def import_dispatcher_report(payload: ImportIn,
                             user: User = Depends(require_roles(Role.ADMIN)),
                             db: Session = Depends(get_db)):
    if payload.content_base64:
        try:
            raw = base64.b64decode(payload.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(400, "content_base64 — не корректный base64") from exc
        result = import_file(db, raw, source_name=payload.filename or "upload.xlsm")
    elif payload.path:
        p = Path(payload.path)
        if not p.exists():
            raise HTTPException(400, f"Файл не найден на сервере: {p}")
        result = import_file(db, p, source_name=str(p))
    else:
        raise HTTPException(400, "Нужно указать path или прислать content_base64")

    from ..models import AuditLog
    db.add(AuditLog(user_id=user.id, login=user.login, action="dispatcher.import",
                    detail=f"{result['source']}: {result['described']}"))
    db.commit()
    return result


@admin_router.get("/status")
def status(user: User = Depends(require_roles(Role.ADMIN)), db: Session = Depends(get_db)):
    path = str(app_settings.get(db, "dispatcher.source_path") or "")
    p = Path(path) if path else None
    return {
        "configured_path": path,
        "exists": bool(p and p.exists()),
        "mtime": datetime.fromtimestamp(p.stat().st_mtime) if p and p.exists() else None,
        "auto_import": bool(app_settings.get(db, "dispatcher.auto_import")),
        "counts": _counts(db),
        # границы того, что реально лежит в витрине, и момент последней загрузки
        "date_min": db.scalar(select(func.min(FactDispatcherShift.report_date))),
        "date_max": db.scalar(select(func.max(FactDispatcherShift.report_date))),
        "updated_at": db.scalar(select(func.max(FactDispatcherShift.updated_at))),
    }
