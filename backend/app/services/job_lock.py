"""Межпроцессная аренда работы (п. 8 TASKS2.md).

`threading.Lock` в `try_sync` бережёт витрину от наложения внутри одного процесса.
Второй воркер uvicorn — это уже другой процесс: у него свой планировщик и свой
замок, поэтому «одна загрузка на кабинет» обязан обеспечивать общий мьютекс.
Таблица `job_lock` подходит SQLite и PostgreSQL одинаково, в отличие от
pg_advisory_lock, которого на SQLite просто нет.
"""
from __future__ import annotations

import logging
import os
import socket
import uuid
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import JobLock, utcnow

log = logging.getLogger("statcab.joblock")

# Все, кто пишет витрину: pull-синхронизация, push из 1С и импорт отчёта
# диспетчера. Таблицы у них пересекаются частично, а кэш ответов инвалидируется
# один на всех — значит писать витрина должна по очереди.
VITRINA = "vitrina_write"

# Аренда без владельца: процесс умер, не успел отпустить (kill -9, рестарт
# контейнера, `compose up --force-recreate`). После этого срока строку имеет право
# перехватить следующий. 30 минут — запас примерно в сто раз больше самой долгой
# загрузки месяца порта; без этого срока одна убитая загрузка блокировала бы
# кабинет до следующей перезагрузки.
DEFAULT_TTL_MINUTES = 30

# Кто мы: pid + хост + случайный хвост. Два воркера одного контейнера различаются,
# а release не снимает аренду, отданную соседу.
_OWNER = f"{os.getpid()}@{socket.gethostname()}:{uuid.uuid4().hex[:8]}"


def owner() -> str:
    """Идентичность этого процесса — её видно в админке и в /readyz."""
    return _OWNER


def acquire(db: Session, name: str = VITRINA, *, ttl_minutes: int = DEFAULT_TTL_MINUTES,
            note: str | None = None) -> bool:
    """True — работа наша. False — её делает кто-то другой; очередь не занимаем.

    Внутри commit: бери аренду до того, как накликал в сессии других изменений,
    иначе acquire заодно их и зафиксирует.
    """
    now = utcnow()
    try:
        db.add(JobLock(name=name, owner=_OWNER, acquired_at=now, note=note))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()                       # строка есть: аренду кто-то держит

    holder = db.scalar(select(JobLock.owner).where(JobLock.name == name))
    taken = db.execute(
        update(JobLock)
        .where(JobLock.name == name, JobLock.acquired_at <= now - timedelta(minutes=ttl_minutes))
        .values(owner=_OWNER, acquired_at=now, note=note)
    )
    db.commit()
    if taken.rowcount == 1:
        log.warning("Аренда «%s» перешла к нам: владелец %s не отпустил её за %d мин",
                    name, holder, ttl_minutes)
        return True
    log.info("Работа «%s» уже идёт: владелец %s", name, holder)
    return False


def release(db: Session, name: str = VITRINA) -> None:
    """Снять аренду. Чужую строку не трогаем: свой срок мы могли пережить."""
    db.execute(delete(JobLock).where(JobLock.name == name, JobLock.owner == _OWNER))
    db.commit()


@contextmanager
def held(db: Session, name: str = VITRINA, *, ttl_minutes: int = DEFAULT_TTL_MINUTES,
         note: str | None = None):
    """`with job_lock.held(db) as ok:` — аренда снимется в любом исходе работы."""
    if not acquire(db, name, ttl_minutes=ttl_minutes, note=note):
        yield False
        return
    try:
        yield True
    finally:
        release(db, name)


def info(db: Session, name: str = VITRINA) -> dict | None:
    """Кто и сколько держит аренду (админка, /readyz). None — работа свободна."""
    row = db.scalar(select(JobLock).where(JobLock.name == name))
    if row is None:
        return None
    return {"owner": row.owner, "note": row.note, "name": name,
            "minutes": int((utcnow() - row.acquired_at).total_seconds() // 60)}
