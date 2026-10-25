"""Таймауты обращений к 1С и защита синхронизации от наложения (TASKS.md, п. 10).

Настоящей сети тестам не нужно: транспорт подменяем своим. `httpx.MockTransport`
таймауты не соблюдает в принципе, поэтому «честный» транспорт ниже спит ровно
столько, сколько ему разрешили, и падает по read-таймауту — как это делает
httpcore с реальным сокетом. Что проверяем:

* проба связи живёт по `onec.probe_timeout_sec`, а не по рабочему
  `onec.timeout_sec`, и зависшая 1С превращается в `checks[].error`,
  а не в подвешенный на минуты поток кабинета;
* повторный `POST /api/admin/sync/run`, пока первый не закончился, — 409,
  а планировщик на активной загрузке просто пропускает цикл;
* блокировка синхронизации снимается и после неудачной загрузки;
* общий `httpx.Client` переиспользуется, пересоздаётся при смене адреса 1С
  и закрывается целиком, а не остаётся висеть до сборки мусора.
"""
from __future__ import annotations

import inspect
import threading
import time
from datetime import datetime

import httpx
import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import SyncRun
from app.onec import http_source
from app.onec import sync as sync_module
from app.onec.base import Snapshot
from app.onec.http_source import HttpSource
from app.onec.mock import MockSource
from app.services import app_settings, scheduling

SYNC_URL = "/api/admin/sync/run"
PATHS = ["/warehouses", "/balance", "/capacity", "/dynamics"]


# --------------------------------------------------------------------- утилиты --
class HonestTransport(httpx.BaseTransport):
    """Транспорт, который соблюдает таймауты так же, как httpcore.

    Спит `delay` секунд, но не дольше read-таймаута, который ему передали, —
    и падает по `ReadTimeout`. Одновременно запоминает, какой таймаут запросил
    вызывающий: на нём и проверяем, что проба идёт по своему лимиту.
    """

    def __init__(self, delay: float, body: dict | None = None) -> None:
        self.delay = delay
        self.body = {"items": []} if body is None else body
        self.timeouts: list[dict] = []
        self.requests = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        timeout = request.extensions.get("timeout") or {}
        self.timeouts.append(timeout)
        read = timeout.get("read")
        time.sleep(self.delay if read is None else min(self.delay, read))
        if read is not None and self.delay > read:
            raise httpx.ReadTimeout("read timeout", request=request)
        return httpx.Response(200, json=self.body)


def use_transport(monkeypatch, transport: httpx.BaseTransport) -> None:
    """Поставить источнику наш транспорт вместо настоящего HTTP-клиента."""
    monkeypatch.setattr(
        http_source, "_new_client",
        lambda auth, timeout: httpx.Client(transport=transport, auth=auth, verify=True))


@pytest.fixture()
def fresh_clients():
    """Клиент у процесса общий — тестам надо начинать с чистого реестра."""
    http_source.close_clients()
    yield
    http_source.close_clients()


@pytest.fixture()
def sync_runs_cleaned():
    """Журнал загрузок общий на весь прогон: чужие тесты проверяют в нём порядок."""
    db = SessionLocal()
    try:
        last_id = db.scalar(select(func.max(SyncRun.id))) or 0
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        for run in list(db.scalars(select(SyncRun).where(SyncRun.id > last_id))):
            db.delete(run)
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def no_leaked_threads():
    """Фоновые потоки теста обязаны умирать вместе с тестом."""
    before = {t.name for t in threading.enumerate()}
    yield
    leaked = sorted(t.name for t in threading.enumerate() if t.name not in before)
    assert not leaked, f"тест оставил живые потоки: {leaked}"


def _run_count() -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(func.count(SyncRun.id))) or 0
    finally:
        db.close()


# ------------------------------------------------------------------ проба 1С ---
def test_probe_is_limited_by_probe_timeout(monkeypatch, fresh_clients):
    """Зависшая 1С: четыре ReadTimeout вместо четырёх рабочих таймаутов по 30 с."""
    transport = HonestTransport(delay=5.0)
    use_transport(monkeypatch, transport)
    source = HttpSource(base_url="http://1c.test/StatCabinet", timeout=30, probe_timeout=0.3)

    started = time.perf_counter()
    checks = source.probe()
    spent = time.perf_counter() - started

    assert [c["path"] for c in checks] == PATHS
    for check in checks:
        assert check["ok"] is False and check["error"], check
        assert "ReadTimeout" in check["error"], check
    # ровно то, что чинит пункт: 4 × 0.3 с вместо 4 × 30 с занятого потока
    assert spent < 4 * 0.3 + 0.5, f"проба шла {spent:.1f} с"
    assert transport.requests == len(PATHS)


def test_probe_passes_probe_timeout_not_working_one(monkeypatch, fresh_clients):
    """В транспорт уходит probe_timeout, а connect — 3 с, не рабочий таймаут."""
    transport = HonestTransport(delay=0.0)
    use_transport(monkeypatch, transport)
    source = HttpSource(base_url="http://1c.test/StatCabinet", timeout=30, probe_timeout=5)

    assert all(c["ok"] for c in source.probe())
    assert transport.timeouts, "источник ни разу не спросил таймаут"
    for timeout in transport.timeouts:
        assert timeout["read"] == pytest.approx(5.0)
        assert timeout["connect"] == pytest.approx(3.0), \
            "соединение должно умирать через 3 с, а не через весь таймаут среза"


def test_probe_timeout_comes_from_settings_store(client, settings_snapshot):
    """Стор настроек отдаёт источнику оба таймаута раздельно (путь из админки)."""
    db = SessionLocal()
    try:
        app_settings.apply(db, {"onec.source": "http",
                                "onec.base_url": "http://1c.test/StatCabinet",
                                "onec.timeout_sec": 40, "onec.probe_timeout_sec": 5})
        source = sync_module.get_source(db)
        assert source.timeout == 40, "рабочий срез по-прежнему ждёт общий таймаут"
        assert source.probe_timeout == 5, "проверка связи должна быть заметно короче"
    finally:
        db.close()


def test_probe_default_timeout_is_eight():
    """Дефолт новой настройки — 8 с, минимум стора — 2 с (меньше бессмысленно)."""
    assert app_settings.DEFAULTS["onec.probe_timeout_sec"] == 8
    spec = app_settings.SPEC["onec.probe_timeout_sec"]
    assert (spec.minimum, spec.maximum) == (2, 60)
    assert HttpSource(base_url="http://1c.test/StatCabinet").probe_timeout == 8


# ------------------------------------------------------- общий httpx.Client ----
def test_client_is_shared_and_recreated_on_address_change(monkeypatch, fresh_clients):
    """Один клиент на процесс и на набор параметров, а не по клиенту на запрос."""
    created: list[httpx.Client] = []

    def fake_client(auth, timeout):
        client = httpx.Client(transport=HonestTransport(0.0), auth=auth, verify=True)
        created.append(client)
        return client

    monkeypatch.setattr(http_source, "_new_client", fake_client)

    first = HttpSource(base_url="http://a.test/StatCabinet")._client()
    again = HttpSource(base_url="http://a.test/StatCabinet")._client()
    assert first is again, "keep-alive: источник обязан переиспользовать клиент"
    assert len(created) == 1

    # админ поменял адрес в UI — старый клиент непригоден: его закрываем, а не забываем
    other = HttpSource(base_url="http://b.test/StatCabinet", login="u", password="p")._client()
    assert other is not first
    assert len(created) == 2
    assert first.is_closed, "клиент по прежнему адресу надо закрыть, а не держать открытым"

    assert http_source.close_clients() == 1
    assert other.is_closed, "клиенты должны закрываться, а не висеть до сборки мусора"


def test_lifespan_closes_clients_on_shutdown():
    """Приложение на остановке обязано закрыть общие клиенты."""
    from app import main

    assert "close_clients" in inspect.getsource(main.lifespan), \
        "lifespan не закрывает httpx.Client: соединение переживёт остановку сервиса"


# ------------------------------------------------------ наложение синхронизации --
class SleepingSource:
    """Источник, который «грузит 1С», пока его держат: активность для 409."""

    name = "sleeping"

    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self.started, self.release = started, release

    def fetch(self, days: int = 30) -> Snapshot:
        self.started.set()
        self.release.wait(timeout=15)
        return Snapshot(snapshot_at=datetime(2020, 1, 15, 10, 0))


def test_second_sync_while_active_is_409(client, admin_hdr, monkeypatch,
                                         sync_runs_cleaned, no_leaked_threads):
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(sync_module, "get_source",
                        lambda db=None: SleepingSource(started, release))

    first: dict[str, int] = {}

    def kick() -> None:
        first["code"] = client.post(SYNC_URL, headers=admin_hdr).status_code

    thread = threading.Thread(target=kick, name="sync-first")
    thread.start()
    assert started.wait(10), "первая синхронизация так и не началась"
    try:
        again = client.post(SYNC_URL, headers=admin_hdr)
        assert again.status_code == 409, again.text
        assert "уже выполняется" in again.json()["detail"], again.text

        # планировщик на активной загрузке не заводится второй раз: max_instances=1
        # спасает job только от самого себя, от ручной кнопки — не от чего
        before = _run_count()
        scheduling.sync_job()
        assert _run_count() == before, "плановая синхронизация наложилась на ручную"
    finally:
        release.set()
        thread.join(15)

    assert not thread.is_alive(), "поток синхронизации завис после отпускания"
    assert first["code"] == 200, "первая загрузка должна была отработать"


def test_sync_lock_is_released_after_failure(client, admin_hdr, monkeypatch, sync_runs_cleaned):
    """Неудачная загрузка не должна навечно занимать блокировку."""

    class BrokenSource:
        name = "broken"

        def fetch(self, days: int = 30) -> Snapshot:
            raise RuntimeError("1С не отвечает")

    monkeypatch.setattr(sync_module, "get_source", lambda db=None: BrokenSource())
    failed = client.post(SYNC_URL, headers=admin_hdr)
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "error"

    monkeypatch.setattr(sync_module, "get_source", lambda db=None: MockSource())
    again = client.post(SYNC_URL, headers=admin_hdr)
    assert again.status_code == 200, "после ошибки синхронизация осталась заблокирована"
    assert again.json()["status"] == "ok"
