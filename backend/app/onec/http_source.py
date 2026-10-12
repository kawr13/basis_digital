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
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

import httpx

from ..config import get_settings
from .base import Snapshot
from .parse import parse_capacity, parse_cargo, parse_dt, parse_dynamics, parse_warehouses

log = logging.getLogger("statcab.onec.http")


class HttpSource:
    name = "http"

    def __init__(self, base_url: str | None = None, login: str | None = None,
                 password: str | None = None, timeout: int | None = None) -> None:
        """None — значит параметр не задан в сторе, берём из .env (как раньше)."""
        s = get_settings()
        base = (s.one_c_base_url if base_url is None else base_url) or ""
        if not base.strip():
            raise RuntimeError("Адрес 1С не задан (onec.base_url / ONE_C_BASE_URL)")
        login = s.one_c_login if login is None else login
        password = s.one_c_password if password is None else password
        self.base = base.strip().rstrip("/")
        self.auth = (login or "", password or "") if (login or "") else None
        try:
            self.timeout = float(timeout) if timeout is not None else float(s.one_c_timeout_sec)
        except (TypeError, ValueError):
            self.timeout = 30.0

    # ------------------------------------------------------------------ запросы --
    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, auth=self.auth, verify=True)

    def _get(self, path: str, params: dict | None = None) -> dict:
        with self._client() as client:
            resp = client.get(f"{self.base}{path}", params=params,
                              headers={"Accept": "application/json"})
            resp.raise_for_status()
            return resp.json()

    def probe(self, days: int = 7) -> list[dict[str, Any]]:
        """Проверка связи: по очереди бьём все четыре метода, ошибки не роняем.

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
        checks: list[dict[str, Any]] = []
        for path, params in targets:
            started = time.perf_counter()
            code: int | None = None
            error: str | None = None
            ok = False
            try:
                with self._client() as client:
                    resp = client.get(f"{self.base}{path}", params=params,
                                      headers={"Accept": "application/json"})
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
