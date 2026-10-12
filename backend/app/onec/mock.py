"""Демо-источник: правдоподобный срез, пока 1С не подключена.

Детерминированный по дате: перезапуск в тот же день даёт те же цифры,
поэтому дашборд не «прыгает» между перезагрузками сервиса.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

from .base import CapacityRec, CargoRec, DynamicsRec, Snapshot, WarehouseRec

WAREHOUSES = [
    WarehouseRec("WH-01", "Склад №1 (морозильник)", "cold", 3200, "т"),
    WarehouseRec("WH-02", "Склад №2 (охлаждаемый)", "cold", 2100, "т"),
    WarehouseRec("WH-03", "Терминал сборных грузов", "terminal", 1500, "т"),
    WarehouseRec("WH-04", "Площадка контейнерная", "terminal", 420, "TEU"),
    WarehouseRec("WH-05", "Склад сухих грузов", "warehouse", 2600, "т"),
]

CARGO_GROUPS = [
    "Минтай с/м", "Скумбрия с/м", "Краб/морепродукты", "Фары/субпродукты",
    "Сборные грузы", "Тара и паллеты", "Сухие грузы",
]


class MockSource:
    name = "mock"

    def fetch(self, days: int = 30) -> Snapshot:
        today = date.today()
        snap_at = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
        rng = random.Random(today.isoformat())

        cargo: list[CargoRec] = []
        capacity: list[CapacityRec] = []
        dynamics: list[DynamicsRec] = []

        for wh in WAREHOUSES:
            fill = rng.uniform(0.55, 0.97)
            capacity.append(CapacityRec(wh.code, round(wh.capacity * fill, 1), wh.capacity, wh.unit))

            groups = rng.sample(CARGO_GROUPS, k=rng.randint(3, 5))
            left = wh.capacity * fill
            for g in groups:
                share = rng.uniform(0.15, 0.45)
                weight = round(min(left, wh.capacity * share), 1)
                left = max(left - weight, 0)
                cargo.append(CargoRec(
                    warehouse_code=wh.code,
                    cargo_group=g,
                    qty=round(weight / rng.uniform(0.6, 1.4), 1),
                    weight_t=weight,
                    overdue_weight_t=round(weight * rng.uniform(0.0, 0.28), 1),
                    avg_age_days=round(rng.uniform(3, 42), 1),
                ))

            for d in range(days):
                day = today - timedelta(days=d)
                rng_day = random.Random(f"{today.isoformat()}|{wh.code}|{day.isoformat()}")
                weekend = day.weekday() >= 5
                base = wh.capacity * (0.02 if weekend else 0.07)
                processed = round(base * rng_day.uniform(0.5, 1.5), 1)
                dynamics.append(DynamicsRec(
                    warehouse_code=wh.code,
                    bucket_date=day,
                    inbound_t=round(processed * rng_day.uniform(0.7, 1.3), 1),
                    outbound_t=round(processed * rng_day.uniform(0.6, 1.2), 1),
                    processed_t=processed,
                    docs_count=int(processed // rng_day.uniform(4, 9)) + 1,
                    avg_dwell_hours=round(rng_day.uniform(18, 96), 1),
                ))

        return Snapshot(snapshot_at=snap_at, warehouses=list(WAREHOUSES),
                        cargo=cargo, capacity=capacity, dynamics=dynamics)
