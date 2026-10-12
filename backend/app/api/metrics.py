"""Эндпоинты статистики. Всё режется по складам, доступным пользователю."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import User, Warehouse
from ..schemas import CapacityOut, CargoBalanceOut, DynamicsOut, SummaryOut, WarehouseOut
from ..services import metrics as q

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get("/warehouses", response_model=list[WarehouseOut])
def warehouses(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = q.allowed_warehouse_ids(db, user)
    if not ids:
        return []
    return list(db.scalars(select(Warehouse).where(Warehouse.id.in_(ids)).order_by(Warehouse.name)))


@router.get("/metrics/summary", response_model=SummaryOut)
def summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return q.summary(db, q.allowed_warehouse_ids(db, user))


@router.get("/metrics/cargo-balance", response_model=CargoBalanceOut)
def cargo_balance(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return q.cargo_balance(db, q.allowed_warehouse_ids(db, user))


@router.get("/metrics/warehouse-capacity", response_model=CapacityOut)
def capacity(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return q.warehouse_capacity(db, q.allowed_warehouse_ids(db, user))


@router.get("/metrics/processing-dynamics", response_model=DynamicsOut)
def dynamics(days: int = Query(14, ge=1, le=180),
             user: User = Depends(current_user), db: Session = Depends(get_db)):
    return q.processing_dynamics(db, q.allowed_warehouse_ids(db, user), days)
