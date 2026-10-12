"""HTTP-источник: читаем срез из HTTP-сервиса, который публикует 1С.

Контракт (см. docs/03_контракт_1с.md):
  GET {base}/warehouses
  GET {base}/balance
  GET {base}/capacity
  GET {base}/dynamics?from=YYYY-MM-DD&to=YYYY-MM-DD
Ответ: {"snapshot_at": ISO8601, "items": [...]}  (для dynamics items обязателен "date").
Доступ: Basic auth (учётка только для чтения, отдельная от пользователей кабинета).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx

from ..config import get_settings
from .base import CapacityRec, CargoRec, DynamicsRec, Snapshot, WarehouseRec


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(value.replace("T", " ").replace("Z", ""))
    except ValueError:
        return datetime.utcnow()


class HttpSource:
    name = "http"

    def __init__(self) -> None:
        s = get_settings()
        if not s.one_c_base_url:
            raise RuntimeError("ONE_C_BASE_URL не задан — источник http недоступен")
        self.base = s.one_c_base_url.rstrip("/")
        self.auth = (s.one_c_login, s.one_c_password) if s.one_c_login else None
        self.timeout = s.one_c_timeout_sec

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base}{path}"
        with httpx.Client(timeout=self.timeout, auth=self.auth, verify=True) as client:
            resp = client.get(url, params=params, headers={"Accept": "application/json"})
            resp.raise_for_status()
            return resp.json()

    def fetch(self, days: int = 30) -> Snapshot:
        today = date.today()
        wh_raw = self._get("/warehouses")
        bal_raw = self._get("/balance")
        cap_raw = self._get("/capacity")
        dyn_raw = self._get("/dynamics", {"from": (today - timedelta(days=days - 1)).isoformat(),
                                          "to": today.isoformat()})

        snap_at = _parse_dt(bal_raw.get("snapshot_at") or cap_raw.get("snapshot_at"))

        warehouses = [
            WarehouseRec(code=w["code"], name=w["name"], kind=w.get("kind", "warehouse"),
                         capacity=float(w.get("capacity") or 0), unit=w.get("unit", "т"))
            for w in wh_raw.get("items", [])
        ]
        cargo = [
            CargoRec(warehouse_code=c["warehouse_code"], cargo_group=c.get("cargo_group", "Без группы"),
                     qty=float(c.get("qty") or 0), weight_t=float(c.get("weight_t") or 0),
                     overdue_weight_t=float(c.get("overdue_weight_t") or 0),
                     avg_age_days=float(c.get("avg_age_days") or 0))
            for c in bal_raw.get("items", [])
        ]
        capacity = [
            CapacityRec(warehouse_code=c["warehouse_code"], used=float(c.get("used") or 0),
                        capacity=float(c.get("capacity") or 0), unit=c.get("unit", "т"))
            for c in cap_raw.get("items", [])
        ]
        dynamics = [
            DynamicsRec(warehouse_code=d["warehouse_code"],
                        bucket_date=date.fromisoformat(d["date"]),
                        inbound_t=float(d.get("inbound_t") or 0),
                        outbound_t=float(d.get("outbound_t") or 0),
                        processed_t=float(d.get("processed_t") or 0),
                        docs_count=int(d.get("docs_count") or 0),
                        avg_dwell_hours=float(d.get("avg_dwell_hours") or 0))
            for d in dyn_raw.get("items", [])
        ]
        return Snapshot(snapshot_at=snap_at, warehouses=warehouses,
                        cargo=cargo, capacity=capacity, dynamics=dynamics)
