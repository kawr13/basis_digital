"""П. 25 TASKS2.md: request-id на каждый запрос и одна строка доступа.

Зачем: без общего идентификатора строки одного запроса — traceback из
`unhandled`, запись аудита, предупреждение сервиса — не склеить в одну картину,
а вопрос «кто и как долго смотрел» без метода, пути и длительности не закрыться.

Три правила строки:
- логгер `statcab.access`, ровно одна строка на запрос;
- query не логируется вовсе: секреты ездят именно в нём (`/readyz?token=…` —
  п. 19, `?refresh_token=…` — п. 4), файл лога их увидеть не должен;
- тело запроса не читается вообще: прослойка только считает байты ответа,
  поэтому пароль из формы в лог не может попасть в принципе.
"""
from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("statcab.access")

#: Заголовок, который уходит клиенту и принимается от него.
HEADER = "X-Request-ID"
_HEADER = HEADER.lower().encode()          # заголовки в ASGI — нижний регистр

#: Ключ request.state: по нему id читают обработчик ошибок и аудит.
REQUEST_ID_KEY = "request_id"

#: Клиентский id принимаем только «как есть»: символы, безопасные для заголовка
#: и для grep по логу. Длиннее 64 — уже не идентификатор, а попытка раздуть
#: строку лога или пропихнуть в заголовок что-то лишнее.
ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")

#: 16 шестнадцатеричных — 64 бита: совпадений в логе не ждём, и строка лога
#: от id не расползается (полный uuid4.hex вдвое длиннее).
ID_LEN = 16

#: Путь приходит от клиента, поэтому ограничен: простыня в URL не должна
#: раздувать строку лога.
MAX_PATH = 200

#: Чем заменяем нечитаемое: перевод строки в пути — это подделка строки лога.
_FILLER = "_"

#: Значение «неизвестно» для лога: форма строки не должна зависеть от того,
#: был ли человек авторизован.
NONE = "-"


def new_request_id() -> str:
    """Короткий идентификатор для запроса без заголовка клиента."""
    return uuid.uuid4().hex[:ID_LEN]


def request_id_of(request: Request) -> str:
    """Request-id запроса для произвольного места — обработчика ошибок, аудита.

    Читается из `scope["state"]`, а не из `request.state`: второй бросает
    AttributeError, если прослойка на запрос не отработала (веб-сокет, тестовый
    Request), а формат сообщения ломать нельзя.
    """
    state = request.scope.get("state") or {}
    return str(state.get(REQUEST_ID_KEY) or NONE)


def safe_path(request: Request) -> str:
    """Путь без query и без управляющих символов.

    Query отбрасываем намеренно: токены кабинет принимает из параметров строки
    запроса (проба готовности, refresh), и один залогиненный запрос тогда
    оставлял бы секрет в файле лога. Управляющие символы заменяем: ASGI-сервер
    раскодировает проценты, и `%0A` в URL дописал бы в лог чужую строку.
    """
    raw = request.url.path
    # Управляющие символы и DEL; не-ASCII (пути с кириллицей) оставляем как есть.
    cleaned = "".join(_FILLER if (c < " " or c == "\x7f") else c for c in raw)
    return cleaned[:MAX_PATH] or _FILLER


def login_of(scope: Scope) -> str:
    """Кто стоял за запросом, без повторной расшифровки токена.

    Источник — `request.state.login`: его кладёт `deps.current_user`, уже
    проверив подпись и активность пользователя. Делать свой `decode_access_token`
    ради строки лога — платить верификацией подписи за каждый запрос и всё
    равно не дать логина (в JWT есть только `sub`).
    """
    state = scope.get("state") or {}
    login = state.get("login")
    if not login:
        # Запасной вариант: в state могли оставить объект пользователя.
        try:
            login = getattr(state.get("user"), "login", None)
        except Exception:  # noqa: BLE001 — сессия к моменту записи лога закрыта
            login = None
    return str(login) if login else NONE


class AccessLogMiddleware:
    """Request-id и строка доступа на каждый HTTP-запрос.

    Прослойка чистая ASGI, а не `BaseHTTPMiddleware`: тот собирает ответ целиком
    в памяти (выгрузки CSV/PDF — мегабайты), считает лишь прочитанное и рвёт
    поток. Здесь байты считаются по ходу `send`, а длительность меряется вокруг
    всего приложения — вместе с сериализацией и отдачей ответа.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":          # websocket/lifespan — без строки доступа
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        rid = request.headers.get(HEADER, "")
        if not ID_RE.fullmatch(rid):
            rid = new_request_id()
        setattr(request.state, REQUEST_ID_KEY, rid)   # видно ручке и обработчику ошибок

        method = request.method
        path = safe_path(request)
        code = 500                            # если приложение упало — строка всё равно нужна
        sent = 0
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal code, sent
            if message["type"] == "http.response.start":
                code = message["status"]
                # id дописываем здесь: `response.start` — последнее место, где
                # заголовки ещё можно поменять. Чужой X-Request-ID снимаем:
                # ручка не имеет права оставить в ответе второй id.
                others = [h for h in message.get("headers", []) if h[0].lower() != _HEADER]
                message["headers"] = others + [(_HEADER, rid.encode())]
            elif message["type"] == "http.response.body":
                sent += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            ms = (time.perf_counter() - started) * 1000.0
            log.info("%s %s %d %.1fms %dB rid=%s login=%s",
                     method, path, code, ms, sent, rid, login_of(scope))


__all__ = ["AccessLogMiddleware", "HEADER", "ID_RE", "REQUEST_ID_KEY", "login_of",
           "new_request_id", "request_id_of", "safe_path"]
