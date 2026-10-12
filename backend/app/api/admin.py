"""Администрирование пользователей кабинета (роль admin)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_roles
from ..models import (
    AuditLog, RefreshToken, Role, User, UserWarehouseScope, Warehouse, utcnow,
)
from ..schemas import AuditOut, UserCreate, UserOut, UserUpdate
from ..security import hash_password, password_is_strong, random_password

router = APIRouter(prefix="/api/admin", tags=["admin"])

admin_only = require_roles(Role.ADMIN)


def _out(u: User) -> UserOut:
    return UserOut(
        id=u.id, login=u.login, full_name=u.full_name, role=u.role.value, email=u.email,
        all_warehouses=u.all_warehouses, warehouse_ids=u.warehouse_ids, last_login_at=u.last_login_at,
    )


@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(admin_only), db: Session = Depends(get_db)):
    return [_out(u) for u in db.scalars(select(User).order_by(User.login))]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db), _: User = Depends(admin_only)):
    login = payload.login.strip().lower()
    if db.scalar(select(User).where(User.login == login)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Логин уже занят")
    ok, msg = password_is_strong(payload.password)
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, msg)

    user = User(
        login=login, full_name=payload.full_name.strip(), email=payload.email,
        role=Role(payload.role), password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.flush()
    _set_scopes(db, user, payload.warehouse_ids)
    db.add(AuditLog(user_id=user.id, login=login, action="admin.user_create", detail=f"role={user.role.value}"))
    db.commit()
    db.refresh(user)
    return _out(user)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db),
                actor: User = Depends(admin_only)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")

    if payload.full_name:
        user.full_name = payload.full_name.strip()
    if payload.role:
        if user.id == actor.id and payload.role != Role.ADMIN.value:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нельзя снять роль со себя")
        user.role = Role(payload.role)
    if payload.is_active is not None:
        user.is_active = payload.is_active
        if payload.is_active:
            user.locked_until = None
            user.failed_attempts = 0
        else:
            _revoke_tokens(db, user)
    if payload.password:
        ok, msg = password_is_strong(payload.password)
        if not ok:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, msg)
        user.password_hash = hash_password(payload.password)
        user.password_changed_at = utcnow()
        user.failed_attempts = 0
        user.locked_until = None
    if payload.warehouse_ids is not None:
        _set_scopes(db, user, payload.warehouse_ids)

    db.add(AuditLog(user_id=actor.id, login=actor.login, action="admin.user_update",
                    detail=f"target={user.login}"))
    db.commit()
    db.refresh(user)
    return _out(user)


@router.post("/reset-password")
def reset_password(user_id: int, db: Session = Depends(get_db), actor: User = Depends(admin_only)):
    """Временный пароль: показать один раз, пользователю придётся сменить его при входе."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    pwd = random_password()
    user.password_hash = hash_password(pwd)
    user.password_changed_at = utcnow()
    user.failed_attempts = 0
    user.locked_until = None
    db.add(AuditLog(user_id=actor.id, login=actor.login, action="admin.reset_password",
                    detail=f"target={user.login}"))
    db.commit()
    return {"login": user.login, "temporary_password": pwd}


@router.post("/users/{user_id}/unlock", response_model=UserOut)
def unlock_user(user_id: int, db: Session = Depends(get_db), actor: User = Depends(admin_only)):
    """Снять блокировку: менеджер набрал неверных паролей, админ проверяет личность."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    user.locked_until = None
    user.failed_attempts = 0
    db.add(AuditLog(user_id=actor.id, login=actor.login, action="admin.user_unlock",
                    detail=f"target={user.login}"))
    db.commit()
    db.refresh(user)
    return _out(user)


@router.delete("/users/{user_id}", response_model=UserOut)
def deactivate_user(user_id: int, db: Session = Depends(get_db),
                    actor: User = Depends(admin_only)):
    """Мягкое удаление: учётка выключается, все refresh-токены отзываются.

    Жёсткого удаления нет намеренно: аудит и история загрузок должны переживать
    увольнение сотрудника. Себя деактивировать нельзя — иначе одним неверным
    кликом можно закрыть себе доступ в админку.
    """
    if user_id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нельзя деактивировать собственную учётку")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")

    user.is_active = False
    user.locked_until = None
    user.failed_attempts = 0
    _revoke_tokens(db, user)
    db.add(AuditLog(user_id=actor.id, login=actor.login, action="admin.user_deactivate",
                    detail=f"target={user.login}"))
    db.commit()
    db.refresh(user)
    return _out(user)


@router.get("/audit", response_model=list[AuditOut])
def audit_log(limit: int = Query(default=100, ge=1, le=1000),
              _: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Последние записи журнала: кто входил, что менял в настройках и складах."""
    return list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                           .limit(limit)))


def _set_scopes(db: Session, user: User, warehouse_ids: list[int]) -> None:
    user.scopes = []
    db.flush()
    for wid in dict.fromkeys(warehouse_ids):
        if db.get(Warehouse, wid):
            db.add(UserWarehouseScope(user_id=user.id, warehouse_id=wid))
    db.flush()


def _revoke_tokens(db: Session, user: User) -> None:
    """Отозвать живые refresh-токены: сессии пользователя умирают сразу."""
    for rec in db.scalars(select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))):
        rec.revoked_at = utcnow()
