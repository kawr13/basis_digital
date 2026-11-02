"""Эндпоинты статистики. Всё режется по складам, доступным пользователю.

Раздел кабинета: это витрина дашборда «Склады 1С», поэтому закрыта она тем же
предикатом доступности раздела, что и вкладка (см. `services/sections.py`).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_section
from ..models import User, Warehouse
from ..schemas import CapacityOut, CargoBalanceOut, DynamicsOut, SummaryOut, WarehouseOut
from ..services import app_settings
from ..services import metrics as q

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get("/warehouses", response_model=list[WarehouseOut])
def warehouses(user: User = Depends(require_section("warehouses")),
               db: Session = Depends(get_db)):
    ids = q.allowed_warehouse_ids(db, user)
    if not ids:
        return []
    return list(db.scalars(select(Warehouse).where(Warehouse.id.in_(ids)).order_by(Warehouse.name)))


@router.get("/metrics/summary", response_model=SummaryOut)
def summary(user: User = Depends(require_section("warehouses")),
            db: Session = Depends(get_db)):
    return q.summary(db, q.allowed_warehouse_ids(db, user))


@router.get("/metrics/cargo-balance", response_model=CargoBalanceOut)
def cargo_balance(user: User = Depends(require_section("warehouses")),
                  db: Session = Depends(get_db)):
    return q.cargo_balance(db, q.allowed_warehouse_ids(db, user))


@router.get("/metrics/warehouse-capacity", response_model=CapacityOut)
def capacity(user: User = Depends(require_section("warehouses")),
             db: Session = Depends(get_db)):
    return q.warehouse_capacity(db, q.allowed_warehouse_ids(db, user))


@router.get("/metrics/processing-dynamics", response_model=DynamicsOut)
def dynamics(days: int | None = Query(default=None, ge=1, le=180),
             user: User = Depends(require_section("warehouses")),
             db: Session = Depends(get_db)):
    """Динамика обработки. Если сутки не указаны — берём настройку ui.dynamics_days."""
    if days is None:
        days = int(app_settings.get(db, "ui.dynamics_days"))
    return q.processing_dynamics(db, q.allowed_warehouse_ids(db, user), days)
