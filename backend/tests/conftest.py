"""Общая настройка тестов: отдельная БД на прогон, тестовый админ, мок-источник 1С.

Важно: переменные окружения ставятся ДО импорта приложения — engine создаётся
на импорте app.db.
"""
from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import select

_tmpdir = tempfile.mkdtemp(prefix="statcab_test_")
os.environ.update(
    DATABASE_URL=f"sqlite:///{_tmpdir}/test.db",
    ENVIRONMENT="dev",
    SECRET_KEY="test-secret-key-0123456789abcdef-ABCDEF0123456789",
    ADMIN_LOGIN="admin",
    ADMIN_PASSWORD="Adm1n-Passw0rd",
    SEED_DEMO_DATA="true",
    ONE_C_SOURCE="mock",
    SYNC_ENABLED="false",
    SYNC_INTERVAL_MINUTES="999",
    # Троттлинг входа — счётчик в памяти на весь прогон (один «IP» у TestClient).
    # Тестам блокировки нужно несколько неудачных попыток, поэтому лимит поднимаем:
    # сам троттлинг отдельной проверкой не тестируется.
    LOGIN_THROTTLE_PER_IP="200",
)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AppSetting  # noqa: E402

ADMIN = {"login": "admin", "password": "Adm1n-Passw0rd"}


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_token(client) -> str:
    r = client.post("/api/auth/login", json=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def admin_hdr(admin_token) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session")
def manager_hdr(client) -> dict:
    r = client.post("/api/auth/login", json={"login": "petrov", "password": "demo1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def settings_snapshot():
    """После теста — точная копия таблицы настроек из до-состояния.

    Настройки кабинета глобальны на процесс, а тесты пишут их через админку:
    без возврата к исходному состоянию следующий тест проверял бы уже чужую
    конфигурацию (адрес 1С, интервал синхронизации, пороги).
    """
    db = SessionLocal()
    try:
        before = {r.key: r.value for r in db.scalars(select(AppSetting))}
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        for rec in list(db.scalars(select(AppSetting))):
            if rec.key in before:
                rec.value = before[rec.key]
            else:
                db.delete(rec)
        db.commit()
    finally:
        db.close()
