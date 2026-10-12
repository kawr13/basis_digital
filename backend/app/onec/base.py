"""Интерфейс источника данных и общие типы «плоских» записей."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol


@dataclass
class WarehouseRec:
    code: str
    name: str
    kind: str = "warehouse"
    capacity: float = 0.0
    unit: str = "т"


@dataclass
class CargoRec:
    warehouse_code: str
    cargo_group: str
    qty: float = 0.0
    weight_t: float = 0.0
    overdue_weight_t: float = 0.0
    avg_age_days: float = 0.0


@dataclass
class CapacityRec:
    warehouse_code: str
    used: float = 0.0
    capacity: float = 0.0
    unit: str = "т"


@dataclass
class DynamicsRec:
    warehouse_code: str
    bucket_date: date
    inbound_t: float = 0.0
    outbound_t: float = 0.0
    processed_t: float = 0.0
    docs_count: int = 0
    avg_dwell_hours: float = 0.0


@dataclass
class Snapshot:
    snapshot_at: datetime
    warehouses: list[WarehouseRec] = field(default_factory=list)
    cargo: list[CargoRec] = field(default_factory=list)
    capacity: list[CapacityRec] = field(default_factory=list)
    dynamics: list[DynamicsRec] = field(default_factory=list)


class StatsSource(Protocol):
    name: str

    def fetch(self, days: int = 30) -> Snapshot:
        """Отдать срез остатков/ёмкости и динамику за последние `days` дней."""
        ...
