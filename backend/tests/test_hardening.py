"""П. 1–5 TASKS2.md: прод не стартует с небезопасной конфигурацией, сброс пароля
отзывает сессии, проба не раздаёт разведданные.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import Request
from pydantic import ValidationError

from app import deps
from app.config import DEFAULT_SECRET_KEY, Settings

GOOD_KEY = "x" * 40
OLD_PASS = "Staryj-Pass-2026"
NEW_PASS = "Novyj-Pass-2026"
MANAGER_LOGIN = "test.manager"


def _prod(**kw):
    """Настройки «как в проде», независимо от .env и переменных прогона.

    Ключи — имена полей (secret_key), а не переменных окружения (SECRET_KEY):
    при extra="ignore" верхнерегистровый kwarg молча выбрасывался, и проверка
    «прод не стартует» проверяла только то, что значения из окружения прочитаны.
    """
    base = dict(_env_file=None, environment="prod", secret_key=GOOD_KEY,
                admin_password="", cors_origins="https://cab.example.ru")
    base.update(kw)
    return Settings(**base)


# --- конфигурация -------------------------------------------------------------

def test_prod_bez_deynogo_klyucha_ne_zavoditsya():
    for bad in ("", "   ", DEFAULT_SECRET_KEY, "korotkij"):
        with pytest.raises(ValidationError):
            _prod(secret_key=bad)


def test_prod_s_deynym_klyuchom_zavoditsya():
    assert _prod().secret_key == GOOD_KEY


def test_opечатka_environment_vypadaet():
    """ENVIRONMENT=production раньше молча означало dev-режим."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production")


def test_dev_dozvolen_s_devonysh_klyuchom():
    assert Settings(_env_file=None, environment="dev",
                    secret_key=DEFAULT_SECRET_KEY).environment == "dev"


def test_zvjozdochka_v_cors_zapreshchena_v_prode():
    with pytest.raises(ValidationError):
        _prod(cors_origins="*")


def test_demo_vitrina_po_umolchaniyu_vyklyuchena():
    """Раньше дефолт был True: прод с позабытым SEED_DEMO_DATA получал mock-тонны."""
    assert Settings.model_fields["seed_demo_data"].default is False


# --- отзыв сессий -------------------------------------------------------------

@pytest.fixture
def manager(client, admin_hdr) -> int:
    """Локальный менеджер: проверки не должны зависеть от демо-посева."""
    users = client.get("/api/admin/users", headers=admin_hdr).json()
    uid = next((u["id"] for u in users if u["login"] == MANAGER_LOGIN), None)
    if uid is None:
        r = client.post("/api/admin/users", headers=admin_hdr,
                        json={"login": MANAGER_LOGIN, "full_name": "Тест Менеджер",
                              "role": "manager", "password": OLD_PASS, "warehouse_ids": []})
        assert r.status_code == 201, r.text
        return r.json()["id"]
    # прошлый тест мог сменить пароль — возвращаем известное состояние
    r = client.patch(f"/api/admin/users/{uid}", headers=admin_hdr, json={"password": OLD_PASS})
    assert r.status_code == 200, r.text
    return uid


def _refresh_token(client, login: str, password: str) -> str:
    """Refresh живёт в cookie path=/api/auth, поэтому читаем заголовок Set-Cookie."""
    r = client.post("/api/auth/login", json={"login": login, "password": password})
    assert r.status_code == 200, r.text
    for header, value in r.headers.items():
        if header.lower() == "set-cookie" and "sc_refresh=" in value:
            return value.split("sc_refresh=", 1)[1].split(";", 1)[0]
    raise AssertionError("сервер не вернул refresh-токен")


def test_smena_parolya_adminom_otzyvaet_refresh(client, admin_hdr, manager):
    raw = _refresh_token(client, MANAGER_LOGIN, OLD_PASS)

    r = client.patch(f"/api/admin/users/{manager}", headers=admin_hdr, json={"password": NEW_PASS})
    assert r.status_code == 200, r.text

    assert client.post(f"/api/auth/refresh?refresh_token={raw}").status_code == 401
    assert client.post("/api/auth/login",
                       json={"login": MANAGER_LOGIN, "password": NEW_PASS}).status_code == 200


def test_reset_password_otzyvaet_refresh(client, admin_hdr, manager):
    raw = _refresh_token(client, MANAGER_LOGIN, OLD_PASS)

    r = client.post(f"/api/admin/reset-password?user_id={manager}", headers=admin_hdr)
    assert r.status_code == 200, r.text

    assert client.post(f"/api/auth/refresh?refresh_token={raw}").status_code == 401


# --- проба -------------------------------------------------------------------

def test_healthz_ne_razdaet_konfiguraciyu(client):
    assert client.get("/healthz").json() == {"status": "ok"}


# --- IP клиента (п. 19–21 TASKS2.md) ----------------------------------------

def _request(xff: str | None = None, peer: str = "10.0.0.9") -> Request:
    headers = [] if xff is None else [(b"x-forwarded-for", xff.encode())]
    return Request({"type": "http", "method": "GET", "path": "/api/x", "headers": headers,
                    "client": (peer, 51234), "scheme": "http",
                    "server": ("statcab", 80), "query_string": b""})


def _trust(monkeypatch, *, trust: bool, hops: int = 1) -> None:
    monkeypatch.setattr(deps, "get_settings", lambda: SimpleNamespace(
        trust_proxy_headers=trust, trust_proxy_hops=hops))


def test_zagolovok_chuzhogo_ip_bez_doveriya_ignoriruetsya(monkeypatch):
    """Заголовок клиент придумывает сам: пока прокси не доверен, берём реальный пир."""
    _trust(monkeypatch, trust=False)
    assert deps.client_ip(_request("203.0.113.5")) == "10.0.0.9"


def test_doverennyj_proksi_zadaet_ip_iz_zagolovka(monkeypatch):
    """Один доверенный hop — это адрес, который nginx увидел перед собой."""
    _trust(monkeypatch, trust=True)
    assert deps.client_ip(_request("203.0.113.5")) == "203.0.113.5"


def test_leveye_zapisi_za_svoimi_hopami_ne_reshayut(monkeypatch):
    """Слева клиент вправе дописать что угодно: верим только записи своих hop."""
    _trust(monkeypatch, trust=True)
    assert deps.client_ip(_request("198.51.100.7, 203.0.113.5")) == "203.0.113.5"


def test_svoj_hops_otkudyayutsya_sprava(monkeypatch):
    """nginx поверх балансировщика: два своих адреса справа, клиент — перед ними."""
    _trust(monkeypatch, trust=True, hops=2)
    assert deps.client_ip(_request("203.0.113.5, 10.1.1.1")) == "203.0.113.5"


def test_bez_zagolovka_ip_pira(monkeypatch):
    _trust(monkeypatch, trust=True)
    assert deps.client_ip(_request()) == "10.0.0.9"


def test_doverie_zagolovku_po_umolchaniyu_vyklyucheno():
    """Безопасное значение по умолчанию: включается только явным TRUST_PROXY_HEADERS."""
    assert Settings.model_fields["trust_proxy_headers"].default is False
    assert Settings.model_fields["trust_proxy_hops"].default == 1
