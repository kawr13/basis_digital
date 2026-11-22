"""П. 25 TASKS2.md: request-id на каждом ответе и одна строка доступа.

Проверяем набор полей строки, а не её побайтовый формат: порядок слов вправе
измениться, «кто, где, сколько, под каким id» — нет. Отдельно и главное: в лог
не должны попадать ни query (там токены проб и refresh), ни тело запроса (там
пароли).
"""
from __future__ import annotations

import asyncio
import logging
import re

import pytest
from starlette.requests import Request
from starlette.types import Message

from fastapi.testclient import TestClient

from app.main import app, unhandled
from app.services import access_log

ACCESS = "statcab.access"

#: Правило проверяем своей регуляркой, а не `access_log.ID_RE`: тест не должен
#: принимать id только потому, что сервер сгенерировал его сам.
ID_TEST = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")

SECRET = "secret-iz-query-9e3f"


@pytest.fixture
def access(caplog):
    """caplog, сведённый к строкам логгера доступа.

    Уровень поднимаем явно: без этого INFO-строки кабинета до обработчика pytest
    просто не долетели бы, и тест молча проверял бы пустоту.
    """
    caplog.set_level(logging.INFO, logger=ACCESS)
    caplog.clear()

    def lines(fragment: str | None = None) -> list[str]:
        got = [r.getMessage() for r in caplog.records if r.name == ACCESS]
        return got if fragment is None else [line for line in got if fragment in line]

    return lines


def _id_of(response) -> str:
    return response.headers["x-request-id"]


def _scope(**kw) -> dict:
    """Минимальный HTTP-scope для проверки прослойки без HTTP-стека."""
    base = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "path": "/api/x", "raw_path": b"/api/x", "root_path": "",
            "scheme": "http", "query_string": b"",
            "headers": [(b"host", b"statcab")],
            "client": ("10.0.0.1", 5000), "server": ("statcab", 80)}
    base.update(kw)
    return base


async def _run(middleware_app, scope: dict) -> list[Message]:
    """Прогнать запрос через `AccessLogMiddleware` и собрать отданные сообщения."""
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await access_log.AccessLogMiddleware(middleware_app)(scope, receive, send)
    return sent


# -------------------------------------------------------------- request-id ----

def test_kazhdyy_otvet_soderzhit_request_id(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert ID_TEST.match(_id_of(r)), _id_of(r)


def test_prislannyy_id_pereispolzuetsya(client):
    """Свой id с фронта — тот же id в логах: иначе заявка «вот этот запрос» ни о чём."""
    sent = "front-2026-11-21.a_b-9"
    r = client.get("/healthz", headers={"X-Request-ID": sent})
    assert r.status_code == 200
    assert _id_of(r) == sent


@pytest.mark.parametrize("bad", ["", " ", "dva slova", "x" * 65, "id;rm -rf", "a/b", "=42"])
def test_nevalidnyy_id_zamenyaetsya_svoim(client, bad):
    """Заголовок придумывает клиент: в лог и наружу уходит только безопасный id."""
    r = client.get("/healthz", headers={"X-Request-ID": bad})
    given = _id_of(r)
    assert given != bad
    assert ID_TEST.match(given), given


def test_id_est_na_404_i_na_401(client):
    """Идентификатор нужен именно когда что-то пошло не так — на любом ответе."""
    missing = client.get("/api/net-takoy-ruchki")
    denied = client.get("/api/auth/me")
    assert missing.status_code == 404 and denied.status_code == 401
    assert ID_TEST.match(_id_of(missing)) and ID_TEST.match(_id_of(denied))


# ----------------------------------------------------------- строка доступа ----

def test_stroka_dostupa_polnaya(client, access):
    r = client.get("/healthz", headers={"X-Request-ID": "rid-stroka-1"})
    assert r.status_code == 200
    lines = access("/healthz")
    assert len(lines) == 1, f"ровно одна строка на запрос: {lines}"
    line = lines[0]
    assert "GET /healthz 200" in line, line
    assert re.search(r"\d+(\.\d+)?ms ", line), f"нет длительности: {line}"
    assert re.search(r"\d+B ", line), f"нет размера ответа: {line}"
    assert "rid=rid-stroka-1" in line, line
    assert "login=-" in line, "без токена — прочерк: форма строки не плывёт"


def test_login_v_stroke_beretsya_iz_proverennogo_sostoyaniya(client, access, admin_hdr):
    """Логин пишет ручка, уже проверившая токен (`deps.current_user`), а не прослойка."""
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/auth/me", headers=admin_hdr).status_code == 200
    anon = access("GET /healthz ")
    user = access("GET /api/auth/me ")
    assert len(anon) == 1 and len(user) == 1, anon + user
    assert "login=-" in anon[0], anon[0]
    assert "login=admin" in user[0], user[0]


def test_odna_stroka_i_na_ozhibke(client, access):
    """401 — тоже запрос: строка одна, и статус в ней настоящий."""
    assert client.get("/api/admin/users").status_code == 401
    lines = access("/api/admin/users")
    assert len(lines) == 1, lines
    assert "401" in lines[0], lines[0]


# ------------------------------------------------------------------ секреты ----

def test_token_iz_query_ne_popadaet_v_log(client, access):
    """Проба принимает `?token=…` (п. 19), refresh — `?refresh_token=…` (п. 4).

    Поэтому в строке доступа путь без query: один такой запрос в проде иначе
    оставил бы рабочий токен в файле лога, который читают не только разработчики.
    """
    assert client.get(f"/readyz?token={SECRET}").status_code in (200, 401, 503)
    # refresh берёт токен сначала из cookie, а потом уже из `?refresh_token=`
    # (п. 4), а общий клиент сессии залогинен — стучимся чистым клиентом, иначе
    # проверялся бы cookie, а не query, и 401 не случился бы никогда.
    assert TestClient(app).post(f"/api/auth/refresh?refresh_token={SECRET}").status_code == 401
    lines = access()
    assert SECRET not in "\n".join(lines), lines
    assert all("?" not in line for line in lines), lines
    assert access("/readyz ") and access("/api/auth/refresh "), lines


def test_parol_iz_tela_ne_popadaet_v_log(client, access):
    """Прослойка тело не читает — значит пароль в лог физически не доезжает."""
    password = "Sentinel-Parol-42"
    r = client.post("/api/auth/login", json={"login": "net-takogo-42", "password": password})
    assert r.status_code in (400, 401, 403), r.status_code
    lines = access("POST /api/auth/login")
    assert len(lines) == 1, lines
    assert password not in lines[0], lines[0]


# ---------------------------------------------------- связка с обработчиком 500 ----

def test_request_id_v_soobshchenii_ob_neobrabotannoy_oshibke(caplog):
    """`log.exception` без id — строка, которую не связать со строкой доступа."""
    request = Request(_scope(path="/api/umiraet", raw_path=b"/api/umiraet", headers=[]))
    setattr(request.state, access_log.REQUEST_ID_KEY, "rid-traceback")
    caplog.set_level(logging.ERROR, logger="statcab")
    response = asyncio.run(unhandled(request, RuntimeError("проверка журнала")))
    assert response.status_code == 500
    assert "rid-traceback" in caplog.text


def test_id_iz_prosloyki_viden_ruchke_a_zagolovok_otvetu(access):
    """`request.state` и заголовок ответа несут один и тот же id; query отсекается.

    Прослойка — голый ASGI, поэтому проверяем её напрямую: состояние доходит до
    ручки через общий `scope["state"]`, X-Request-ID дописан в `response.start`,
    а `?token=` в строке доступа не появляется даже здесь, без HTTP-стека.
    """
    seen: dict = {}

    async def endpoint(scope, receive, send) -> None:
        seen.update(scope.get("state") or {})
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    sent = asyncio.run(_run(endpoint, _scope(query_string=f"token={SECRET}".encode(),
                                             headers=[(b"host", b"statcab"),
                                                      (b"x-request-id", b"rid-golyj-asgi")])))

    assert seen.get(access_log.REQUEST_ID_KEY) == "rid-golyj-asgi"
    assert dict(sent[0]["headers"])[b"x-request-id"] == b"rid-golyj-asgi"
    assert sent[0]["status"] == 204
    lines = access("rid-golyj-asgi")
    assert len(lines) == 1, lines
    assert "204" in lines[0] and SECRET not in lines[0], lines[0]


def test_zagolovok_ne_dupliruetsya_esli_ruchka_postavila_svoy(access):
    """Свой X-Request-ID у ручки: в ответе и в логе остаётся один id — тот, что в логе."""
    async def endpoint(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-length", b"2"), (b"x-request-id", b"chuzhoy")]})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    sent = asyncio.run(_run(endpoint, _scope()))
    ids = [v for k, v in sent[0]["headers"] if k.lower() == b"x-request-id"]
    assert len(ids) == 1 and ID_TEST.match(ids[0].decode()), ids
    assert ids[0] != b"chuzhoy"
    lines = access("GET /api/x ")
    assert len(lines) == 1 and f"rid={ids[0].decode()}" in lines[0], lines
