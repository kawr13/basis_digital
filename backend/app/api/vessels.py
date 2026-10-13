"""API документа «Постановка судов».

Права берутся из настроек кабинета (вкладка «Настройки» в админке), а не зашиты
в код: `vessels.view_roles` — кто видит план, `vessels.edit_roles` — кто ставит и
правит суда, `vessels.delete_roles` — кто удаляет. Значение — список ролей через
запятую (admin, top, manager). Так диспетчеру можно дать право ставить суда, не
превращая его в топ-менеджмента. Все правки уходят в журнал аудита.

Период: `from`/`to` — календарные даты; в выборку попадает любое судно, которое
хоть одним слотом задевает период, поэтому «посмотреть любой период» работает и
для постановок, начавшихся раньше.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_roles
from ..services import app_settings
from ..models import AuditLog, Berth, Role, User, VesselPlacement
from ..schemas import (
    BerthOut, BerthUpsert, PlacementIn, PlacementOut, PlacementUpdate,
)
from ..services import vessels as svc

router = APIRouter(prefix="/api/vessels", tags=["vessels"])

admin_only = require_roles(Role.ADMIN)

VIEW_KEY = "vessels.view_roles"
EDIT_KEY = "vessels.edit_roles"
DELETE_KEY = "vessels.delete_roles"

ROLE_LABEL = {"admin": "администратор", "top": "топ-менеджмент", "manager": "менеджер"}


def _allow(db: Session, user: User, key: str, what: str) -> None:
    """Проверяем роль по настройке; текст ошибки говорит, у кого право есть."""
    roles = app_settings.role_set(db, key)
    if user.role.value in roles:
        return
    who = ", ".join(ROLE_LABEL.get(r, r) for r in sorted(roles)) or "никого"
    raise HTTPException(status.HTTP_403_FORBIDDEN, f"{what}: право есть у {who}")


def _audit(db: Session, user: User, action: str, detail: str, request: Request) -> None:
    db.add(AuditLog(user_id=user.id, login=user.login, action=action,
                    ip=request.client.host if request.client else None, detail=detail))


def _placement_out(p: VesselPlacement) -> PlacementOut:
    return PlacementOut.model_validate(p)


# --------------------------------------------------------------- причалы ----


@router.get("/berths", response_model=list[BerthOut])
def list_berths(db: Session = Depends(get_db), user: User = Depends(current_user)):
    _allow(db, user, VIEW_KEY, "Справочник причалов закрыт")
    return svc.berths(db)


@router.post("/berths", response_model=BerthOut, status_code=status.HTTP_201_CREATED)
def create_berth(
    payload: BerthUpsert,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(admin_only),
):
    code = payload.code.strip()
    if db.query(Berth).filter(Berth.code == code).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Причал {code} уже есть")
    berth = Berth(
        code=code, depth=payload.depth, length_m=payload.length_m,
        lanes=payload.lanes, sort_order=payload.sort_order, is_active=True,
    )
    db.add(berth)
    _audit(db, user, "vessels.berth_create", code, request)
    db.commit()
    db.refresh(berth)
    return berth


@router.patch("/berths/{berth_id}", response_model=BerthOut)
def update_berth(
    berth_id: int,
    payload: BerthUpsert,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(admin_only),
):
    berth = db.get(Berth, berth_id)
    if not berth:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Причал не найден")
    code = payload.code.strip()
    other = db.query(Berth).filter(Berth.code == code, Berth.id != berth_id).first()
    if other:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Причал {code} уже есть")
    berth.code, berth.depth, berth.length_m = code, payload.depth, payload.length_m
    berth.lanes, berth.sort_order = payload.lanes, payload.sort_order
    if payload.is_active is not None:
        berth.is_active = payload.is_active
    _audit(db, user, "vessels.berth_update", code, request)
    db.commit()
    db.refresh(berth)
    return berth


# ---------------------------------------------------------- постановки ----


@router.get("/placements")
def list_placements(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    berth_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Сетка плана за период: причалы + попавшие в период постановки."""
    _allow(db, user, VIEW_KEY, "План постановки судов закрыт")
    d_from, d_to = svc.period_of(date_from, date_to)
    rows = svc.placements_in_period(db, date_from=d_from, date_to=d_to, berth_id=berth_id)
    return {
        "period": {"from": d_from, "to": d_to},
        "slot_hours": list(svc.SLOT_HOURS),
        "berths": [BerthOut.model_validate(b) for b in svc.berths(db)],
        "placements": [_placement_out(p) for p in rows],
    }


@router.post("/placements", response_model=PlacementOut, status_code=status.HTTP_201_CREATED)
def create_placement(
    payload: PlacementIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    _allow(db, user, EDIT_KEY, "Поставить судно")
    try:
        svc.validate(
            db, berth_id=payload.berth_id, lane=payload.lane,
            starts_at=payload.starts_at, ends_at=payload.ends_at,
        )
    except svc.Conflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except svc.BadPlan as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    row = VesselPlacement(
        berth_id=payload.berth_id, lane=payload.lane,
        vessel=payload.vessel.strip(), volume=(payload.volume or "").strip() or None,
        cargo=(payload.cargo or "").strip() or None, note=(payload.note or "").strip() or None,
        starts_at=svc.normalize(payload.starts_at), ends_at=svc.normalize(payload.ends_at),
        created_by=user.id, updated_by=user.id,
    )
    db.add(row)
    db.flush()
    _audit(db, user, "vessels.create", svc.place_summary(row), request)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/placements/{placement_id}", response_model=PlacementOut)
def update_placement(
    placement_id: int,
    payload: PlacementUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    _allow(db, user, EDIT_KEY, "Править постановку")
    row = db.get(VesselPlacement, placement_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Постановка не найдена")

    berth_id = payload.berth_id if payload.berth_id is not None else row.berth_id
    lane = payload.lane if payload.lane is not None else row.lane
    starts_at = payload.starts_at or row.starts_at
    ends_at = payload.ends_at or row.ends_at
    try:
        svc.validate(
            db, berth_id=berth_id, lane=lane, starts_at=starts_at, ends_at=ends_at,
            exclude_id=row.id,
        )
    except svc.Conflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except svc.BadPlan as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    row.berth_id, row.lane = berth_id, lane
    row.starts_at, row.ends_at = svc.normalize(starts_at), svc.normalize(ends_at)
    if payload.vessel is not None:
        row.vessel = payload.vessel.strip()
    if payload.volume is not None:
        row.volume = payload.volume.strip() or None
    if payload.cargo is not None:
        row.cargo = payload.cargo.strip() or None
    if payload.note is not None:
        row.note = payload.note.strip() or None
    row.updated_by = user.id
    _audit(db, user, "vessels.update", svc.place_summary(row), request)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/placements/{placement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_placement(
    placement_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    _allow(db, user, DELETE_KEY, "Удалить постановку")
    row = db.get(VesselPlacement, placement_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Постановка не найдена")
    _audit(db, user, "vessels.delete", svc.place_summary(row), request)
    db.delete(row)
    db.commit()
