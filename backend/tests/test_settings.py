"""Персональные настройки: раскладка дашборда и фильтры отчётов."""
from __future__ import annotations


def test_layout_roundtrip(client, admin_hdr):
    layout = {
        "version": 1,
        "widgets": [
            {"id": "cargo_balance", "span": 6, "visible": True},
            {"id": "capacity", "span": 4, "visible": False},
        ],
    }
    put = client.put("/api/settings/preferences/dashboard_layout", json={"value": layout},
                     headers=admin_hdr)
    assert put.status_code == 200, put.text

    got = client.get("/api/settings/preferences/dashboard_layout", headers=admin_hdr)
    assert got.status_code == 200
    assert got.json()["value"] == layout

    all_prefs = client.get("/api/settings/preferences", headers=admin_hdr).json()
    assert all_prefs["dashboard_layout"]["widgets"][0]["id"] == "cargo_balance"


def test_layout_is_per_user(client, admin_hdr, manager_hdr):
    client.put("/api/settings/preferences/dashboard_layout",
               json={"value": {"version": 1, "widgets": [{"id": "kpi", "span": 3, "visible": True}]}},
               headers=manager_hdr)

    manager = client.get("/api/settings/preferences/dashboard_layout", headers=manager_hdr).json()["value"]
    admin = client.get("/api/settings/preferences/dashboard_layout", headers=admin_hdr).json()["value"]
    assert manager["widgets"][0]["id"] == "kpi"
    assert admin["widgets"][0]["id"] == "cargo_balance", "настройки не перетекают между пользователями"


def test_unknown_key_rejected(client, admin_hdr):
    r = client.put("/api/settings/preferences/secret_passwords", json={"value": {"a": 1}},
                   headers=admin_hdr)
    assert r.status_code == 400


def test_oversized_value_rejected(client, admin_hdr):
    r = client.put("/api/settings/preferences/dashboard_layout",
                   json={"value": {"blob": "x" * 30000}}, headers=admin_hdr)
    assert r.status_code == 413


def test_reset_layout(client, manager_hdr):
    assert client.delete("/api/settings/preferences/dashboard_layout", headers=manager_hdr).status_code == 204
    got = client.get("/api/settings/preferences/dashboard_layout", headers=manager_hdr)
    assert got.status_code == 200 and got.json()["value"] is None


def test_settings_need_auth(client):
    assert client.get("/api/settings/preferences").status_code == 401
    assert client.put("/api/settings/preferences/dashboard_layout", json={"value": {}}).status_code == 401
