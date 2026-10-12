"""Админка: справочник складов.

Склады приходят из 1С, но кабинет живёт и до подключения 1С, а 1С заводит
новый склад раньше, чем начнёт по нему отдавать данные. Поэтому справочник
можно вести руками: создать, переименовать, поправить ёмкость.

Удаления нет: факты (остатки/ёмкость/динамика) висят внешним ключом на складе,
и терять историю нельзя. «Удалить» = деактивировать (`is_active=false`) — склад
перестаёт попадать в метрики и отчёты, строки остаются.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_roles
from ..models import AuditLog, Role, User, Warehouse
from ..schemas import WAREHOUSE_KINDS, WarehouseAdminOut, WarehouseCreate, WarehouseUpdate

router = APIRouter(prefix="/api/admin/warehouses", tags=["admin-warehouses"])

admin_only = require_roles(Role.ADMIN)


def _check_kind(kind: str) -> str:
    kind = (kind or "").strip()
    if kind not in WAREHOUSE_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Вид склада: " + " | ".join(WAREHOUSE_KINDS))
    return kind


def _check_code_free(db: Session, code: str, own_id: int | None = None) -> None:
    """Код склада — идентификатор из 1С, дубликаты недопустимы (регистр не важен)."""
    clash = db.scalar(select(Warehouse).where(func.upper(Warehouse.code) == code.upper(),
                                              Warehouse.id != (own_id or -1)))
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Код склада «{code}» уже используется")


@router.get("", response_model=list[WarehouseAdminOut])
def list_warehouses(_: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Все склады, включая деактивированные — админ должен видеть историю."""
    return list(db.scalars(select(Warehouse).order_by(Warehouse.code)))


@router.post("", response_model=WarehouseAdminOut, status_code=status.HTTP_201_CREATED)
def create_warehouse(payload: WarehouseCreate, db: Session = Depends(get_db),
                     actor: User = Depends(admin_only)):
    code = payload.code.strip()
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Код склада обязателен")
    _check_kind(payload.kind)
    _check_code_free(db, code)

    wh = Warehouse(code=code, name=payload.name.strip(), kind=payload.kind.strip(),
                   capacity=payload.capacity, unit=payload.unit.strip() or "т",
                   is_active=payload.is_active)
    db.add(wh)
    db.flush()
    db.add(AuditLog(user_id=actor.id, login=actor.login, action="admin.warehouse_create",
                    detail=f"code={wh.code}"))
    db.commit()
    db.refresh(wh)
    return wh


@router.patch("/{warehouse_id}", response_model=WarehouseAdminOut)
def update_warehouse(warehouse_id: int, payload: WarehouseUpdate, db: Session = Depends(get_db),
                     actor: User = Depends(admin_only)):
    wh = db.get(Warehouse, warehouse_id)
    if not wh:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Склад не найден")

    changed: list[str] = []
    if payload.code is not None:
        code = payload.code.strip()
        if not code:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Код склада обязателен")
        if code != wh.code:
            _check_code_free(db, code, own_id=wh.id)
            wh.code = code
            changed.append("code")
    if payload.name is not None:
        wh.name = payload.name.strip()
        changed.append("name")
    if payload.kind is not None:
        wh.kind = _check_kind(payload.kind)
        changed.append("kind")
    if payload.capacity is not None:
        wh.capacity = payload.capacity
        changed.append("capacity")
    if payload.unit is not None:
        wh.unit = payload.unit.strip() or wh.unit
        changed.append("unit")
    if payload.is_active is not None:
        wh.is_active = payload.is_active
        changed.append("is_active")

    db.add(AuditLog(user_id=actor.id, login=actor.login, action="admin.warehouse_update",
                    detail=f"code={wh.code} поля: {', '.join(changed) if changed else 'нет'}"))
    db.commit()
    db.refresh(wh)
    return wh


@router.delete("/{warehouse_id}", response_model=WarehouseAdminOut)
def deactivate_warehouse(warehouse_id: int, db: Session = Depends(get_db),
                         actor: User = Depends(admin_only)):
    """Мягкое удаление: склад выключается, факты остаются."""
    wh = db.get(Warehouse, warehouse_id)
    if not wh:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Склад не найден")
    wh.is_active = False
    db.add(AuditLog(user_id=actor.id, login=actor.login, action="admin.warehouse_deactivate",
                    detail=f"code={wh.code}"))
    db.commit()
    db.refresh(wh)
    return wh
