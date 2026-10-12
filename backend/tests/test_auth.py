"""Тесты авторизации и разграничения доступа."""
from __future__ import annotations

from .conftest import ADMIN


def _login(client, login, password):
    return client.post("/api/auth/login", json={"login": login, "password": password})


def test_login_ok_and_me(client):
    r = _login(client, **ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"] and body["user"]["role"] == "admin"
    assert "sc_refresh" in r.cookies

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["login"] == "admin"
    assert me.json()["all_warehouses"] is True


def test_login_wrong_password_401(client):
    r = _login(client, "admin", "nope-nope-1")
    assert r.status_code == 401
    assert "Неверный" in r.json()["detail"]


def test_me_requires_token(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_refresh_rotation_and_reuse_detection(client):
    r = _login(client, **ADMIN)
    first = r.cookies["sc_refresh"]

    r2 = client.post("/api/auth/refresh", cookies={"sc_refresh": first})
    assert r2.status_code == 200, r2.text
    second = r2.cookies["sc_refresh"]
    assert second != first

    # повторное использование уже повёрнутого токена = украли сессию
    r3 = client.post("/api/auth/refresh", cookies={"sc_refresh": first})
    assert r3.status_code == 401

    # и новая ветка семьи тоже отозвана
    r4 = client.post("/api/auth/refresh", cookies={"sc_refresh": second})
    assert r4.status_code == 401


def test_logout_kills_session(client):
    r = _login(client, **ADMIN)
    token = r.json()["access_token"]
    refresh = r.cookies["sc_refresh"]
    client.post("/api/auth/logout", cookies={"sc_refresh": refresh})
    assert client.post("/api/auth/refresh", cookies={"sc_refresh": refresh}).status_code == 401
    # access живёт до истечения TTL — это осознанно (короткое время жизни)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_change_password_revokes_sessions(client):
    r = _login(client, **ADMIN)
    token, refresh = r.json()["access_token"], r.cookies["sc_refresh"]
    ch = client.post("/api/auth/change-password",
                     headers={"Authorization": f"Bearer {token}"},
                     json={"old_password": ADMIN["password"], "new_password": "N3w-Passw0rd!"})
    assert ch.status_code == 204
    assert client.post("/api/auth/refresh", cookies={"sc_refresh": refresh}).status_code == 401
    assert _login(client, "admin", "N3w-Passw0rd!").status_code == 200
    # возвращаем пароль остальным тестам
    t2 = _login(client, "admin", "N3w-Passw0rd!").json()["access_token"]
    assert client.post("/api/auth/change-password", headers={"Authorization": f"Bearer {t2}"},
                       json={"old_password": "N3w-Passw0rd!", "new_password": ADMIN["password"]}).status_code == 204


def test_lockout_after_failed_attempts(client):
    r = _login(client, **ADMIN)
    hdr = {"Authorization": f"Bearer {r.json()['access_token']}"}
    created = client.post("/api/admin/users", headers=hdr, json={
        "login": "victor", "full_name": "Тестов Виктор", "password": "Str0ng-Pass",
        "role": "manager", "warehouse_ids": [],
    })
    assert created.status_code == 201, created.text

    for _ in range(5):
        assert _login(client, "victor", "wrong-password").status_code == 401
    locked = _login(client, "victor", "Str0ng-Pass")
    assert locked.status_code == 423
    assert "заблокирована" in locked.json()["detail"]


def test_admin_can_create_disable_and_reenable_user(client, admin_hdr):
    lst = client.get("/api/admin/users", headers=admin_hdr)
    assert lst.status_code == 200
    ids = {u["login"]: u["id"] for u in lst.json()}

    off = client.patch(f"/api/admin/users/{ids['victor']}", headers=admin_hdr, json={"is_active": False})
    assert off.status_code == 200
    assert _login(client, "victor", "Str0ng-Pass").status_code == 403

    on = client.patch(f"/api/admin/users/{ids['victor']}", headers=admin_hdr, json={"is_active": True})
    assert on.status_code == 200
    # после отключения блокировка заодно снята
    assert _login(client, "victor", "Str0ng-Pass").status_code == 200


def test_manager_cannot_use_admin_api(client, manager_hdr):
    assert client.get("/api/admin/users", headers=manager_hdr).status_code == 403
    assert client.post("/api/sync/run", headers=manager_hdr).status_code == 403


def test_metrics_scoped_by_warehouse(client, admin_hdr, manager_hdr):
    all_wh = client.get("/api/warehouses", headers=admin_hdr).json()
    mgr_wh = client.get("/api/warehouses", headers=manager_hdr).json()
    assert len(all_wh) >= 5
    assert len(mgr_wh) == 2, "менеджеру доступны только назначенные склады"

    cap_all = client.get("/api/metrics/warehouse-capacity", headers=admin_hdr).json()
    cap_mgr = client.get("/api/metrics/warehouse-capacity", headers=manager_hdr).json()
    assert cap_all["rows"] and cap_all["snapshot_at"]
    assert len(cap_mgr["rows"]) == 2
    assert {r["warehouse_id"] for r in cap_mgr["rows"]} == {w["id"] for w in mgr_wh}
    for row in cap_all["rows"]:
        assert row["status"] in {"ok", "warning", "critical"}
        assert 0 <= row["utilization"] <= 3


def test_metrics_need_auth(client):
    for path in ["/api/metrics/summary", "/api/metrics/cargo-balance",
                 "/api/metrics/warehouse-capacity", "/api/metrics/processing-dynamics"]:
        assert client.get(path).status_code == 401


def test_dynamics_and_summary(client, admin_hdr):
    dyn = client.get("/api/metrics/processing-dynamics?days=14", headers=admin_hdr).json()
    assert len(dyn["points"]) >= 10
    assert dyn["processed_total"] > 0
    assert dyn["delta_pct"] is not None

    summ = client.get("/api/metrics/summary", headers=admin_hdr).json()
    assert summ["cargo_weight_t"] > 0
    assert summ["warehouses"] >= 5
    assert 0 < summ["utilization"] < 2
