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

from datetime import date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_roles
from ..models import AuditLog, Berth, Role, User, VesselPlacement
from ..schemas import (
    BerthOut, BerthUpsert, PlacementIn, PlacementOut, PlacementUpdate,
)
from ..services import plan_pdf, vessels as svc

router = APIRouter(prefix="/api/vessels", tags=["vessels"])

admin_only = require_roles(Role.ADMIN)

RIGHT_LABEL = {"view": "смотреть", "edit": "ставить и править", "delete": "удалять"}


def _allow(db: Session, user: User, right: str, what: str) -> None:
    """Право считается так: личная настройка пользователя → иначе роли из настроек."""
    if svc.rights_for(db, user).get(right):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, f"{what}: вам нельзя {RIGHT_LABEL[right]}")


def _audit(db: Session, user: User, action: str, detail: str, request: Request) -> None:
    db.add(AuditLog(user_id=user.id, login=user.login, action=action,
                    ip=request.client.host if request.client else None, detail=detail))


def _placement_out(p: VesselPlacement) -> PlacementOut:
    return PlacementOut.model_validate(p)


# --------------------------------------------------------------- причалы ----


@router.get("/berths", response_model=list[BerthOut])
def list_berths(db: Session = Depends(get_db), user: User = Depends(current_user)):
    # администратор справочник видит всегда: он его и ведёт, даже если ему
    # лично закрыли доступ к самому плану постановки
    if user.role != Role.ADMIN:
        _allow(db, user, "view", "Справочник причалов закрыт")
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


@router.delete("/berths/{berth_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_berth(
    berth_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(admin_only),
):
    """Удалить причал можно, только если на нём нет ни одной постановки.

    Иначе история плана повиснет на несуществующем причале. Такой причал нужно
    снять с линии (`is_active=false`) — он уйдёт из сетки, а строки останутся.
    """
    berth = db.get(Berth, berth_id)
    if not berth:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Причал не найден")
    used = db.execute(
        select(func.count()).select_from(VesselPlacement).where(VesselPlacement.berth_id == berth_id)
    ).scalar_one()
    if used:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"На причале {berth.code} постановок: {used}. Удалить нельзя — снимите его с линии, "
            "он скроется из плана, а история останется",
        )
    code = berth.code
    db.delete(berth)
    _audit(db, user, "vessels.berth_delete", code, request)
    db.commit()


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
    _allow(db, user, "view", "План постановки судов закрыт")
    d_from, d_to = svc.period_of(date_from, date_to)
    rows = svc.placements_in_period(db, date_from=d_from, date_to=d_to, berth_id=berth_id)
    # Просмотр периода целиком в прошлом — план «как тогда»: состояние на последний
    # день периода. Актуальный взгляд (период захватил сегодня или дальше) — на сейчас.
    as_of = min(datetime.now(), datetime.combine(d_to, time.max))
    svc.apply_reberthing_state(db, rows, now=as_of)
    return {
        "period": {"from": d_from, "to": d_to},
        "slot_hours": list(svc.SLOT_HOURS),
        "berths": [BerthOut.model_validate(b) for b in svc.berths(db, only_active=True)],
        "placements": [_placement_out(p) for p in rows],
    }


@router.get("/plan.pdf")
def plan_pdf_file(
    request: Request,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    day_from: int = Query(default=0, ge=0, description="первые сутки окна просмотра"),
    day_to: int = Query(default=0, ge=0, description="последние сутки окна просмотра"),
    berth_ids: str | None = Query(default=None, description="причалы окна просмотра: 1,2,3"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """PDF видимого куска плана.

    Окно присылает фронтенд — только он знает, что сейчас в кадре сетки. Сервер
    рисует векторно: кириллица остаётся текстом, линии — линиями.
    """
    _allow(db, user, "view", "План постановки судов закрыт")
    d_from, d_to = svc.period_of(date_from, date_to)
    ids: list[int] | None = None
    if berth_ids:
        try:
            ids = [int(x) for x in berth_ids.split(",") if x.strip()]
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                               "berth_ids — числа через запятую") from e
    try:
        pdf = plan_pdf.build_plan_pdf(
            db, date_from=d_from, date_to=d_to,
            day_from=day_from, day_to=day_to, berth_ids=ids,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    name = f"plan_{d_from:%d-%m-%Y}_{d_to:%d-%m-%Y}.pdf"
    _audit(db, user, "vessels.export_pdf", f"{d_from}…{d_to}, сутки {day_from}–{day_to}", request)
    db.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/free-offset")
def free_offset(
    berth_id: int = Query(...),
    # время принимаем строкой: `from __future__ import annotations` превращает
    # аннотации в строки, и pydantic не разбирает datetime в Query
    starts_at: str = Query(...),
    ends_at: str = Query(...),
    length_m: int = Query(..., ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Первая отметка (м), где судно такой длины встаёт без пересечений.

    Нужна, чтобы форма не заставляла диспетчера гадать: он видит «свободно с 60 м»,
    а не получает 409 после отправки. Окончательное слово всё равно за validate().
    """
    _allow(db, user, "view", "План постановки судов закрыт")
    berth = db.get(Berth, berth_id)
    if not berth:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Причал не найден")
    try:
        start, end = svc.normalize(datetime.fromisoformat(starts_at)), svc.normalize(datetime.fromisoformat(ends_at))
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Не понял время: нужен вид 2026-05-10T06:00:00") from None
    offset = svc.first_free_offset(
        db, berth_id=berth_id, starts_at=start, ends_at=end, length_m=length_m
    )
    return {
        "offset_m": offset,
        "length_m": length_m,
        "berth_length_m": svc.berth_length(berth),
        "fits": offset is not None and (not berth.length_m or offset + length_m <= berth.length_m),
    }


@router.post("/placements", response_model=PlacementOut, status_code=status.HTTP_201_CREATED)
def create_placement(
    payload: PlacementIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    _allow(db, user, "edit", "Поставить судно")
    try:
        svc.validate(
            db, berth_id=payload.berth_id, starts_at=payload.starts_at, ends_at=payload.ends_at,
            offset_m=payload.offset_m, length_m=payload.length_m,
        )
    except svc.Conflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except svc.BadPlan as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    if payload.moved_from_id is not None and not db.get(VesselPlacement, payload.moved_from_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нет исходной постановки для перешвартовки")

    row = VesselPlacement(
        berth_id=payload.berth_id, lane=payload.lane,
        length_m=payload.length_m, offset_m=payload.offset_m,
        vessel=payload.vessel.strip(), volume=(payload.volume or "").strip() or None,
        cargo=(payload.cargo or "").strip() or None, note=(payload.note or "").strip() or None,
        starts_at=svc.normalize(payload.starts_at), ends_at=svc.normalize(payload.ends_at),
        moved_from_id=payload.moved_from_id, at_other_terminal=payload.at_other_terminal,
        color=payload.color.lower() if payload.color else None,
        created_by=user.id, updated_by=user.id,
    )
    db.add(row)
    db.flush()
    _audit(db, user, "vessels.create", svc.place_summary(row), request)
    db.commit()
    db.refresh(row)
    svc.apply_reberthing_state(db, [row])  # тот же «действительный» вид, что и в списке
    return row


@router.patch("/placements/{placement_id}", response_model=PlacementOut)
def update_placement(
    placement_id: int,
    payload: PlacementUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    _allow(db, user, "edit", "Править постановку")
    row = db.get(VesselPlacement, placement_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Постановка не найдена")

    berth_id = payload.berth_id if payload.berth_id is not None else row.berth_id
    lane = payload.lane if payload.lane is not None else row.lane
    length_m = payload.length_m if "length_m" in payload.model_fields_set else row.length_m
    offset_m = payload.offset_m if payload.offset_m is not None else row.offset_m
    starts_at = payload.starts_at or row.starts_at
    ends_at = payload.ends_at or row.ends_at
    try:
        svc.validate(
            db, berth_id=berth_id, starts_at=starts_at, ends_at=ends_at,
            offset_m=offset_m, length_m=length_m, exclude_id=row.id,
        )
    except svc.Conflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except svc.BadPlan as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    row.berth_id, row.lane = berth_id, lane
    row.length_m, row.offset_m = length_m, offset_m
    row.starts_at, row.ends_at = svc.normalize(starts_at), svc.normalize(ends_at)
    if payload.vessel is not None:
        row.vessel = payload.vessel.strip()
    if payload.volume is not None:
        row.volume = payload.volume.strip() or None
    if payload.cargo is not None:
        row.cargo = payload.cargo.strip() or None
    if payload.note is not None:
        row.note = payload.note.strip() or None
    if payload.at_other_terminal is not None:
        row.at_other_terminal = payload.at_other_terminal
    if "color" in payload.model_fields_set:
        row.color = payload.color.lower() if payload.color else None
    row.updated_by = user.id
    _audit(db, user, "vessels.update", svc.place_summary(row), request)
    db.commit()
    db.refresh(row)
    svc.apply_reberthing_state(db, [row])
    return row


@router.delete("/placements/{placement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_placement(
    placement_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    _allow(db, user, "delete", "Удалить постановку")
    row = db.get(VesselPlacement, placement_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Постановка не найдена")
    _audit(db, user, "vessels.delete", svc.place_summary(row), request)
    db.delete(row)
    db.commit()
