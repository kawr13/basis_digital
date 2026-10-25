"""Страницы строк дриллдауна и серверная выгрузка CSV (TASKS.md, п. 6).

Почему эти проверки нужны именно в таком виде:

* `/rows` без пагинации отдавал 158 КБ за месяц и ~1.9 МБ за год, поэтому экран
  теперь получает страницу (`limit`/`offset`), а честный размер выборки приезжает
  в `total` — по нему фронт пишет «показаны первые N из M»;
* кнопка «CSV` не может собираться из экрана: усечённая страница срезала бы
  половину выгрузки, поэтому файл пишет бэкенд (`/rows.csv`) без лимита и
  проверяем мы, что строк в файле ровно `total`, а не `len(rows)`;
* `total` и `totals` считаются по всей выборке, до лимита, — иначе «итого» под
  таблицей перестало бы совпадать с цифрой на графике;
* формат файла — общий для кабинета: `;`, десятичная запятая, BOM (Excel);
* CSV не кэшируется: протухшая выгрузка хуже медленной;
* лимит периода (п. 5) обязан действовать и на файл, иначе год выгружали бы
  в обход 400.

Данные заводим свои (декабрь 2025) и убираем за собой: витрина на весь прогон
одна, а файл «Отчёта диспетчера» в тесты не тянем.
"""
from __future__ import annotations

import csv
import io
from datetime import date

import pytest
from sqlalchemy import delete

from app.api.dispatcher import ROWS_LIMIT_DEFAULT
from app.db import SessionLocal
from app.models import FactVesselOperation
from app.services import data_cache

PERIOD = {"from": "2025-12-05", "to": "2025-12-07"}
DAYS = [date(2025, 12, 5), date(2025, 12, 6), date(2025, 12, 7)]
SHIFTS = ("08-20", "20-08")
VESSELS = ("ROWS SHIP A", "ROWS SHIP B")
ROWS_URL = "/api/dispatcher/rows"
CSV_URL = "/api/dispatcher/rows.csv"
SOURCE = "rows-test"
# 12 строк: 3 суток × 2 смены × 2 судна, план = 100..1200 т (итог — 7800 т)
TOTAL = 12
PLAN_TOTAL = 7800.0


def _vessel(day: date, shift: str, vessel: str, plan_t: float) -> FactVesselOperation:
    return FactVesselOperation(
        report_date=day, shift=shift, berth="РП - 1/2", vessel=vessel,
        cargo_group="генгруз", plan_shift_t=plan_t, fact_shift_t=plan_t,
        productivity_tph=100.0, machine_hours=10, downtime_hours=1, source=SOURCE,
    )


@pytest.fixture(scope="module")
def vitrine():
    """12 строк обработки за три суток — есть что урезать лимитом и что выгрузить."""
    db = SessionLocal()
    try:
        n = 0
        for day in DAYS:
            for shift in SHIFTS:
                for vessel in VESSELS:
                    n += 1
                    db.add(_vessel(day, shift, vessel, float(n * 100)))
        db.commit()
    finally:
        db.close()
    data_cache.invalidate()
    yield
    db = SessionLocal()
    try:
        db.execute(delete(FactVesselOperation).where(FactVesselOperation.source == SOURCE))
        db.commit()
    finally:
        db.close()
    data_cache.invalidate()


def _rows(client, hdr, **params) -> dict:
    r = client.get(ROWS_URL, params={**PERIOD, "kind": "vessel", **params}, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


def _identities(payload: dict) -> list[tuple]:
    """«Отпечаток» строк: порядок страниц сверяем по нему, а не по индексам."""
    return [(r["report_date"], r["shift"], r["berth"], r["plan_shift_t"])
            for r in payload["rows"]]


def _csv(client, hdr, **params):
    r = client.get(CSV_URL, params={**PERIOD, "kind": "vessel", **params}, headers=hdr)
    assert r.status_code == 200, r.text
    return r


def _csv_rows(response) -> list[list[str]]:
    """Файл так же читаем, как Excel: BOM снимаем, разделитель — `;`."""
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text), delimiter=";"))


def _label(columns: list[dict], key: str) -> str:
    """Подпись колонки: в файле живут подписи (`план, т`), а не ключи JSONа."""
    return next(c["label"] for c in columns if c["key"] == key)


def _numbers(rows: list[list[str]], header: list[str], label: str) -> list[float]:
    """Числовая колонка файла: `1500,0` → 1500.0 (десятичная запятая — наша же)."""
    idx = header.index(label)
    return [float(row[idx].replace(",", ".")) for row in rows if row[idx]]


# ------------------------------------------------------------------ страницы ----

def test_limit_caps_the_page_and_reports_the_real_size(client, admin_hdr, vitrine):
    """`limit=5` отдаёт 5 строк, но `total` — про всю выборку, а не про экран."""
    body = _rows(client, admin_hdr, limit=5)
    assert len(body["rows"]) == 5
    assert body["total"] == TOTAL
    assert body["truncated"] is True
    # итог — по всей выборке: панель детализирует график, а не то, что влезло в окно
    assert body["totals"]["plan_shift_t"] == PLAN_TOTAL


def test_default_limit_is_the_documented_one(client, admin_hdr, vitrine):
    """Без `limit` экран живёт по умолчанию (500) — «слишком большой» выборки нет."""
    assert ROWS_LIMIT_DEFAULT == 500
    body = _rows(client, admin_hdr)
    assert body["total"] == TOTAL and body["truncated"] is False
    assert len(body["rows"]) == TOTAL


def test_offset_pages_through_without_duplicates(client, admin_hdr, vitrine):
    """Соседние страницы не пересекаются и в точности повторяют полный список."""
    pages = [_identities(_rows(client, admin_hdr, limit=5, offset=o))
             for o in (0, 5, 10)]
    assert [len(p) for p in pages] == [5, 5, 2]
    assert pages[0] and pages[1] and pages[2]
    assert set(pages[0]) & set(pages[1]) == set()
    assert set(pages[1]) & set(pages[2]) == set()
    full = _identities(_rows(client, admin_hdr, limit=2000))
    assert pages[0] + pages[1] + pages[2] == full      # порядок не поехал
    last = _rows(client, admin_hdr, limit=5, offset=10)
    assert last["truncated"] is False                  # дальше листать некуда


def test_limit_is_validated(client, admin_hdr, vitrine):
    """2001 строку никто не просит осознанно, а 0 и отрицательный offset — ошибка."""
    assert client.get(ROWS_URL, params={**PERIOD, "kind": "vessel", "limit": 2001},
                      headers=admin_hdr).status_code == 422
    assert client.get(ROWS_URL, params={**PERIOD, "kind": "vessel", "limit": 0},
                      headers=admin_hdr).status_code == 422
    assert client.get(ROWS_URL, params={**PERIOD, "kind": "vessel", "offset": -1},
                      headers=admin_hdr).status_code == 422


# ---------------------------------------------------------------------- CSV ----

def test_csv_file_contains_the_whole_selection(client, admin_hdr, vitrine):
    """Файл полнее экрана: строк в нём ровно `total`, а не `len(rows)` из `/rows`."""
    page = _rows(client, admin_hdr, limit=5)
    resp = _csv(client, admin_hdr)
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.content.startswith(b"\xef\xbb\xbf"), "Excel без BOM теряет кириллицу"
    assert "attachment" in resp.headers["content-disposition"]
    assert "vessel-2025-12-05_2025-12-07.csv" in resp.headers["content-disposition"]

    rows = _csv_rows(resp)
    header = rows[0]
    assert header == [c["label"] for c in page["columns"]]
    assert ";" in resp.content.decode("utf-8-sig").splitlines()[0]
    assert len(rows) - 1 == page["total"] == TOTAL
    assert len(rows) - 1 > len(page["rows"])


def test_csv_and_rows_are_the_same_slice(client, admin_hdr, vitrine):
    """Цифры файла и JSONа — из одного разреза: сумма колонки совпадает до тонны."""
    body = _rows(client, admin_hdr, limit=5)          # страница не влияет на итог
    rows = _csv_rows(_csv(client, admin_hdr))
    header, data = rows[0], rows[1:]
    for key in ("plan_shift_t", "fact_shift_t"):
        plan = _label(body["columns"], key)
        assert sum(_numbers(data, header, plan)) == body["totals"][key]


def test_csv_honours_the_key_of_the_drilldown(client, admin_hdr, vitrine):
    """Клик по одному судну выгружает одно судно, а не весь разрез."""
    body = _rows(client, admin_hdr, key="ROWS SHIP A")
    rows = _csv_rows(_csv(client, admin_hdr, key="ROWS SHIP A"))
    assert body["total"] == 6
    assert len(rows) - 1 == body["total"]
    plan = _label(body["columns"], "plan_shift_t")
    # тоннажи того же судна: файл и JSON обязаны называть одни и те же строки
    assert set(_numbers(rows[1:], rows[0], plan)) == {r["plan_shift_t"] for r in body["rows"]}


def test_csv_filename_survives_a_cyrillic_key(client, admin_hdr, vitrine):
    """Ключ «РП - 1/2»: кириллица и слэш не должны ломать заголовок ответа.

    Starlette кодирует заголовки в latin-1: наивный `filename=<ключ>` падал 500
    на самом частом клике дриллдауна — загрузке причала.
    """
    r = client.get(CSV_URL, params={**PERIOD, "kind": "berth", "key": "РП - 1/2"},
                   headers=admin_hdr)
    assert r.status_code == 200, r.text
    head = r.headers["content-disposition"]
    assert "filename*=UTF-8''" in head and "%D0%A0%D0%9F" in head
    assert len(_csv_rows(r)) - 1 == TOTAL


def test_csv_is_not_cached(client, admin_hdr, vitrine):
    """Новая смена обязана попасть в файл сразу: кэш здесь означал бы протухшую выгрузку."""
    assert len(_csv_rows(_csv(client, admin_hdr))) - 1 == TOTAL
    db = SessionLocal()
    try:
        db.add(_vessel(date(2025, 12, 7), "08-20", "ROWS SHIP C", 1300.0))
        db.commit()
    finally:
        db.close()
    data_cache.invalidate()
    try:
        rows = _csv_rows(_csv(client, admin_hdr))
        assert len(rows) - 1 == TOTAL + 1
        plan = _label(_rows(client, admin_hdr, limit=1)["columns"], "plan_shift_t")
        assert sum(_numbers(rows[1:], rows[0], plan)) == PLAN_TOTAL + 1300.0
    finally:
        db = SessionLocal()
        try:
            db.execute(delete(FactVesselOperation)
                       .where(FactVesselOperation.source == SOURCE,
                              FactVesselOperation.vessel == "ROWS SHIP C"))
            db.commit()
        finally:
            db.close()
        data_cache.invalidate()


def test_csv_respects_the_shift_filter(client, admin_hdr, vitrine):
    """Ночь в файле — только ночь: параметр `shift` у обоих эндпоинтов общий."""
    body = _rows(client, admin_hdr, shift="20-08")
    rows = _csv_rows(_csv(client, admin_hdr, shift="20-08"))
    assert body["total"] == TOTAL // 2
    assert len(rows) - 1 == TOTAL // 2
    assert {r[rows[0].index("смена")] for r in rows[1:]} == {"20-08"}


def test_csv_rejects_a_year_long_period(client, admin_hdr, vitrine):
    """Лимит длины периода (п. 5) действует и на файл: выгрузка не обход 400."""
    year = {"from": "2024-01-01", "to": "2025-12-31", "kind": "vessel"}
    assert client.get(CSV_URL, params=year, headers=admin_hdr).status_code == 400
    assert client.get(ROWS_URL, params=year, headers=admin_hdr).status_code == 400


# --------------------------------------------------------------------- права ----

def test_rows_and_csv_require_auth(client, vitrine):
    """Оба эндпоинта — только для авторизованных: в строках витрины цифры порта."""
    assert client.get(ROWS_URL, params={**PERIOD, "kind": "vessel"}).status_code == 401
    assert client.get(CSV_URL, params={**PERIOD, "kind": "vessel"}).status_code == 401
