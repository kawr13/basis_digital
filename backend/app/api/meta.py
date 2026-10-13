"""Метаданные кабинета: насколько свежи витринные данные.

Фронт опрашивает `/api/meta/freshness` каждые 15 секунд: это дешевле, чем
перезапрашивать все метрики, и позволяет честно писать «данные 1С на …» и
«обновлено N мин назад», а также подсвечивать устаревшие цифры.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import FactCargoBalance, FactWarehouseCapacity, SyncRun, User, utcnow
from ..services import app_settings

router = APIRouter(prefix="/api/meta", tags=["meta"])

# Данные считаются устаревшими, если успешного обмена с 1С не было дольше
# N интервалов синхронизации подряд. 2.5 — чтобы не шуметь из-за одного
# пропущенного тика (перезапуск сервиса, окно техобслуживания 1С).
STALE_FACTOR = 2.5


def _latest_snapshot(db: Session):
    """Самая свежая метка среза по всем витринным таблицам."""
    stamps = [
        db.scalar(select(func.max(FactCargoBalance.snapshot_at))),
        db.scalar(select(func.max(FactWarehouseCapacity.snapshot_at))),
    ]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


@router.get("/freshness")
def freshness(db: Session = Depends(get_db), _: User = Depends(current_user)) -> dict:
    """Как свежи данные и когда был последний обмен с 1С (любая авторизованная роль)."""
    data_as_of = _latest_snapshot(db)
    interval = int(app_settings.get(db, "sync.interval_minutes"))
    last = db.scalar(
        select(SyncRun)
        .where(SyncRun.status == "ok", SyncRun.finished_at.is_not(None))
        .order_by(SyncRun.finished_at.desc())
        .limit(1)
    )
    now = utcnow()

    # Успешных синков вообще не было — данные точно не свежие.
    stale = last is None
    if last is not None and last.finished_at is not None:
        stale = now - last.finished_at > timedelta(minutes=interval * STALE_FACTOR)

    return {
        "data_as_of": data_as_of.isoformat() if data_as_of else None,
        "last_sync": None
        if last is None
        else {
            "id": last.id,
            "started_at": last.started_at.isoformat() if last.started_at else None,
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
            "status": last.status,
            "source": last.source,
            "rows": last.rows,
        },
        "stale": bool(stale),
        "sync_interval_minutes": interval,
        "server_time": now.isoformat(),
    }
