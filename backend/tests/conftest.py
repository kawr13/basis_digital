"""Общая настройка тестов: отдельная БД на прогон, тестовый админ, мок-источник 1С.

Важно: переменные окружения ставятся ДО импорта приложения — engine создаётся
на импорте app.db.
"""
from __future__ import annotations

import os
import tempfile

import pytest

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
)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

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
