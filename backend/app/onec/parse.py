"""Разбор «плоских» строк 1С в записи контракта.

Один разбор на два сценария:
  * кабинет сам сходил в HTTP-сервис 1С (http_source.py, ключ `items`);
  * 1С сама прислала срез на POST /api/onec/push (ключи `warehouses`/`balance`/...).

Поля здесь, а не в Pydantic-схемах, потому что контракт намеренно «плоский» и
терпимый к отсутствующим полям: 1С отдаёт готовые строки, а кабинет приводит
типы и не падает от пустых значений (см. docs/03_контракт_1с.md).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from .base import CapacityRec, CargoRec, DynamicsRec, Snapshot, WarehouseRec


def parse_dt(value: Any) -> datetime:
    """ISO8601 или «12.10.2026 18:00»-подобная строка → наивный UTC."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if not value:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    text = str(value).strip().replace("T", " ").replace("Z", "")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text.split(" ")[0])
        except ValueError as exc:
            raise ValueError(f"не разобрали дату «{value}»") from exc


def _num(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return default


def _text(value: Any, default: str = "") -> str:
    return str(value).strip() if value not in (None, "") else default


def _rows(items: Any) -> list[dict]:
    """Разрешаем и `items`, и именованный массив, и просто список строк."""
    if items is None:
        return []
    if isinstance(items, dict):                      # {"items": [...]}
        items = items.get("items", [])
    if not isinstance(items, list):
        raise ValueError("ожидался список строк")
    rows = []
    for row in items:
        if not isinstance(row, dict):
            raise ValueError("строка среза должна быть объектом")
        rows.append(row)
    return rows


def parse_warehouses(items: Any) -> list[WarehouseRec]:
    out: list[WarehouseRec] = []
    for row in _rows(items):
        code = _text(row.get("code") or row.get("warehouse_code"))
        if not code:
            raise ValueError("у склада нет кода")
        out.append(WarehouseRec(
            code=code,
            name=_text(row.get("name"), code),
            kind=_text(row.get("kind"), "warehouse"),
            capacity=_num(row.get("capacity")),
            unit=_text(row.get("unit"), "т"),
        ))
    return out


def parse_cargo(items: Any) -> list[CargoRec]:
    out: list[CargoRec] = []
    for row in _rows(items):
        code = _text(row.get("warehouse_code") or row.get("code"))
        if not code:
            raise ValueError("в строке остатков нет warehouse_code")
        out.append(CargoRec(
            warehouse_code=code,
            cargo_group=_text(row.get("cargo_group"), "Без группы"),
            qty=_num(row.get("qty")),
            weight_t=_num(row.get("weight_t")),
            overdue_weight_t=_num(row.get("overdue_weight_t")),
            avg_age_days=_num(row.get("avg_age_days")),
        ))
    return out


def parse_capacity(items: Any) -> list[CapacityRec]:
    out: list[CapacityRec] = []
    for row in _rows(items):
        code = _text(row.get("warehouse_code") or row.get("code"))
        if not code:
            raise ValueError("в строке ёмкости нет warehouse_code")
        out.append(CapacityRec(
            warehouse_code=code,
            used=_num(row.get("used")),
            capacity=_num(row.get("capacity")),
            unit=_text(row.get("unit"), "т"),
        ))
    return out


def parse_dynamics(items: Any) -> list[DynamicsRec]:
    out: list[DynamicsRec] = []
    for row in _rows(items):
        code = _text(row.get("warehouse_code") or row.get("code"))
        raw_date = row.get("date") or row.get("bucket_date")
        if not code:
            raise ValueError("в строке динамики нет warehouse_code")
        if not raw_date:
            raise ValueError("в строке динамики нет date")
        try:
            bucket = date.fromisoformat(str(raw_date).strip()[:10])
        except ValueError as exc:
            raise ValueError(f"в строке динамики дата не YYYY-MM-DD: {raw_date}") from exc
        out.append(DynamicsRec(
            warehouse_code=code,
            bucket_date=bucket,
            inbound_t=_num(row.get("inbound_t")),
            outbound_t=_num(row.get("outbound_t")),
            processed_t=_num(row.get("processed_t")),
            docs_count=int(_num(row.get("docs_count"))),
            avg_dwell_hours=_num(row.get("avg_dwell_hours")),
        ))
    return out


def snapshot_from_payload(data: Any) -> Snapshot:
    """Срез из тела push-запроса (тот же контракт, что отдаёт HTTP-сервис)."""
    if not isinstance(data, dict):
        raise ValueError("ожидался JSON-объект со срезом")

    warehouses = parse_warehouses(data.get("warehouses", data.get("items")))
    cargo = parse_cargo(data.get("balance", data.get("cargo")))
    capacity = parse_capacity(data.get("capacity"))
    dynamics = parse_dynamics(data.get("dynamics"))

    if not (warehouses or cargo or capacity or dynamics):
        raise ValueError("пустой срез: ни одной строки в warehouses/balance/capacity/dynamics")

    return Snapshot(
        snapshot_at=parse_dt(data.get("snapshot_at")),
        warehouses=warehouses,
        cargo=cargo,
        capacity=capacity,
        dynamics=dynamics,
    )
