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
* single-flight: параллельные одинаковые запросы считают агрегат один раз, а
  байтовый потолок `MAX_BYTES` ограничивает RAM кэша (п. 32 TASKS2.md).

Данные заводим свои (декабрь 2025) и убираем за собой: файл отчёта диспетчера в
тесты не тянем, а витрина на весь прогон одна.
"""
from __future__ import annotations

import threading
import time
from datetime import date

import pytest
from sqlalchemy import delete, event

from app import db as app_db
from app.models import FactDispatcherShift, FactVesselOperation
from app.services import data_cache


def SessionLocal():  # noqa: N802 — имя фабрики сохранено намеренно
    """Сессия ТЕКУЩЕЙ базы.

    `from app.db import SessionLocal` связывает с фабрикой, а не с базой: тесты
    переезда (test_dbconfig) ставят новый движок на ходу, и импорт на уровне
    модуля остался бы смотреть в прежнюю базу.
    """
    return app_db.SessionLocal()


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

    event.listen(app_db.engine, "before_cursor_execute", listener)
    try:
        yield calls
    finally:
        event.remove(app_db.engine, "before_cursor_execute", listener)


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
    assert data_cache.stats()["entries"] == data_cache.MAX_ENTRIES == 8
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
    assert data_cache.stats()["entries"] == 0
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


# ------------------------------------------------ single-flight и байтовый пол ----

def test_single_flight_body_computed_once():
    """Две нитки и один ключ: считает первая, вторая забирает её байты (п. 32 TASKS2.md).

    Эмулируем путь эндпоинта `get → посчитали → put`: первая нитка «считает»
    медленно, вторая стартует, когда полёт уже зарегистрирован, и обязана
    получить те же байты, ни разу не считая.
    """
    data_cache.invalidate()
    key, started = ("single", "flight"), threading.Event()
    computed: list[int] = []
    got: list[bytes] = []

    def worker() -> None:
        body = data_cache.get(key, "v1")
        if body is not None:              # считали не мы — забрали чужой результат
            got.append(body)
            return
        started.set()
        time.sleep(0.3)                   # «долгий» расчёт агрегата
        computed.append(1)
        got.append(data_cache.put(key, "v1", {"ok": 1}))

    t1 = threading.Thread(target=worker)
    t1.start()
    assert started.wait(2), "лидер не стартовал"
    t2 = threading.Thread(target=worker)
    t2.start()
    t1.join(5)
    t2.join(5)
    assert not t1.is_alive() and not t2.is_alive(), "single-flight не имеет права вешать потоки"
    assert len(computed) == 1, "агрегат обязан считаться один раз"
    assert got == [b'{"ok":1}', b'{"ok":1}']
    assert data_cache.stats()["inflight_joined"] >= 1
    data_cache.invalidate()


def test_single_flight_cached_value_built_once():
    """`cached_value`: медленный build выполняется один раз на параллельные вызовы."""
    data_cache.invalidate()
    calls: list[int] = []
    started = threading.Event()

    def build() -> str:
        calls.append(1)
        started.set()
        time.sleep(0.3)
        return "v-1"

    out: list[str] = []
    t1 = threading.Thread(target=lambda: out.append(data_cache.cached_value(("k",), 60.0, build)))
    t1.start()
    assert started.wait(2), "лидер не стартовал"
    t2 = threading.Thread(target=lambda: out.append(data_cache.cached_value(("k",), 60.0, build)))
    t2.start()
    t1.join(5)
    t2.join(5)
    assert not t1.is_alive() and not t2.is_alive(), "ожидание не имеет права вешать потоки"
    assert sorted(out) == ["v-1", "v-1"]
    assert len(calls) == 1, "значение обязано считаться один раз"
    data_cache.invalidate()


def test_dead_leader_does_not_hang_waiter(monkeypatch):
    """Лидер умер до `put` — ждущий ждёт не дольше `INFLIGHT_TTL` и становится лидером.

    Потолок уменьшаем: проверяем механизм стража, а не то, что ждать приходится
    десять секунд реального времени.
    """
    data_cache.invalidate()
    monkeypatch.setattr(data_cache, "INFLIGHT_TTL", 0.2)
    key = ("ключ", "сбой")
    assert data_cache.get(key, "v1") is None       # лидер-призрак: put не позовём
    out: list = []
    t0 = time.monotonic()
    t = threading.Thread(target=lambda: out.append(data_cache.get(key, "v1")))
    t.start()
    t.join(5)
    assert not t.is_alive(), "смерть лидера не имеет права вешать ждущего"
    assert out == [None], "переждав дедлайн, поток обязан стать лидером сам"
    assert time.monotonic() - t0 < 3.0, "ждать обязаны ~INFLIGHT_TTL, а не вечно"
    data_cache.invalidate()


def test_byte_cap_evicts_oldest(monkeypatch):
    """Потолок по суммарным байтам (п. 32 TASKS2.md): переполнение вытесняет старое.

    `MAX_BYTES` уменьшаем — гонять мегабайты payload'ов ради теста незачем,
    проверяется порядок вытеснения, а не величина цифры.
    """
    data_cache.invalidate()
    monkeypatch.setattr(data_cache, "MAX_BYTES", 1500)
    big = {"x": "0" * 900}                          # ~907 байт в JSON
    assert data_cache.put(("байт", 1), "v1", big)
    assert data_cache.put(("байт", 2), "v1", big)   # вдвоём не влезают — первая ушла
    assert data_cache.get(("байт", 1), "v1") is None, "старая запись вытеснена по байтам"
    assert data_cache.get(("байт", 2), "v1") is not None
    st = data_cache.stats()
    assert st["entries"] == 1 and st["bytes"] <= data_cache.MAX_BYTES
    data_cache.invalidate()


def test_oversized_single_entry_survives(monkeypatch):
    """Единственный ответ больше потолка — всё равно живёт.

    Иначе единственный годовой `overview` (~740 КБ по замерам TASKS2.md)
    вытеснялся бы в момент добавления: кэш «ограничивал» бы RAM ценой
    собственной бесполезности.
    """
    data_cache.invalidate()
    monkeypatch.setattr(data_cache, "MAX_BYTES", 100)
    body = data_cache.put(("байт", "гигант"), "v1", {"x": "1" * 500})
    assert data_cache.get(("байт", "гигант"), "v1") == body
    assert data_cache.stats()["entries"] == 1
    data_cache.invalidate()
