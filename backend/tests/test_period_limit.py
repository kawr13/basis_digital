"""Ограничение длины периода на «Статистике» (TASKS.md, п. 5).

Годовой запрос стоит ~1.2 с сервером и ~743 КБ JSON, а экран столько не рисует
осмысленно: агрегацию считает кабинет, а не 1С. Поэтому `_period` не пускает
размах длиннее `dispatcher.max_period_days` — и `/overview`, и `/rows`, и, что
важно, ДО обращения к кэшу ответов: 400 — ошибка запроса, а не вариант ответа.

Период задаём явными `from`/`to`: проверка длины не зависит от того, есть ли в
витрине строки за этот год, поэтому новые смены не заводим и чужие тесты (у них
витрина на весь прогон одна) не трогаем. Настройки глобальны на процесс —
фикс `_restore` возвращает таблицу в прежнее состояние.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AppSetting
from app.services import app_settings, data_cache

DEFAULT_KEY = "dispatcher.default_period_days"
LIMIT_KEY = "dispatcher.max_period_days"
KEYS = (DEFAULT_KEY, LIMIT_KEY)

OVERVIEW = "/api/dispatcher/overview"
ROWS = "/api/dispatcher/rows"
META = "/api/dispatcher/meta"
SETTINGS = "/api/admin/settings"

END = date(2025, 11, 1)


def _span(days: int, end: date = END) -> dict:
    """Период ровно из `days` суток: границы включительно, как их считает бэк."""
    return {"from": (end - timedelta(days=days - 1)).isoformat(), "to": end.isoformat()}


@pytest.fixture(autouse=True)
def _restore():
    """После теста — точное состояние двух ключей настроек из «до».

    Плюс сброс кэша: записи `/meta` собираются под разными лимитами, и оставлять
    их в LRU нельзя — следующий прогон должен стартовать с чистого листа.
    """
    db = SessionLocal()
    try:
        before = {r.key: r.value for r in db.scalars(select(AppSetting).where(AppSetting.key.in_(KEYS)))}
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        for rec in list(db.scalars(select(AppSetting).where(AppSetting.key.in_(KEYS)))):
            if rec.key in before:
                rec.value = before[rec.key]
            else:
                db.delete(rec)
        db.commit()
    finally:
        db.close()
    data_cache.invalidate()


# ------------------------------------------------------------------- лимит ----

def test_meta_reports_limit(client, admin_hdr):
    """Фронт строит панель периода по `meta.max_period_days`, а не по догадкам."""
    meta = client.get(META, headers=admin_hdr).json()
    assert meta["max_period_days"] == app_settings.DEFAULTS[LIMIT_KEY] == 184
    assert meta["default_period_days"] < meta["max_period_days"], "дефолт обязан проходить лимит"


def test_year_period_rejected(client, admin_hdr):
    """365 суток → 400 с числом разрешённых суток в тексте (годовой запрос ровно за него)."""
    r = client.get(OVERVIEW, params=_span(365), headers=admin_hdr)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "184" in detail, f"в тексте нет числа суток лимита: {detail}"
    assert "сут" in detail and "администратор" in detail, detail
    assert "etag" not in r.headers, "ошибка не должна отдавать заголовки кэша"


def test_rows_endpoint_checks_period_too(client, admin_hdr):
    """`/rows` зовёт тот же `_period`: дриллдаун за год не должен обходить лимит."""
    r = client.get(ROWS, params={**_span(365), "kind": "vessel"}, headers=admin_hdr)
    assert r.status_code == 400
    assert "184" in r.json()["detail"]


def test_limit_boundary_is_inclusive(client, admin_hdr):
    """Ровно лимит — 200, лимит + сутки — 400: `(end - start).days + 1`."""
    ok = client.get(OVERVIEW, params=_span(184), headers=admin_hdr)
    assert ok.status_code == 200, ok.text
    assert ok.json()["period"]["from"] == _span(184)["from"]
    assert ok.json()["period"]["to"] == END.isoformat()

    bad = client.get(OVERVIEW, params=_span(185), headers=admin_hdr)
    assert bad.status_code == 400
    assert "185" in bad.json()["detail"], "в тексте есть и фактическая длина периода"


def test_default_period_never_exceeds_limit(client, admin_hdr):
    """Страница без параметров открывает период по умолчанию — он внутри лимита."""
    assert client.put(SETTINGS, json={LIMIT_KEY: 30}, headers=admin_hdr).status_code == 200
    r = client.get(OVERVIEW, headers=admin_hdr)
    assert r.status_code == 200, r.text
    assert client.get(OVERVIEW, params=_span(31), headers=admin_hdr).status_code == 400


# ------------------------------------------------------------- из админки ----

def test_limit_overridden_from_admin(client, admin_hdr):
    """Поднял лимит — год проходит сразу; вернул — снова 400, кэш не мешает."""
    assert client.get(OVERVIEW, params=_span(365), headers=admin_hdr).status_code == 400

    r = client.put(SETTINGS, json={LIMIT_KEY: 366}, headers=admin_hdr)
    assert r.status_code == 200, r.text
    assert r.json()["settings"][LIMIT_KEY] == 366

    # версия данных от настроек не ездит, поэтому лимит входит в ключ кэша `/meta`
    assert client.get(META, headers=admin_hdr).json()["max_period_days"] == 366

    ok = client.get(OVERVIEW, params=_span(365), headers=admin_hdr)
    assert ok.status_code == 200, ok.text

    assert client.put(SETTINGS, json={LIMIT_KEY: 184}, headers=admin_hdr).status_code == 200
    back = client.get(OVERVIEW, params=_span(365), headers=admin_hdr)
    assert back.status_code == 400, "проверка длины обязана опережать кэш ответов"


def test_limit_below_default_period_rejected(client, admin_hdr):
    """Лимит меньше периода по умолчанию → 422: страница бил бы 400 по своему периоду."""
    r = client.put(SETTINGS, json={LIMIT_KEY: 14}, headers=admin_hdr)
    assert r.status_code == 422
    assert "период" in r.json()["detail"].lower(), r.text

    # обратный разворот той же пары: дефолт длиннее лимита — тоже ошибка
    r2 = client.put(SETTINGS, json={DEFAULT_KEY: 200}, headers=admin_hdr)
    assert r2.status_code == 422

    # ни одна из попыток не должна была испортить рабочую пару значений
    meta = client.get(META, headers=admin_hdr).json()
    assert meta["max_period_days"] == 184 and meta["default_period_days"] == 30


def test_spec_is_exposed_to_admin_ui(client, admin_hdr):
    """Форма админки строится из `specs` — новый ключ обязан в ней появиться."""
    body = client.get(SETTINGS, headers=admin_hdr).json()
    spec = body["specs"][LIMIT_KEY]
    assert spec["kind"] == "int" and spec["min"] == 7 and spec["max"] == 366
    assert spec["label"], "подпись для формы админки"
