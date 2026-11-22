"""П. 17–18 TASKS2.md: повторы обращений к 1С, суммарный дедлайн и потолок ответа.

Настоящей сети не нужно: транспорт подменяем своим (тот же приём, что в
`test_onec_timeouts.py`). Проверяем поведение, а не текст кода: сколько запросов
ушло, что вернулось наружу и сколько спал планировщик.
"""
from __future__ import annotations

import json
import time

import httpx
import pytest

from app.onec import http_source
from app.onec.http_source import (
    BACKOFF_SEC, MAX_RESPONSE_BYTES, RETRIES, HttpSource, ResponseTooLargeError,
    UpstreamBusyError,
)

BASE = "http://1c.example/StatCabinet"


def make_source(**kw) -> HttpSource:
    kw.setdefault("base_url", BASE)
    kw.setdefault("login", "cab")
    kw.setdefault("password", "secret")
    kw.setdefault("timeout", 5)
    return HttpSource(**kw)


def use_transport(monkeypatch, transport: httpx.BaseTransport) -> None:
    monkeypatch.setattr(http_source, "_new_client",
                        lambda auth, timeout: httpx.Client(transport=transport, auth=auth,
                                                           verify=True))


@pytest.fixture(autouse=True)
def fresh_clients():
    """Клиент у процесса общий — начинать надо с чистого реестра."""
    http_source.close_clients()
    yield
    http_source.close_clients()


class Clock:
    """Подменные часы: паузы считаются, но не отъедают время теста."""

    def __init__(self) -> None:
        self.slept: list[float] = []
        self.now = 1000.0

    def perf_counter(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(http_source, "time", fake)
    return fake


class StatusTransport(httpx.BaseTransport):
    """Первые `fail_first` ответов — с указанным статусом, дальше 200 с телом."""

    def __init__(self, fail_first: int, status: int = 503, body: dict | None = None) -> None:
        self.fail_first = fail_first
        self.status = status
        self.body = {"items": []} if body is None else body
        self.requests = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.requests <= self.fail_first:
            return httpx.Response(self.status, text="сервис занят")
        return httpx.Response(200, json=self.body)


class BoomTransport(httpx.BaseTransport):
    """Транспорт, который всегда рвёт соединение — как упавший балансировщик."""

    def __init__(self) -> None:
        self.requests = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        raise httpx.ConnectError("connection refused", request=request)


class BigBodyTransport(httpx.BaseTransport):
    """Ответ больше потолка: с Content-Length и без него (1С умеет оба)."""

    def __init__(self, size: int, declare: bool) -> None:
        self.size = size
        self.declare = declare
        self.requests = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        body = b"x" * self.size
        headers = {"content-length": str(self.size)} if self.declare else {}
        return httpx.Response(200, content=body, headers=headers)


class RouteTransport(httpx.BaseTransport):
    """Разные ответы на разные методы, как у настоящего HTTP-сервиса 1С."""

    def __init__(self, routes: dict[str, dict]) -> None:
        self.routes = routes
        self.requests: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        for path, body in self.routes.items():
            if request.url.path.endswith(path):
                return httpx.Response(200, json=body)
        return httpx.Response(200, json={"items": []})


# ------------------------------------------------------------------- повторы --

def test_otvet_5xx_povtoryaetsya_i_dozhimaet_do_uspeha(monkeypatch, clock):
    transport = StatusTransport(fail_first=2)
    use_transport(monkeypatch, transport)

    assert make_source()._get("/balance") == {"items": []}
    assert transport.requests == RETRIES + 1
    # Пауза растёт вдвое: первая — BACKOFF_SEC, вторая — удвоенная.
    assert clock.slept == [pytest.approx(BACKOFF_SEC), pytest.approx(BACKOFF_SEC * 2)]


def test_posle_potolka_popytok_oshibka_naruzhu(monkeypatch, clock):
    transport = StatusTransport(fail_first=99)
    use_transport(monkeypatch, transport)

    with pytest.raises(UpstreamBusyError):
        make_source()._get("/balance")
    assert transport.requests == RETRIES + 1
    assert len(clock.slept) == RETRIES          # перед последней попыткой не спим


def test_obryv_konnekta_toze_povtoryaetsya(monkeypatch, clock):
    transport = BoomTransport()
    use_transport(monkeypatch, transport)

    with pytest.raises(httpx.ConnectError):
        make_source()._get("/warehouses")
    assert transport.requests == RETRIES + 1


def test_4xx_ne_povtoryaetsya(monkeypatch, clock):
    """404 — это адрес или контракт: долбить больного смысла нет."""
    transport = StatusTransport(fail_first=99, status=404)
    use_transport(monkeypatch, transport)

    with pytest.raises(httpx.HTTPStatusError):
        make_source()._get("/balance")
    assert transport.requests == 1
    assert clock.slept == []


# --------------------------------------------------------------- пределы и срок --

def test_zagolovok_o_bolshom_otvete_otklonyaetsya_bez_chteniya(monkeypatch, clock):
    monkeypatch.setattr(http_source, "MAX_RESPONSE_BYTES", 4096)
    transport = BigBodyTransport(size=1024 * 1024, declare=True)
    use_transport(monkeypatch, transport)

    with pytest.raises(ResponseTooLargeError):
        make_source()._get("/balance")
    assert transport.requests == 1          # даже не начали читать тело


def test_otvet_bez_content_length_obryaetsya_na_potolke(monkeypatch, clock):
    monkeypatch.setattr(http_source, "MAX_RESPONSE_BYTES", 4096)
    transport = BigBodyTransport(size=1024 * 1024, declare=False)
    use_transport(monkeypatch, transport)

    with pytest.raises(ResponseTooLargeError):
        make_source()._get("/balance")


def test_razmer_otveta_v_norme_prohodit(monkeypatch, clock):
    assert MAX_RESPONSE_BYTES > 1024 * 1024
    transport = BigBodyTransport(size=64 * 1024, declare=True)
    use_transport(monkeypatch, transport)
    with pytest.raises(ValueError):         # тело не JSON — но размер в пределах
        make_source()._get("/balance")


def test_ishcherpannyy_dedlayn_ne_dazhe_stuchitsya(monkeypatch, clock):
    transport = StatusTransport(fail_first=0)
    use_transport(monkeypatch, transport)
    source = make_source()
    with pytest.raises(TimeoutError):
        source._get("/balance", deadline=clock.now - 1)
    assert transport.requests == 0


def test_dedlayn_obryvaet_povtory_ranshe_sroka(monkeypatch, clock):
    """Оставшегося времени не хватает даже на паузу — смысла повторять нет."""
    transport = StatusTransport(fail_first=99)
    use_transport(monkeypatch, transport)
    source = make_source()

    with pytest.raises(UpstreamBusyError):
        source._get("/balance", deadline=clock.now + BACKOFF_SEC / 2)
    assert transport.requests == 1
    assert clock.slept == []


# ---------------------------------------------------------------------- срез --

def test_fetchkladyvaet_preduprezhdeniya_o_bityh_strokah(monkeypatch, clock):
    """Одна битая строка не стоит месяца витрины (п. 18), но должна быть слышна."""
    transport = RouteTransport({
        "/warehouses": {"items": [{"code": "A1", "name": "Склад А"}]},
        "/balance": {"snapshot_at": "2026-11-20 08:00:00",
                     "items": [{"warehouse_code": "A1", "weight_t": 5}]},
        "/capacity": {"items": []},
        "/dynamics": {"items": [{"warehouse_code": "A1", "date": "2026-11-20",
                                 "processed_t": 10},
                                {"warehouse_code": "A1", "date": "не дата"}]},
    })
    use_transport(monkeypatch, transport)

    snap = make_source().fetch(days=7)

    assert [w.code for w in snap.warehouses] == ["A1"]
    assert len(snap.dynamics) == 1
    assert snap.warnings and any("динамик" in w for w in snap.warnings)
    # четыре метода, по одному запросу на каждом: повторов на здоровом источнике нет
    assert len(transport.requests) == 4


def test_zdorovyy_otvet_idet_bez_povtorov_i_pause(monkeypatch, clock):
    """Проверяем, что дедлайн не ломает обычный путь: 4 метода — 4 запроса."""
    transport = RouteTransport({})
    use_transport(monkeypatch, transport)
    snap = make_source().fetch(days=30)
    assert snap.warehouses == [] and snap.dynamics == []
    assert len(transport.requests) == 4
    assert clock.slept == []
