"""Личные вкладки пользователя (`users.sections`) — какие разделы ему доступны.

Правило пересечения (см. `app/services/sections.py`): сначала глобальная галка
`ui.hidden_sections`, затем личный список, затем правила роли. Каждый уровень
только сужает доступ, поэтому в тестах дважды проверяем «личное не помогает»:
скрытый для всех раздел не открывает даже личный список, а исключённая из
личного списка «Постановка» закрыта, даже когда `vessel_right` разрешает всё.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog

PASSWORD = "Sections-2026-pass"


def _hdr(client, login: str) -> dict:
    r = client.post("/api/auth/login", json={"login": login, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def make_user(client, admin_hdr):
    """Завести учётку менеджера; после теста удалить, чтобы не мешать другим."""
    logins: list[str] = []

    def factory(login: str, **payload) -> dict:
        body = {"login": login, "full_name": "Проверка Личных Вкладок",
                "password": PASSWORD, "role": "manager", **payload}
        r = client.post("/api/admin/users", json=body, headers=admin_hdr)
        assert r.status_code == 201, r.text
        logins.append(login)
        return r.json()

    yield factory

    db = SessionLocal()
    try:
        from app.models import User
        for login in logins:
            user = db.scalar(select(User).where(User.login == login))
            if user:
                db.delete(user)
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ база ----

def test_without_sections_nothing_changes(client, make_user):
    """sections=None — личный список не наложен, вся коробка ведёт себя как раньше."""
    created = make_user("sec_none")
    assert created["sections"] is None, "по умолчанию личных ограничений нет"
    hdr = _hdr(client, "sec_none")

    assert client.get("/api/auth/me", headers=hdr).json()["sections"] is None
    for url in ("/api/dispatcher/overview", "/api/dispatcher/meta",
                "/api/reports/processing", "/api/reports/warehouses",
                "/api/warehouses", "/api/metrics/summary", "/api/vessels/placements"):
        r = client.get(url, headers=hdr)
        assert r.status_code == 200, f"{url}: {r.status_code} {r.text}"


def test_login_and_me_carry_sections(client, make_user):
    """Вход и /me отдают личный список — фронт вычитает из него глобальные галки."""
    make_user("sec_me", sections=["statistics", "reports"])
    r = client.post("/api/auth/login", json={"login": "sec_me", "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["sections"] == ["statistics", "reports"]

    hdr = _hdr(client, "sec_me")
    assert client.get("/api/auth/me", headers=hdr).json()["sections"] == ["statistics", "reports"]


# ------------------------------------------------------------- ограничения ----

def test_personal_sections_close_other_sections(client, make_user):
    """Менеджер с «Индикаторами»: остальное — 403, даже когда роль смотреть велит."""
    # vessels.view_roles по умолчанию включает manager, плюс личное право «manage»:
    # без личных вкладок этот человек увидел бы план
    make_user("sec_stats", sections=["statistics"], vessel_right="manage")
    hdr = _hdr(client, "sec_stats")

    assert client.get("/api/dispatcher/overview", headers=hdr).status_code == 200
    assert client.get("/api/dispatcher/meta", headers=hdr).status_code == 200

    for url in ("/api/reports/processing", "/api/warehouses", "/api/metrics/summary",
                "/api/metrics/warehouse-capacity", "/api/vessels/placements",
                "/api/vessels/berths"):
        r = client.get(url, headers=hdr)
        assert r.status_code == 403, f"{url}: ожидали 403, получили {r.status_code}"

    assert "Постановка" in client.get("/api/vessels/placements", headers=hdr).json()["detail"]


def test_personal_sections_do_not_grant_more(client, make_user):
    """Личный список не отменяет права на постановку: «view» без права смотреть — 403."""
    make_user("sec_view_only", sections=["vessels"], vessel_right="none")
    hdr = _hdr(client, "sec_view_only")
    r = client.get("/api/vessels/placements", headers=hdr)
    assert r.status_code == 403


def test_hidden_sections_is_stronger_than_personal(client, admin_hdr, make_user, settings_snapshot):
    """Глобальное скрытие сильнее личного: вкладка из списка есть, а раздела нет."""
    make_user("sec_global", sections=["statistics", "warehouses"])
    hdr = _hdr(client, "sec_global")
    assert client.get("/api/warehouses", headers=hdr).status_code == 200

    r = client.put("/api/admin/settings", json={"ui.hidden_sections": ["warehouses"]},
                   headers=admin_hdr)
    assert r.status_code == 200, r.text

    hidden = client.get("/api/warehouses", headers=hdr)
    assert hidden.status_code == 403
    assert "скрыт для всех" in hidden.json()["detail"]
    # «Отчёты» закрыты лично, а «Склады» ещё и глобально — детализация не открывает
    assert client.get("/api/reports/processing", headers=hdr).status_code == 403
    assert client.get("/api/dispatcher/overview", headers=hdr).status_code == 200


def test_warehouses_section_opens_reports_too(client, make_user):
    """Детализация дашборда читает /api/reports/*: доступен один из разделов — открыто."""
    make_user("sec_wh", sections=["warehouses"])
    hdr = _hdr(client, "sec_wh")
    assert client.get("/api/reports/processing", headers=hdr).status_code == 200
    assert client.get("/api/reports/cargo", headers=hdr).status_code == 200


# ------------------------------------------------------------------- форма ----

def test_empty_or_unknown_sections_rejected(client, admin_hdr, make_user):
    """Без вкладок пользователя оставлять нельзя, неизвестный id — ошибка формы."""
    r = client.post("/api/admin/users", json={
        "login": "sec_empty", "full_name": "Проверка Вкладок Пустых",
        "password": PASSWORD, "role": "manager", "sections": []}, headers=admin_hdr)
    assert r.status_code == 400, r.text
    assert "хотя бы одну" in r.json()["detail"]

    created = make_user("sec_form")
    r = client.patch(f"/api/admin/users/{created['id']}", json={"sections": []}, headers=admin_hdr)
    assert r.status_code == 400, r.text

    r = client.patch(f"/api/admin/users/{created['id']}", json={"sections": ["kassa"]},
                     headers=admin_hdr)
    assert r.status_code == 400 and "kassa" in r.json()["detail"]


def test_sections_dedupe_and_order(client, make_user):
    """Дубликаты и порядок галок не должны гулять: значение нормализуем."""
    created = make_user("sec_order", sections=["vessels", "statistics", "vessels"])
    assert created["sections"] == ["statistics", "vessels"]


def test_null_clears_override(client, admin_hdr, make_user):
    """sections=null снимает личные ограничения — человек снова «по роли»."""
    created = make_user("sec_reset", sections=["statistics"])
    hdr = _hdr(client, "sec_reset")
    assert client.get("/api/reports/processing", headers=hdr).status_code == 403

    r = client.patch(f"/api/admin/users/{created['id']}", json={"sections": None},
                     headers=admin_hdr)
    assert r.status_code == 200, r.text
    assert r.json()["sections"] is None
    assert client.get("/api/reports/processing", headers=hdr).status_code == 200


def test_sections_change_is_audited(client, admin_hdr, make_user):
    """Смена вкладок — действие администратора: оно остаётся в журнале, как vessel_right."""
    created = make_user("sec_audit", sections=["statistics"])
    r = client.patch(f"/api/admin/users/{created['id']}",
                     json={"sections": ["statistics", "reports"]}, headers=admin_hdr)
    assert r.status_code == 200, r.text

    entries = client.get("/api/admin/audit?limit=50", headers=admin_hdr).json()
    mine = [e for e in entries if e["action"] == "admin.user_sections"
            and e["detail"] and e["detail"].startswith("sec_audit")]
    assert mine, "записи о смене вкладок в журнале нет"
    assert mine[0]["detail"] == "sec_audit: statistics → statistics,reports"

    # снятие ограничений тоже остаётся
    client.patch(f"/api/admin/users/{created['id']}", json={"sections": None}, headers=admin_hdr)
    entries = client.get("/api/admin/audit?limit=50", headers=admin_hdr).json()
    assert any(e["action"] == "admin.user_sections" and e["detail"].endswith("→ по роли")
               for e in entries if e["detail"] and e["detail"].startswith("sec_audit"))


def test_sections_column_survives_orm(client, make_user):
    """Колонка реальная (миграция в db._ADDED_COLUMNS), а не виртуальная."""
    from app.models import User
    make_user("sec_db", sections=["reports", "vessels"])
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.login == "sec_db"))
        assert user.sections == "reports,vessels"
        stored = list(db.scalars(select(AuditLog).where(AuditLog.action == "admin.user_create")))
        assert stored, "создание пользователя должно оставаться в журнале"
    finally:
        db.close()
