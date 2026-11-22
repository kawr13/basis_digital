"""Ретention служебных записей (п. 14 TASKS2.md).

Витрина — данные порта, их не удаляют; «Постановка судов» — ручной документ,
его тоже не трогаем. А журнал входов и действий, refresh-сессии и журнал
загрузок из 1С растут каждый день, и старше нескольких месяцев их не читает
никто. Без чистки audit_log через год — самая большая таблица базы, а дамп
бэкапа раздут в разы именно им.

Файловых снимков кабинет не ведёт: отчёт диспетчера читают из исходного .xlsm,
поэтому чистить на диске нечего.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AuditLog, RefreshToken, SyncRun, utcnow

log = logging.getLogger("statcab.retention")

# Протухшую сессию держим не «до упора», а с запасом: детектор повторного
# использования refresh-токена смотрит по family_id, и исчезнувшая строка
# превращает «токен засветился» в «ничего не нашли».
DEAD_SESSION_DAYS = 30


def purge(db: Session) -> dict[str, int]:
    """Удалить служебные записи старше срока. Возвращает счётчики для лога и админки."""
    st = get_settings()
    now = utcnow()
    audit_before = now - timedelta(days=st.retention_audit_days)
    runs_before = now - timedelta(days=st.retention_runs_days)
    dead_before = now - timedelta(days=DEAD_SESSION_DAYS)

    # Загружку удаляем только завершённую: строка «running» — это обрыв, а не
    # старость, её закрывает reconcile_hung_runs при старте кабинета.
    counts = {
        "audit": db.execute(delete(AuditLog).where(AuditLog.created_at < audit_before)).rowcount,
        "runs": db.execute(delete(SyncRun).where(
            SyncRun.finished_at.is_not(None), SyncRun.started_at < runs_before)).rowcount,
        "sessions": db.execute(delete(RefreshToken).where(or_(
            RefreshToken.expires_at < dead_before,
            and_(RefreshToken.revoked_at.is_not(None), RefreshToken.revoked_at < dead_before),
        ))).rowcount,
    }
    db.commit()
    if any(counts.values()):
        log.info("Ретention: удалено %s (журнал %d дн., загрузки %d дн., сессии %d дн.)",
                 counts, st.retention_audit_days, st.retention_runs_days, DEAD_SESSION_DAYS)
    return counts


def summary(db: Session) -> dict[str, int]:
    """Сколько служебных записей накопилось — показать админу до первой чистки."""
    return {
        "audit": db.scalar(select(func.count(AuditLog.id))) or 0,
        "runs": db.scalar(select(func.count(SyncRun.id))) or 0,
        "sessions": db.scalar(select(func.count(RefreshToken.id))) or 0,
    }
