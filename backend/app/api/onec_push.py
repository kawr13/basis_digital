"""Push-режим: 1С сама присылает срез в кабинет.

Нужен, когда между сетями нельзя ходить «из кабинета в 1С» (файрвол, другой
контур) или когда 1С умеет только отправлять. Тело запроса — ровно тот же
контракт, что отдаёт HTTP-сервис (см. docs/03_контракт_1с.md), поэтому разбор
и сохранение общие с pull-режимом: `parse.snapshot_from_payload` + `apply_snapshot`.

Авторизация — не пользовательский JWT, а статический токен в заголовке
`X-OneC-Token` (настройка `onec.push_token` / env `ONEC_PUSH_TOKEN`).
Токен не задан → эндпоинт выключен (503), чтобы случайно не открыть запись.
"""
from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AuditLog, SyncRun, utcnow
from ..onec.parse import snapshot_from_payload
from ..onec.sync import apply_snapshot
from ..schemas import PushOut
from ..services import app_settings

log = logging.getLogger("statcab.onec.push")

router = APIRouter(prefix="/api/onec", tags=["onec"])

TOKEN_HEADER = "X-OneC-Token"


def _check_token(db: Session, request: Request) -> None:
    """503 — токен не настроен; 401 — не совпал. Сравнение постоянное по времени."""
    secret = str(app_settings.get(db, "onec.push_token") or "")
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Push из 1С не настроен: задайте ONEC_PUSH_TOKEN или "
                            "настройку onec.push_token")
    given = request.headers.get(TOKEN_HEADER, "")
    if not hmac.compare_digest(given, secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный токен X-OneC-Token")


@router.post("/push", response_model=PushOut)
def push_snapshot(payload: dict[str, Any], request: Request, db: Session = Depends(get_db)):
    """Принять срез от 1С. Идемпотентно: повторный тот же срез не множит строки."""
    _check_token(db, request)

    run = SyncRun(source="push", status="running")
    db.add(run)
    db.commit()

    try:
        snap = snapshot_from_payload(payload)
        counts = apply_snapshot(db, snap)
        rows = sum(counts.values())
        run.status, run.rows, run.finished_at = "ok", rows, utcnow()
        db.commit()
        log.info("Push из 1С: строк %d (%s)", rows, counts)
        db.add(AuditLog(login="1c-push", action="onec.push", ip=request.client.host if request.client else None,
                        detail=f"строк: {rows}, срез: {snap.snapshot_at.isoformat(sep=' ')}"))
        db.commit()
        return PushOut(status="ok", source="push", run_id=run.id,
                       snapshot_at=snap.snapshot_at, rows=rows, counts=counts)
    except Exception as exc:  # noqa: BLE001 — чужие данные: пишем в журнал и отдаём 400
        db.rollback()
        run.status, run.error, run.finished_at = "error", f"{type(exc).__name__}: {exc}"[:1000], utcnow()
        db.add(run)
        db.commit()
        db.add(AuditLog(login="1c-push", action="onec.push.error",
                        ip=request.client.host if request.client else None,
                        detail=(run.error or "")[:500]))
        db.commit()
        log.warning("Push из 1С отклонён: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Срез не принят: {exc}") from exc
