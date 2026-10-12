"""Персональные настройки пользователя кабинета.

Раскладка дашборда (порядок плашек, их ширина, что скрыто) и сохранённые фильтры
отчётов живут в БД, а не в localStorage — тогда настройка едет за пользователем
между браузером, рабочим компьютером и телефоном.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import User, UserPreference
from ..schemas import PreferenceIn, PreferenceOut

router = APIRouter(prefix="/api/settings", tags=["settings"])

KNOWN_KEYS = {"dashboard_layout", "report_filters"}
MAX_VALUE_LEN = 20_000


@router.get("/preferences", response_model=dict[str, object])
def get_preferences(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out: dict[str, object] = {}
    for pref in db.scalars(select(UserPreference).where(UserPreference.user_id == user.id)):
        try:
            out[pref.key] = json.loads(pref.value)
        except json.JSONDecodeError:
            out[pref.key] = None
    return out


@router.get("/preferences/{key}", response_model=PreferenceOut)
def get_preference(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    pref = db.get(UserPreference, (user.id, key))
    return PreferenceOut(key=key, value=json.loads(pref.value) if pref else None)


@router.put("/preferences/{key}", response_model=PreferenceOut)
def put_preference(key: str, payload: PreferenceIn, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    if key not in KNOWN_KEYS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Неизвестная настройка. Разрешены: {', '.join(sorted(KNOWN_KEYS))}")

    raw = json.dumps(payload.value, ensure_ascii=False)
    if len(raw) > MAX_VALUE_LEN:
        raise HTTPException(413, "Слишком большая настройка")

    pref = db.get(UserPreference, (user.id, key))
    if pref:
        pref.value = raw
    else:
        db.add(UserPreference(user_id=user.id, key=key, value=raw))
    db.commit()
    return PreferenceOut(key=key, value=payload.value)


@router.delete("/preferences/{key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preference(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    pref = db.get(UserPreference, (user.id, key))
    if pref:
        db.delete(pref)
        db.commit()
    return None
