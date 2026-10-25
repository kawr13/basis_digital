"""Администрирование пользователей кабинета (роль admin)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_roles
from ..models import (
    AuditLog, RefreshToken, Role, User, UserPreference, UserWarehouseScope, Warehouse,
    VesselPlacement, utcnow,
)
from ..schemas import AuditOut, UserCreate, UserOut, UserUpdate
from ..security import hash_password, password_is_strong, random_password
from ..services.vessels import VESSEL_RIGHTS, rights_for

router = APIRouter(prefix="/api/admin", tags=["admin"])

admin_only = require_roles(Role.ADMIN)


def _out(u: User, db: Session) -> UserOut:
    return UserOut(
        id=u.id, login=u.login, full_name=u.full_name, role=u.role.value, email=u.email,
        all_warehouses=u.all_warehouses, warehouse_ids=u.warehouse_ids, last_login_at=u.last_login_at,
        vessel_right=u.vessel_right, vessel_rights=rights_for(db, u),
    )


@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(admin_only), db: Session = Depends(get_db)):
    return [_out(u, db) for u in db.scalars(select(User).order_by(User.login))]


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
    _set_vessel_right(user, payload.vessel_right)
    db.add(user)
    db.flush()
    _set_scopes(db, user, payload.warehouse_ids)
    db.add(AuditLog(user_id=user.id, login=login, action="admin.user_create", detail=f"role={user.role.value}"))
    db.commit()
    db.refresh(user)
    return _out(user, db)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db),
                actor: User = Depends(admin_only)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")

    if payload.full_name:
        user.full_name = payload.full_name.strip()
    if "vessel_right" in payload.model_fields_set:
        before = user.vessel_right
        _set_vessel_right(user, payload.vessel_right)
        if before != user.vessel_right:
            db.add(AuditLog(user_id=actor.id, login=actor.login, action="admin.vessel_right",
                            detail=f"{user.login}: {before or 'по роли'} → {user.vessel_right or 'по роли'}"))
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
    return _out(user, db)


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
    return _out(user, db)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db),
                actor: User = Depends(admin_only)):
    """Удалить учётку целиком. Выключить — это PATCH c is_active=false.

    Раньше DELETE только выключал учётку, и оплошность администратора (лишний
    человек, опечатка в логине) оставалась в списке навсегда: «отключён» виден в
    той же таблице. Увольнение — как раз случай, когда учётку надо выключить,
    и кнопка «Отключить» для этого осталась на PATCH.

    Удалять безопасно, потому что история не висит на учётке: в audit_log автор
    записан ещё и текстом (login), а постановки хранят created_by/updated_by
    числом без внешней ссылки. Строки плана и журнала переживают автора —
    просто остаются без подписи, и об этом говорит плашка в UI.
    """
    if user_id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нельзя удалить собственную учётку")
    # Себя удалить нельзя, а current_user не пускает в админку отключённого —
    # значит хотя бы один активный администратор остаётся всегда, и отдельная
    # проверка «последний админ» здесь не нужна.
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")

    # Внешние ключи SQLite по умолчанию выключены, поэтому ON DELETE CASCADE сам
    # ничего не чистит: удаляем явно, иначе останутся висячие строки и на SQLite,
    # и на PostgreSQL поведение разойдётся.
    sessions = db.query(RefreshToken).filter(RefreshToken.user_id == user_id).delete(
        synchronize_session=False)
    scopes = db.query(UserWarehouseScope).filter(UserWarehouseScope.user_id == user_id).delete(
        synchronize_session=False)
    db.query(UserPreference).filter(UserPreference.user_id == user_id).delete(
        synchronize_session=False)
    # журнал не вычищаем: отвязываем учётку, логин в записи уже лежит текстом
    orphan_audit = db.query(AuditLog).filter(AuditLog.user_id == user_id).update(
        {AuditLog.user_id: None}, synchronize_session=False)
    orphan_plan = db.query(VesselPlacement).filter(VesselPlacement.created_by == user_id).update(
        {VesselPlacement.created_by: None}, synchronize_session=False)
    orphan_plan += db.query(VesselPlacement).filter(VesselPlacement.updated_by == user_id).update(
        {VesselPlacement.updated_by: None}, synchronize_session=False)
    login = user.login
    db.delete(user)
    db.add(AuditLog(user_id=actor.id, login=actor.login, action="admin.user_delete",
                    detail=f"target={login} сессий={sessions} доступов={scopes} "
                           f"журнал без автора={orphan_audit} план без автора={orphan_plan}"))
    db.commit()
    return None


@router.get("/audit", response_model=list[AuditOut])
def audit_log(limit: int = Query(default=100, ge=1, le=1000),
              _: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Последние записи журнала: кто входил, что менял в настройках и складах."""
    return list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                           .limit(limit)))


def _set_vessel_right(user: User, raw: str | None) -> None:
    """Личное право на постановку судов. Пусто — «по роли», как до настройки."""
    value = (raw or "").strip().lower()
    if not value:
        user.vessel_right = None
        return
    if value not in VESSEL_RIGHTS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Право на постановку судов: допустимо " + ", ".join(VESSEL_RIGHTS) + " или пусто",
        )
    user.vessel_right = value


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
