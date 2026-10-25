"""HTTP-источник: читаем срез из HTTP-сервиса, который публикует 1С.

Контракт (см. docs/03_контракт_1с.md):
  GET {base}/warehouses
  GET {base}/balance
  GET {base}/capacity
  GET {base}/dynamics?from=YYYY-MM-DD&to=YYYY-MM-DD
Ответ: {"snapshot_at": ISO8601, "items": [...]}  (для dynamics items обязателен "date").
Доступ: Basic auth (учётка только для чтения, отдельная от пользователей кабинета).

Параметры подключения приходят аргументами конструктора — их берёт стор настроек
(app_settings), а .env остаётся запасным вариантом, если админ ничего не менял.

Таймаута два, и они разные. Рабочий срез ждёт `onec.timeout_sec`: 1С имеет право
думать минуту. Проверка связи в админке ждёт `onec.probe_timeout_sec` (по
умолчанию 8 с на запрос, 3 с на соединение, ~30 с на все четыре GET): клик по
кнопке не должен вешать поток кабинета на две минуты.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, timedelta
from typing import Any

import httpx

from ..config import get_settings
from .base import Snapshot
from .parse import parse_capacity, parse_cargo, parse_dt, parse_dynamics, parse_warehouses

log = logging.getLogger("statcab.onec.http")

# Сколько ждать установленное TCP/TLS-соединение. Заведомо меньше чтения:
# недоступный хост виден через 3 с, а не через весь таймаут проверки.
CONNECT_TIMEOUT_SEC = 3.0

# Сколько запросов в пробе (/warehouses /balance /capacity /dynamics) — по нему
# считается и суммарный лимит длительности проверки.
PROBE_REQUESTS = 4

# ------------------------------------------------------------------- клиенты --
# httpx.Client дорог (TCP + TLS) и при этом потокобезопасен, поэтому держим
# один клиент на процесс для каждого набора «адрес + учётка», а не по клиенту на
# запрос: keep-alive работает, рукопожатие одно.
#
# Ключ — отпечаток подключения (verify зашит в клиента, он пока один — True).
# Сменил админ адрес или пароль в UI — ключ другой: клиент создаётся заново,
# а старый по прежнему адресу закрывается, иначе кабинет ходил бы в прежнюю 1С
# до перезапуска контейнера и держал бы закро́м открытые соединения.
_CLIENTS: dict[tuple[str, str, str], httpx.Client] = {}
_LOCK = threading.Lock()


def _new_client(auth, timeout: float) -> httpx.Client:
    """Точка подмены в тестах: транспорт подставляют вместо настоящего."""
    return httpx.Client(timeout=timeout, auth=auth, verify=True)


def _close_quietly(client: httpx.Client) -> None:
    try:
        client.close()
    except Exception:  # noqa: BLE001 — закрытие не должно ронять ни запрос, ни остановку
        log.debug("HTTP-клиент 1С закрылся с ошибкой", exc_info=True)


def _client_for(base: str, auth, timeout: float) -> httpx.Client:
    """Общий клиент процесса для пары «адрес + учётка» (создаётся один раз)."""
    login, password = auth if auth else ("", "")
    key = (base, login, password)
    with _LOCK:
        client = _CLIENTS.get(key)
        if client is not None:
            return client
        for stale in [k for k in _CLIENTS if k[0] != base]:
            _close_quietly(_CLIENTS.pop(stale))
        client = _new_client(auth, timeout)
        _CLIENTS[key] = client
        log.info("HTTP-клиент 1С: новый клиент для %s", base)
        return client


def close_clients() -> int:
    """Закрыть все клиенты. Зовёт lifespan при остановке приложения."""
    with _LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    for client in clients:
        _close_quietly(client)
    return len(clients)


def _as_number(value: Any, fallback: Any, default: float) -> float:
    """Число из стора/.env: значения приходят и строками, и пустыми."""
    for candidate in (value, fallback):
        if candidate is None or (isinstance(candidate, str) and not candidate.strip()):
            continue
        try:
            return float(candidate)
        except (TypeError, ValueError):
            continue
    return default


class HttpSource:
    name = "http"

    def __init__(self, base_url: str | None = None, login: str | None = None,
                 password: str | None = None, timeout: int | None = None,
                 probe_timeout: int | None = None) -> None:
        """None — значит параметр не задан в сторе, берём из .env (как раньше)."""
        s = get_settings()
        base = (s.one_c_base_url if base_url is None else base_url) or ""
        if not base.strip():
            raise RuntimeError("Адрес 1С не задан (onec.base_url / ONE_C_BASE_URL)")
        login = s.one_c_login if login is None else login
        password = s.one_c_password if password is None else password
        self.base = base.strip().rstrip("/")
        self.auth = (login or "", password or "") if (login or "") else None
        self.timeout = _as_number(timeout, s.one_c_timeout_sec, 30.0)
        self.probe_timeout = _as_number(probe_timeout, s.one_c_probe_timeout_sec, 8.0)

    # ------------------------------------------------------------------ запросы --
    @staticmethod
    def _timeout(total: float) -> httpx.Timeout:
        """Таймаут одного запроса: connect короче — мёртвый адрес виден сразу."""
        total = max(float(total), 0.1)
        return httpx.Timeout(total, connect=min(CONNECT_TIMEOUT_SEC, total))

    def _client(self) -> httpx.Client:
        """Общий клиент процесса — не закрывать (его закрывает close_clients)."""
        return _client_for(self.base, self.auth, self.timeout)

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._client().get(f"{self.base}{path}", params=params,
                                  headers={"Accept": "application/json"},
                                  timeout=self._timeout(self.timeout))
        resp.raise_for_status()
        return resp.json()

    def probe(self, days: int = 7) -> list[dict[str, Any]]:
        """Проверка связи: по очереди бьём все четыре метода, ошибки не роняем.

        Каждый запрос живёт не дольше `onec.probe_timeout_sec`, а вся проверка —
        не дольше PROBE_REQUESTS × того же времени: даже полностью недоступная
        1С не держит поток кабинета дольше ~30 с.

        Возвращает список проверок: path / ok / status / ms / error.
        """
        today = date.today()
        targets: list[tuple[str, dict | None]] = [
            ("/warehouses", None),
            ("/balance", None),
            ("/capacity", None),
            ("/dynamics", {"from": (today - timedelta(days=days - 1)).isoformat(),
                           "to": today.isoformat()}),
        ]
        budget = PROBE_REQUESTS * self.probe_timeout
        deadline = time.perf_counter() + budget
        checks: list[dict[str, Any]] = []
        for path, params in targets:
            started = time.perf_counter()
            code: int | None = None
            error: str | None = None
            ok = False
            left = deadline - started
            if left <= 0:
                # Суммарный лимит выбран не для красоты: без него четыре
                # зависших запроса дают 4 × таймаут занятого потока.
                checks.append({"path": path, "ok": False, "status": None, "ms": 0,
                               "error": f"проверка прервана: суммарный лимит {budget:g} с исчерпан"})
                continue
            try:
                resp = self._client().get(f"{self.base}{path}", params=params,
                                          headers={"Accept": "application/json"},
                                          timeout=self._timeout(min(self.probe_timeout, left)))
                code = resp.status_code
                if resp.status_code >= 400:
                    error = f"HTTP {resp.status_code}"
                else:
                    try:
                        body = resp.json()
                        ok = isinstance(body, dict)
                        if not ok:
                            error = "ответ не объект JSON"
                    except ValueError:
                        error = "ответ не JSON (проверьте Content-Type в 1С)"
            except Exception as exc:  # noqa: BLE001 — сеть/SSL/таймаут, наружу не пускаем
                error = f"{type(exc).__name__}: {exc}"[:300]
            checks.append({
                "path": path,
                "ok": ok,
                "status": code,
                "ms": int((time.perf_counter() - started) * 1000),
                "error": error,
            })
        return checks

    # --------------------------------------------------------------------- срез --
    def fetch(self, days: int = 30) -> Snapshot:
        today = date.today()
        wh_raw = self._get("/warehouses")
        bal_raw = self._get("/balance")
        cap_raw = self._get("/capacity")
        dyn_raw = self._get("/dynamics", {"from": (today - timedelta(days=days - 1)).isoformat(),
                                          "to": today.isoformat()})

        snap_at = parse_dt(bal_raw.get("snapshot_at") or cap_raw.get("snapshot_at"))
        return Snapshot(
            snapshot_at=snap_at,
            warehouses=parse_warehouses(wh_raw.get("items", [])),
            cargo=parse_cargo(bal_raw.get("items", [])),
            capacity=parse_capacity(cap_raw.get("items", [])),
            dynamics=parse_dynamics(dyn_raw.get("items", [])),
        )
