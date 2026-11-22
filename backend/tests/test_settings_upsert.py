"""Стор настроек: запись через `INSERT ... ON CONFLICT DO UPDATE` (п. 32 TASKS2.md).

Прежний путь «SELECT, потом INSERT/UPDATE» на каждый ключ делал два круга к БД
и допускал гонку между проверкой и вставкой. Проверяем главное свойство upsert'а:
повторная запись ключа обновляет строку, а не плодит дубликат и не теряет
значение. Ключи берём без `.env`-перекрытия (`sync.window_days`,
`sync.age_limit_sec`), чтобы эффективное значение читалось прямо из таблицы.

Настройки глобальны на процесс, поэтому фикс `clean_keys` возвращает затронутые
строки `app_settings` к прежнему виду после каждого теста.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app import db as app_db
from app.models import AppSetting
from app.services import app_settings as store


def SessionLocal():  # noqa: N802 — сессия ТЕКУЩЕЙ базы (см. test_dispatcher_cache)
    return app_db.SessionLocal()


KEY_A = "sync.window_days"      # int 7..366, env нет
KEY_B = "sync.age_limit_sec"    # int 60..604800, env нет


@pytest.fixture
def clean_keys():
    """После теста в таблице — ровно те же строки затронутых ключей, что были до."""
    keys = (KEY_A, KEY_B)
    db = SessionLocal()
    try:
        before = {r.key: (r.value, r.updated_by) for r in
                  db.scalars(select(AppSetting).where(AppSetting.key.in_(keys)))}
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        for r in db.scalars(select(AppSetting).where(AppSetting.key.in_(keys))):
            db.delete(r)
        for key, (value, updated_by) in before.items():
            db.add(AppSetting(key=key, value=value, updated_by=updated_by))
        db.commit()
    finally:
        db.close()


def _rows(db, key: str) -> list[AppSetting]:
    return list(db.scalars(select(AppSetting).where(AppSetting.key == key)))


def test_apply_twice_updates_single_row(clean_keys):
    """Повторная запись того же ключа: одна строка, новое значение, без дубликата."""
    db = SessionLocal()
    try:
        store.apply(db, {KEY_A: 45})
        assert store.get(db, KEY_A) == 45

        store.apply(db, {KEY_A: 60})
        rows = _rows(db, KEY_A)
        assert len(rows) == 1, "upsert обязан обновлять строку, а не вставлять вторую"
        assert rows[0].value == "60", "значение не должно теряться"
        assert store.get(db, KEY_A) == 60
        assert rows[0].updated_at is not None, "Core-вставка обязана сама ставить updated_at"
    finally:
        db.close()


def test_apply_batch_writes_every_key_once(clean_keys):
    """Пачка из двух ключей — по одной строке на ключ, оба значения читаются."""
    db = SessionLocal()
    try:
        store.apply(db, {KEY_A: 100, KEY_B: 7200})
        assert store.get(db, KEY_A) == 100
        assert store.get(db, KEY_B) == 7200
        assert len(_rows(db, KEY_A)) == 1
        assert len(_rows(db, KEY_B)) == 1
    finally:
        db.close()


def test_apply_after_reset_writes_again(clean_keys):
    """После `reset` ключ пишется заново тем же запросом — без «висячего» дубликата."""
    db = SessionLocal()
    try:
        store.apply(db, {KEY_A: 45})
        store.reset(db, [KEY_A])
        assert store.get(db, KEY_A) == store.DEFAULTS[KEY_A]

        store.apply(db, {KEY_A: 90})
        rows = _rows(db, KEY_A)
        assert len(rows) == 1 and rows[0].value == "90"
    finally:
        db.close()
