"""Кэш витрины диспетчера: байты ответа, ETag и протухание по версии данных.

Что проверяем и почему именно это:

* повторный `/overview` не трогает фактовые таблицы вообще — иначе кэш экономит
  только на Python, а 700 КБ JSON сериализовались бы по-прежнему на каждый запрос;
* новая смена в витрине меняет ответ без ручного сброса — версионирование
  важнее `Cache-Control`, иначе импорт `.xlsm` не показывался бы до двух минут;
* `If-None-Match` отдаёт 304 без тела — на этом живёт опрос страницы раз в
  15 секунд (см. TASKS.md, п. 14);
* `period.requested_from/requested_to` — мутация payload'а в эндпоинте, поэтому
  разные сырые параметры обязаны давать разные записи кэша, а не «чужие» даты;
* LRU на 8 записей и `invalidate()` — границы того, что кэш обещает.

Данные заводим свои (декабрь 2025) и убираем за собой: файл отчёта диспетчера в
тесты не тянем, а витрина на весь прогон одна.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import delete, event

from app.db import SessionLocal, engine
from app.models import FactDispatcherShift, FactVesselOperation
from app.services import data_cache

PERIOD = {"from": "2025-12-01", "to": "2025-12-03"}
URL = "/api/dispatcher/overview"


def _shift(day: date, shift: str, *, present: float = 10.0, staff: float = 20.0) -> FactDispatcherShift:
    return FactDispatcherShift(
        report_date=day, shift=shift, sheet=f"{day:%m-%d}", dispatcher="Кэш Т.Т.",
        crew_present=present, present_total=present, staff_total=staff,
        rs_ok_start=4, rs_repair_start=1, rs_ok_end=4, rs_repair_end=1, rs_total=5,
        source="test",
    )


def _vessel(day: date, shift: str, vessel: str, tons: float) -> FactVesselOperation:
    return FactVesselOperation(
        report_date=day, shift=shift, berth="РП - 1/2", vessel=vessel,
        cargo_group="генгруз", plan_shift_t=tons, fact_shift_t=tons,
        machine_hours=10, downtime_hours=1, source="test",
    )


@pytest.fixture
def fact_queries():
    """Список SQL-запросов к таблицам витрины (`before_cursor_execute` на engine).

    Слушатель вешаем на движок: TestClient крутит приложение в другом потоке,
    а engine один — счётчик видит запросы всех потоков, а лишние отсекаются по
    имени таблицы.
    """
    calls: list[str] = []

    def listener(conn, cursor, statement, parameters, context, executemany) -> None:
        if "fact_" in statement.lower():
            calls.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    try:
        yield calls
    finally:
        event.remove(engine, "before_cursor_execute", listener)


@pytest.fixture(scope="module")
def vitrine():
    """Две смены синтетической витрины (01 и 03 декабря); за собой убираем —
    витрина на весь прогон одна, и чужие тесты читают те же таблицы."""
    data_cache.invalidate()
    db = SessionLocal()
    try:
        rows = [
            _shift(date(2025, 12, 1), "08-20"),
            _vessel(date(2025, 12, 1), "08-20", "CACHE SHIP", 100.0),
            _shift(date(2025, 12, 3), "20-08"),
            _vessel(date(2025, 12, 3), "20-08", "CACHE SHIP", 50.0),
        ]
        db.add_all(rows)
        db.commit()
    finally:
        db.close()
    data_cache.invalidate()
    yield
    db = SessionLocal()
    try:
        for model in (FactVesselOperation, FactDispatcherShift):
            db.execute(delete(model).where(model.source == "test",
                                           model.report_date >= date(2025, 12, 1)))
        db.commit()
    finally:
        db.close()
    data_cache.invalidate()


def _overview(client, hdr, **params) -> dict:
    r = client.get(URL, params=params, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ кэш ответов ----

def test_repeat_overview_touches_no_fact_tables(client, admin_hdr, vitrine, fact_queries):
    """Второй запрос того же периода — ни одного SQL к фактовым таблицам."""
    data_cache.invalidate()
    first = client.get(URL, params=PERIOD, headers=admin_hdr)
    assert first.status_code == 200
    assert first.headers["cache-control"] == "private, no-cache"
    assert first.headers["etag"]
    assert len(fact_queries) > 0, "первый запрос обязан читать витрину"

    fact_queries.clear()
    second = client.get(URL, params=PERIOD, headers=admin_hdr)
    assert second.status_code == 200
    assert fact_queries == [], f"повторный запрос сходил в БД: {fact_queries}"
    assert second.content == first.content, "из кэша должны уходить те же байты"
    assert second.headers["etag"] == first.headers["etag"]


def test_if_none_match_gets_304(client, admin_hdr, vitrine):
    """Актуальный ETag → 304 без тела, чужой → обычный 200 с ответом."""
    etag = client.get(URL, params=PERIOD, headers=admin_hdr).headers["etag"]

    r = client.get(URL, params=PERIOD, headers={**admin_hdr, "If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == etag

    weak = client.get(URL, params=PERIOD, headers={**admin_hdr, "If-None-Match": f"W/{etag}"})
    assert weak.status_code == 304, "слабый ETag — это тот же самый ответ"

    other = client.get(URL, params=PERIOD, headers={**admin_hdr,
                                                    "If-None-Match": '"deadbeef"'})
    assert other.status_code == 200
    assert other.json()["kpi"]["throughput_t"] == 150.0


def test_requested_period_is_not_shared(client, admin_hdr, vitrine):
    """Один разрешённый период, разные сырые параметры — разные requested_*."""
    hi = client.get("/api/dispatcher/meta", headers=admin_hdr).json()["date_max"]

    both = _overview(client, admin_hdr, **{"from": "2025-12-01", "to": hi})
    tail = _overview(client, admin_hdr, **{"from": "2025-12-01"})
    bare = _overview(client, admin_hdr)
    day = _overview(client, admin_hdr, **{"from": "2025-12-01", "to": hi, "shift": "08-20"})

    assert {k: both["period"][k] for k in ("from", "to", "days")} == \
        {"from": "2025-12-01", "to": hi, "days": 2}
    assert tail["period"]["from"] == both["period"]["from"] and tail["period"]["to"] == hi
    assert (both["period"]["requested_from"], both["period"]["requested_to"]) == ("2025-12-01", hi)
    assert (tail["period"]["requested_from"], tail["period"]["requested_to"]) == ("2025-12-01", None)
    assert (bare["period"]["requested_from"], bare["period"]["requested_to"]) == (None, None)
    # фильтр смены — тоже отдельная запись, а не порча соседнего ответа
    assert day["shift_filter"] == "08-20" and both["shift_filter"] == "all"
    assert _overview(client, admin_hdr, **{"from": "2025-12-01", "to": hi})["shift_filter"] == "all"


def test_validation_error_never_enters_cache(client, admin_hdr, vitrine):
    """400 за перевёрнутый период возвращается до кэша и в кэше не лежит."""
    r = client.get(URL, params={"from": "2025-12-03", "to": "2025-12-01"}, headers=admin_hdr)
    assert r.status_code == 400
    assert "etag" not in r.headers
    assert _overview(client, admin_hdr, **PERIOD)["kpi"]["throughput_t"] == 150.0


def test_freshness_is_not_cached(client, admin_hdr, vitrine):
    """`/api/meta/freshness` — он про свежесть данных, кэш там запрещён постановкой."""
    r = client.get("/api/meta/freshness", headers=admin_hdr)
    assert r.status_code == 200
    assert "etag" not in r.headers and "cache-control" not in r.headers


# ------------------------------------------------------------------- версии и LRU ----

def test_new_shift_busts_cache(client, admin_hdr, vitrine, monkeypatch):
    """Смена, появившаяся в витрине, обязана попасть в ответ без ручного сброса."""
    monkeypatch.setattr(data_cache, "VERSION_TTL", 0.0)   # не ждать 2 с на версию
    assert _overview(client, admin_hdr, **PERIOD)["kpi"]["throughput_t"] == 150.0

    db = SessionLocal()
    try:
        db.add(_shift(date(2025, 12, 2), "08-20"))
        db.add(_vessel(date(2025, 12, 2), "08-20", "NEW SHIP", 250.0))
        db.commit()
    finally:
        db.close()

    ov = _overview(client, admin_hdr, **PERIOD)
    assert ov["kpi"]["throughput_t"] == 400.0
    assert ov["kpi"]["days"] == 3

    db = SessionLocal()
    try:
        for model in (FactVesselOperation, FactDispatcherShift):
            db.execute(delete(model).where(model.source == "test",
                                           model.report_date == date(2025, 12, 2)))
        db.commit()
    finally:
        db.close()


def test_version_survives_empty_table_and_is_asked_once(fact_queries):
    """Версия — один запрос к девяти таблицам; пустая таблица её не ломает."""
    data_cache.invalidate()
    db = SessionLocal()
    try:
        first = data_cache.version(db)
        assert len(fact_queries) == 1, "версия — один запрос `UNION ALL`, а не девять"
        parts = dict(p.split("=", 1) for p in first.split("|"))
        assert set(parts) == {m.__tablename__ for m in data_cache.FACT_TABLES}
        # пустая таблица даёт None: он сворачивается в заглушку, а не роняет сборку
        assert all(parts[name] for name in parts)
        assert first == data_cache.version(db)
        assert len(fact_queries) == 1, "в пределах VERSION_TTL версию не переспрашивают"
    finally:
        db.close()
    data_cache.invalidate()


def test_lru_keeps_only_eight_entries():
    """После 12 различных ключей в кэше остаётся максимум 8 записей."""
    data_cache.invalidate()
    for i in range(12):
        assert data_cache.put(("ключ", i), "v1", {"i": i})
    assert data_cache.info()["entries"] == data_cache.MAX_ENTRIES == 8
    assert data_cache.get(("ключ", 0), "v1") is None, "самые старые вытеснены"
    assert data_cache.get(("ключ", 11), "v1") == b'{"i":11}'


def test_put_serializes_dates_and_gets_back_same_bytes():
    """В payload живут `date` — байты должны совпадать с тем, что отдал бы FastAPI."""
    data_cache.invalidate()
    payload = {"bucket_date": date(2025, 12, 1), "kpi": {"throughput_t": 150.0}}
    body = data_cache.put(("ключ", "дата"), "v1", payload)
    assert body == b'{"bucket_date":"2025-12-01","kpi":{"throughput_t":150.0}}'
    assert data_cache.get(("ключ", "дата"), "v1") == body
    assert data_cache.etag(body).strip('"') != data_cache.etag(body + b" ").strip('"')
    assert data_cache.get(("ключ", "дата"), "v2") is None, "другая версия — промах"
    data_cache.invalidate()


def test_invalidate_forces_recount(client, admin_hdr, vitrine, fact_queries):
    """Ручной сброс заставляет считать заново — им пользуется импорт `.xlsm`."""
    data_cache.invalidate()
    assert client.get(URL, params=PERIOD, headers=admin_hdr).status_code == 200

    fact_queries.clear()
    assert client.get(URL, params=PERIOD, headers=admin_hdr).status_code == 200
    assert fact_queries == []

    data_cache.invalidate()
    assert data_cache.info()["entries"] == 0
    assert client.get(URL, params=PERIOD, headers=admin_hdr).status_code == 200
    assert fact_queries, "после invalidate() ответ обязан быть пересобран"


def test_import_resets_cache(client, admin_hdr, vitrine, monkeypatch):
    """Импорт `.xlsm` сбрасывает кэш сам: у строк-деталей своей updated_at нет.

    Файл не разбираем и в витрину ничего не кладём — проверяем только связку
    «импорт отдал управление обратно → кэш пуст».
    """
    reset: list[int] = []
    monkeypatch.setattr(data_cache, "invalidate", lambda: reset.append(1))

    class _Shift:
        report_date = date(2025, 12, 1)

    class _Snapshot:
        shifts = [_Shift()]

    monkeypatch.setattr("app.api.dispatcher.parse_workbook", lambda src: _Snapshot())
    monkeypatch.setattr("app.api.dispatcher.apply_snapshot", lambda db, snap: {"shift": 1})
    monkeypatch.setattr("app.api.dispatcher.describe", lambda snap: "1 смена")

    r = client.post("/api/admin/dispatcher/import",
                    json={"content_base64": "aGk=", "filename": "тест.xlsm"},
                    headers=admin_hdr)
    assert r.status_code == 200, r.text
    assert reset, "после импорта кэш обязан быть сброшен"
