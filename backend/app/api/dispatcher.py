"""Страница «Статистика»: данные суточного отчёта диспетчера.

Права. Витрина диспетчера — портовая: склады в ней не измерение (в отличие от
остатков 1С), поэтому фильтр по складам здесь применять нечего. Доступ — любой
авторизованный пользователь; админские действия (импорт файла) вынесены в
`admin_router` и закрыты ролью admin.

Импорт. Пока 1С не отдаёт отчёт по HTTP, файл `Отчет диспетчера.xlsm` загружает
администратор (`POST /api/admin/dispatcher/import`) или автостарт (настройка
`dispatcher.source_path`). Контракт тот же, что отдаст 1С, — `DispatcherSnapshot`.

Кэш. Данные витрины меняются раз в ~15 минут (синк 1С) или в момент импорта
файла, а страницу перерисовывают чаще, чем кто-то успевает их изменить.
Поэтому `/meta`, `/overview` и `/rows` отдают собранный payload из
`services/data_cache` (байты + ETag), а не пересчитывают его на каждый запрос.
"""
from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
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
from ..services import data_cache
from ..services import dispatcher as svc

router = APIRouter(prefix="/api/dispatcher", tags=["dispatcher"])
admin_router = APIRouter(prefix="/api/admin/dispatcher", tags=["dispatcher-admin"])


# Ответ витрины обязаны переспрашивать у сервера: админ импортировал новый
# «Отчёт диспетчера» — кабинет обязан показать его сразу, а не через минуту.
# Дорогое здесь не проверка, а тело ответа, поэтому к `no-cache` приложен ETag:
# при неизменных данных браузер получает 304 без тела вместо 700 КБ JSON.
CACHE_CONTROL = "private, no-cache"


def _bounds(db: Session) -> tuple[date | None, date | None]:
    """Границы витрины через кэш короткоживущих значений.

    Их спрашивает каждый запрос — разрешить период, построить пресеты, — а
    меняются они только вместе с данными. Без кэша это был бы `SELECT min|max`
    по `fact_dispatcher_shift` даже тогда, когда весь остальной ответ пришёл из
    кэша и ход в БД был бы единственным.
    """
    return data_cache.cached_value(("bounds",), data_cache.VERSION_TTL,
                                   lambda: svc.period_bounds(db))


def _period_limits(db: Session) -> tuple[int, int]:
    """(период по умолчанию, лимит длины периода) — одним походом к настройкам.

    Спрашивают их разрешение периода, `/meta` и ключ кэша `/meta`: версия данных
    от настроек не сдвигается, поэтому лимит обязан входить в ключ — иначе после
    `PUT /api/admin/settings` страница строила бы панель периода по устаревшему
    значению до первой же новой смены в витрине.
    """
    values = app_settings.get_all(db)
    return (int(values["dispatcher.default_period_days"]),
            int(values["dispatcher.max_period_days"]))


def _check_period_length(start: date, end: date, limit: int) -> None:
    """Длина периода не больше `dispatcher.max_period_days`.

    Годовой запрос — самый дешёвый способ выложить кабинет: ~1.2 с сервером и
    ~740 КБ JSON, а экран столько не рисует осмысленно (агрегацию считает сам
    кабинет, см. docs/05). Проверка живёт в `_period`, то есть до обращения к
    кэшу: 400 — ошибка запроса, а не вариант ответа, и в кэше он не лежит.
    """
    days = (end - start).days + 1
    if days <= limit:
        return
    raise HTTPException(
        400,
        f"Период {days} суток длиннее разрешённого: максимум {limit} суток. "
        "Выберите пресет (7/14/30/90 суток) или более короткий промежуток; "
        "лимит меняет администратор: администрирование → Настройки → «Максимальная "
        "длина периода, суток».",
    )


def _period(db: Session, date_from: date | None, date_to: date | None) -> tuple[date, date]:
    """Период статистики: по умолчанию — последние N суток, но не раньше данных.

    Разрешённый период обязан уложиться в `dispatcher.max_period_days` — и для
    явных `from`/`to`, и для периода по умолчанию (`/overview` и `/rows` зовут
    `_period` обе).
    """
    lo, hi = _bounds(db)
    days, limit = _period_limits(db)
    if date_from and date_to:
        if date_to < date_from:
            raise HTTPException(400, "Дата окончания раньше даты начала")
        start, end = date_from, date_to
    else:
        end = date_to or hi or date.today()
        start = date_from or (end - timedelta(days=days - 1))
        if lo and start < lo:
            start = lo
        if start > end:
            start = end
    _check_period_length(start, end, limit)          # 400 — до обращения к кэшу
    return start, end


def _raw(value: date | None) -> str:
    """Сырой параметр периода для ключа кэша: `None` и дата — разные ключи."""
    return value.isoformat() if value else ""


def _etag_match(header: str, tag: str) -> bool:
    """Совпал ли `If-None-Match` с нашим ETag; заголовок умеет список и `*`."""
    if header.strip() == "*":
        return True
    want = tag.strip('"')
    # слабые теги (`W/"..."`) — клиент вернул наш же, сравниваем содержимое
    return any(part.strip().removeprefix("W/").strip('"') == want
               for part in header.split(","))


def _cached(request: Request, db: Session, key: tuple, build) -> Response:
    """Отдать байты из кэша витрины; при промахе посчитать `build()` и положить.

    Порядок важен: всё, что обязано ответить 400 (перевёрнутый период,
    неизвестный разрез), эндпоинт считает ДО обращения сюда — иначе ошибка
    лежала бы в кэше как успешный ответ до самой инвалидации.

    Пользователя в ключ не кладём: витрина диспетчера портовая, её читает любой
    авторизованный (фильтр по складам здесь был бы ошибкой). Появятся права на
    разрезы — пользователя придётся дописать в ключ, иначе чужая выборка уйдёт
    другому.
    """
    ver = data_cache.version(db)
    body = data_cache.get(key, ver)
    if body is None:
        body = data_cache.put(key, ver, build())
    tag = data_cache.etag(body)
    headers = {"ETag": tag, "Cache-Control": CACHE_CONTROL}
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and _etag_match(if_none_match, tag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def _meta(db: Session) -> dict:
    """Содержимое `/meta`: вынесено, чтобы эндпоинт остался тонким (кэш + ответ)."""
    lo, hi = _bounds(db)
    default_days, max_days = _period_limits(db)
    shifts = list(db.execute(
        select(FactDispatcherShift.shift, func.count(FactDispatcherShift.id))
        .group_by(FactDispatcherShift.shift)
    ))
    return {
        "date_min": lo,
        "date_max": hi,
        "default_period_days": default_days,
        # по нему фронт строит панель периода: `min`/`max` у полей ввода и
        # пресеты длиннее лимита — иначе страница ловила бы 400 догадками
        "max_period_days": max_days,
        "shifts": [{"shift": s, "count": c} for s, c in sorted(shifts)],
        "updated_at": db.scalar(select(func.max(FactDispatcherShift.updated_at))),
        "sources": [{"source": src, "count": n} for src, n in db.execute(
            select(FactDispatcherShift.source, func.count(FactDispatcherShift.id))
            .group_by(FactDispatcherShift.source)
        )],
    }


@router.get("/meta")
def meta(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Справоч страницы: границы данных, смены, источники. Тоже из кэша."""
    # Настройки периода входят в ключ кэша: версия данных от них не сдвигается,
    # а `max_period_days` фронт читает отсюда — после сохранения в админке панель
    # периода обязана перестроиться сразу, а не доедать старый ответ.
    default_days, max_days = _period_limits(db)
    return _cached(request, db, ("meta", default_days, max_days), lambda: _meta(db))


@router.get("/overview")
def overview(request: Request,
             date_from: date | None = Query(default=None, alias="from"),
             date_to: date | None = Query(default=None, alias="to"),
             shift: str | None = Query(default=None, pattern="^(all|08-20|20-08)$"),
             user: User = Depends(current_user), db: Session = Depends(get_db)):
    start, end = _period(db, date_from, date_to)          # 400 — до обращения к кэшу
    only_shift = None if shift in (None, "all") else shift

    def build() -> dict:
        payload = svc.overview(db, start, end, only_shift)
        payload["period"]["requested_from"] = date_from
        payload["period"]["requested_to"] = date_to
        return payload

    # В ключе и разрешённый период, и сырые параметры: payload мутируется
    # requested_from/requested_to, поэтому запросы с одинаковым периодом, но
    # разными `from`/`to` — разные ответы, а не одна запись на двоих.
    cache_key = ("overview", start.isoformat(), end.isoformat(), shift or "all",
                 _raw(date_from), _raw(date_to))
    return _cached(request, db, cache_key, build)


@router.get("/day")
def day(day_: date = Query(alias="date"),
        shift: str | None = Query(default=None, pattern="^(all|08-20|20-08)$"),
        user: User = Depends(current_user), db: Session = Depends(get_db)):
    return svc.day_detail(db, day_, None if shift in (None, "all") else shift)


@router.get("/rows")
def rows_view(request: Request,
              date_from: date | None = Query(default=None, alias="from"),
              date_to: date | None = Query(default=None, alias="to"),
              shift: str | None = Query(default=None, pattern="^(all|08-20|20-08)$"),
              kind: str = Query(..., pattern="^(vessel|berth|cargo|rail_cargo|truck_cargo|"
                                             "station|rail_move|platform)$"),
              key: str | None = Query(default=None, max_length=120),
              user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Строки витрины, из которых собран график: клик по бару = таблица цифр."""
    start, end = _period(db, date_from, date_to)          # 400 — до обращения к кэшу
    only_shift = None if shift in (None, "all") else shift

    def build() -> dict:
        try:
            return svc.detail_rows(db, start, end, only_shift, kind, key)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    cache_key = ("rows", kind, key or "", start.isoformat(), end.isoformat(),
                 shift or "all", _raw(date_from), _raw(date_to))
    return _cached(request, db, cache_key, build)


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
    # Импорт — момент, когда кэш обязан протухнуть сразу: у строк-деталей своей
    # updated_at нет, а счётчик версий обновляется не на каждый запрос.
    data_cache.invalidate()
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
