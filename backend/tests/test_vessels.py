"""Тесты документа «Постановка судов»: справочник причалов, ручные строки плана,
просмотр за любой период, пересечения на дорожке и права."""
from __future__ import annotations

from datetime import date, datetime, timedelta

PERIOD = {"from": "2026-05-01", "to": "2026-05-31"}


def _berth_id(client, hdr, code="РП-1") -> int:
    rows = client.get("/api/vessels/berths", headers=hdr).json()
    return next(b["id"] for b in rows if b["code"] == code)


def _mk(client, hdr, *, code="РП-1", lane=1, vessel="TEST SHIP", start="2026-05-03T06:00:00",
        end="2026-05-05T12:00:00", **kw):
    body = {
        "berth_id": _berth_id(client, hdr, code), "lane": lane, "vessel": vessel,
        "starts_at": start, "ends_at": end, **kw,
    }
    return client.post("/api/vessels/placements", json=body, headers=hdr)


# ------------------------------------------------------------------ справочник ----


def test_berths_seeded(client, admin_hdr):
    rows = client.get("/api/vessels/berths", headers=admin_hdr).json()
    assert len(rows) >= 6, rows
    assert rows[0]["code"] == "РП-1"
    assert rows[0]["length_m"] == 100 and rows[0]["depth"] == 8.3


def test_berth_create_and_duplicate(client, admin_hdr):
    r = client.post("/api/vessels/berths", json={"code": "Тest-99", "length_m": 90, "lanes": 1},
                    headers=admin_hdr)
    assert r.status_code == 201, r.text
    again = client.post("/api/vessels/berths", json={"code": "Тest-99"}, headers=admin_hdr)
    assert again.status_code == 409


# ------------------------------------------------------------------- создание ----


def test_create_and_read(client, admin_hdr):
    r = _mk(client, admin_hdr, vessel="CROWN SAPPHIRE", volume="5 500", cargo="БШ",
            note="сборник")
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["vessel"] == "CROWN SAPPHIRE" and row["cargo"] == "БШ"
    assert row["starts_at"] == "2026-05-03T06:00:00"

    data = client.get("/api/vessels/placements", params=PERIOD, headers=admin_hdr).json()
    got = [p for p in data["placements"] if p["id"] == row["id"]]
    assert got and got[0]["lane"] == 1
    assert data["period"] == {"from": "2026-05-01", "to": "2026-05-31"}
    assert data["slot_hours"] == [0, 6, 12, 18]


def test_period_covers_running_placement(client, admin_hdr):
    """План «непрерывный»: судно, начатое в апреле, видно и в мае."""
    r = _mk(client, admin_hdr, code="РП-2", vessel="APRIL SHIP",
            start="2026-04-25T18:00:00", end="2026-05-02T00:00:00")
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    may = client.get("/api/vessels/placements", params=PERIOD, headers=admin_hdr).json()
    assert pid in [p["id"] for p in may["placements"]]

    # период целиком после окончания — уже нет
    june = client.get("/api/vessels/placements",
                      params={"from": "2026-06-01", "to": "2026-06-30"}, headers=admin_hdr).json()
    assert pid not in [p["id"] for p in june["placements"]]

    # период целиком до начала — тоже нет
    march = client.get("/api/vessels/placements",
                       params={"from": "2026-03-01", "to": "2026-03-31"}, headers=admin_hdr).json()
    assert pid not in [p["id"] for p in march["placements"]]


def test_period_default_is_forward(client, admin_hdr):
    data = client.get("/api/vessels/placements", headers=admin_hdr).json()
    d_from, d_to = date.fromisoformat(data["period"]["from"]), date.fromisoformat(data["period"]["to"])
    assert d_from == date.today() and d_to > d_from


def test_period_reversed_is_normalized(client, admin_hdr):
    data = client.get("/api/vessels/placements",
                      params={"from": "2026-05-31", "to": "2026-05-01"}, headers=admin_hdr).json()
    assert data["period"] == {"from": "2026-05-01", "to": "2026-05-31"}


# ------------------------------------------------------------------- правила ----


def test_conflict_same_lane(client, admin_hdr):
    a = _mk(client, admin_hdr, code="РП-3", lane=1, vessel="FIRST",
            start="2026-05-10T00:00:00", end="2026-05-12T00:00:00")
    assert a.status_code == 201, a.text
    b = _mk(client, admin_hdr, code="РП-3", lane=1, vessel="SECOND",
            start="2026-05-11T12:00:00", end="2026-05-13T00:00:00")
    assert b.status_code == 409
    assert "FIRST" in b.json()["detail"] and "РП-3" in b.json()["detail"]

    # та же дата, но другая дорожка — можно
    c = _mk(client, admin_hdr, code="РП-3", lane=2, vessel="SECOND",
            start="2026-05-11T12:00:00", end="2026-05-13T00:00:00")
    assert c.status_code == 201, c.text


def test_touching_slots_do_not_conflict(client, admin_hdr):
    """Одно судно ушло в 12:00, другое пришло в 12:00 — слот один, не пересечение?

    Нет: границы включительные, значит второй слот начинается ровно там же, где
    занят первый. Пересечение есть — это и есть защита от «нарисовать два судна
    в одной клетке».
    """
    a = _mk(client, admin_hdr, code="РП-4", lane=1, vessel="LEAVES",
            start="2026-06-10T00:00:00", end="2026-06-10T12:00:00")
    assert a.status_code == 201, a.text
    b = _mk(client, admin_hdr, code="РП-4", lane=1, vessel="ARRIVES",
            start="2026-06-10T18:00:00", end="2026-06-11T00:00:00")
    assert b.status_code == 201, b.text


def test_bad_slot_time(client, admin_hdr):
    r = _mk(client, admin_hdr, code="РП-5", vessel="WRONG TIME", start="2026-05-03T07:00:00",
            end="2026-05-04T00:00:00")
    assert r.status_code == 400 and "слот" in r.json()["detail"].lower()


def test_end_before_start(client, admin_hdr):
    r = _mk(client, admin_hdr, code="РП-5", vessel="BACKWARD", start="2026-05-06T00:00:00",
            end="2026-05-04T00:00:00")
    assert r.status_code == 400


def test_lane_beyond_berth(client, admin_hdr):
    """У РП-1 две дорожки — третья не помещается физически."""
    r = _mk(client, admin_hdr, code="РП-1", lane=3, vessel="EXTRA LANE",
            start="2026-07-01T00:00:00", end="2026-07-02T00:00:00")
    assert r.status_code == 400 and "дорожка" in r.json()["detail"]


def test_unknown_berth(client, admin_hdr):
    r = client.post("/api/vessels/placements",
                    json={"berth_id": 999999, "vessel": "GHOST",
                          "starts_at": "2026-05-01T00:00:00", "ends_at": "2026-05-02T00:00:00"},
                    headers=admin_hdr)
    assert r.status_code == 400


# --------------------------------------------------------------- правка/удаление ----


def test_update_moves_and_allows_own_slot(client, admin_hdr):
    r = _mk(client, admin_hdr, code="РП-6", lane=1, vessel="MOVER",
            start="2026-08-01T00:00:00", end="2026-08-02T00:00:00")
    pid = r.json()["id"]
    # переносим в ту же самую клетку: сама с собой конфликта быть не должно
    upd = client.patch(f"/api/vessels/placements/{pid}",
                       json={"starts_at": "2026-08-01T00:00:00", "ends_at": "2026-08-02T12:00:00",
                             "cargo": "МЛК (БКК)"},
                       headers=admin_hdr)
    assert upd.status_code == 200, upd.text
    assert upd.json()["cargo"] == "МЛК (БКК)" and upd.json()["ends_at"] == "2026-08-02T12:00:00"

    # а перенос на соседнее судно — конфликт
    other = _mk(client, admin_hdr, code="РП-6", lane=1, vessel="BLOCKER",
                start="2026-08-05T00:00:00", end="2026-08-06T00:00:00")
    assert other.status_code == 201
    clash = client.patch(f"/api/vessels/placements/{pid}",
                         json={"starts_at": "2026-08-05T12:00:00", "ends_at": "2026-08-06T06:00:00"},
                         headers=admin_hdr)
    assert clash.status_code == 409 and "BLOCKER" in clash.json()["detail"]


def test_delete_only_admin(client, admin_hdr, manager_hdr):
    pid = _mk(client, admin_hdr, code="РП-1", lane=2, vessel="TO DELETE",
              start="2026-09-01T00:00:00", end="2026-09-02T00:00:00").json()["id"]
    assert client.delete(f"/api/vessels/placements/{pid}", headers=manager_hdr).status_code == 403
    assert client.delete(f"/api/vessels/placements/{pid}", headers=admin_hdr).status_code == 204
    data = client.get("/api/vessels/placements",
                      params={"from": "2026-09-01", "to": "2026-09-30"}, headers=admin_hdr).json()
    assert pid not in [p["id"] for p in data["placements"]]


# ---------------------------------------------------------------------- права ----


def test_manager_reads_but_not_writes(client, manager_hdr):
    data = client.get("/api/vessels/placements", params=PERIOD, headers=manager_hdr)
    assert data.status_code == 200
    assert len(data.json()["berths"]) >= 6
    r = _mk(client, manager_hdr, vessel="MANAGER TRY")
    assert r.status_code == 403


def test_top_can_write(client):
    tok = client.post("/api/auth/login",
                      json={"login": "ivanova", "password": "demo1234"}).json()["access_token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    r = _mk(client, hdr, code="РП-2", lane=2, vessel="TOP PLAN",
            start="2026-10-01T00:00:00", end="2026-10-03T06:00:00")
    assert r.status_code == 201, r.text


def test_anonymous_denied(client):
    assert client.get("/api/vessels/placements").status_code == 401


def test_audit_records_actions(client, admin_hdr):
    """Правки плана должны оставаться в журнале — это документ, а не заметка."""
    _mk(client, admin_hdr, code="РП-3", lane=2, vessel="AUDIT SHIP",
        start="2026-11-01T00:00:00", end="2026-11-02T00:00:00")
    rows = client.get("/api/admin/audit", params={"limit": 200}, headers=admin_hdr).json()
    actions = [r["action"] for r in rows]
    assert "vessels.create" in actions
    detail = next(r["detail"] for r in rows if r["action"] == "vessels.create")
    assert "AUDIT SHIP" in detail


# ----------------------------------------------------- роли из настроек админки ----


def _set_settings(client, hdr, patch: dict):
    return client.put("/api/admin/settings", json=patch, headers=hdr)


def _reset(client, hdr, keys):
    return client.post("/api/admin/settings/reset", json=keys, headers=hdr)


def test_roles_come_from_settings(client, admin_hdr, manager_hdr):
    """Право ставить суда можно выдать менеджеру настройкой, без смены роли."""
    keys = ["vessels.view_roles", "vessels.edit_roles", "vessels.delete_roles"]
    try:
        r = _set_settings(client, admin_hdr, {"vessels.edit_roles": "manager"})
        assert r.status_code == 200, r.text

        made = _mk(client, manager_hdr, code="РП-4", lane=2, vessel="MANAGER NOW",
                   start="2027-01-05T00:00:00", end="2027-01-06T00:00:00")
        assert made.status_code == 201, made.text
        pid = made.json()["id"]
        # но удалять по-прежнему нельзя: право на удаление — отдельная настройка
        assert client.delete(f"/api/vessels/placements/{pid}", headers=manager_hdr).status_code == 403
        assert client.delete(f"/api/vessels/placements/{pid}", headers=admin_hdr).status_code == 204
    finally:
        _reset(client, admin_hdr, keys)

    # после сброса менеджер снова только наблюдает
    assert _mk(client, manager_hdr, code="РП-4", lane=2, vessel="AFTER RESET",
               start="2027-02-05T00:00:00", end="2027-02-06T00:00:00").status_code == 403


def test_view_can_be_closed_for_role(client, admin_hdr, manager_hdr):
    keys = ["vessels.view_roles"]
    try:
        assert _set_settings(client, admin_hdr, {"vessels.view_roles": "admin,top"}).status_code == 200
        r = client.get("/api/vessels/placements", params=PERIOD, headers=manager_hdr)
        assert r.status_code == 403 and "закрыт" in r.json()["detail"]
        assert client.get("/api/vessels/berths", headers=manager_hdr).status_code == 403
        assert client.get("/api/vessels/placements", params=PERIOD, headers=admin_hdr).status_code == 200
    finally:
        _reset(client, admin_hdr, keys)


def test_unknown_role_in_setting_rejected(client, admin_hdr):
    r = _set_settings(client, admin_hdr, {"vessels.edit_roles": "dispatcher"})
    assert r.status_code == 422
    assert "admin, top, manager" in r.json()["detail"]
    # пустой список тоже не проходят: план не должен остаться без владельцев
    assert _set_settings(client, admin_hdr, {"vessels.delete_roles": ""}).status_code == 422


def test_public_settings_expose_roles(client, manager_hdr):
    """Фронт рисует кнопки по этим же значениям — иначе права расходятся с UI."""
    pub = client.get("/api/settings/public", headers=manager_hdr).json()
    roles = lambda k: set(pub[k].split(","))
    assert roles("vessels.view_roles") == {"admin", "top", "manager"}
    assert roles("vessels.edit_roles") == {"admin", "top"}
    assert roles("vessels.delete_roles") == {"admin"}


# ------------------------------------------- личное право пользователя (как склады) ----


def _user_id(client, hdr, login):
    return next(u["id"] for u in client.get("/api/admin/users", headers=hdr).json() if u["login"] == login)


def _token(client, login, password):
    return client.post("/api/auth/login", json={"login": login, "password": password}).json()["access_token"]


def test_personal_right_beats_role(client, admin_hdr):
    """Диспетчеру можно выдать постановку прямо в карточке пользователя, не меняя роль."""
    uid = _user_id(client, admin_hdr, "sidorova")
    hdr = {"Authorization": f"Bearer {_token(client, 'sidorova', 'demo1234')}"}
    try:
        # по роли менеджер ставить не может
        assert _mk(client, hdr, code="РП-5", lane=2, vessel="BEFORE GRANT",
                   start="2027-03-01T00:00:00", end="2027-03-02T00:00:00").status_code == 403

        r = client.patch(f"/api/admin/users/{uid}", json={"vessel_right": "edit"}, headers=admin_hdr)
        assert r.status_code == 200, r.text
        assert r.json()["vessel_right"] == "edit"

        made = _mk(client, hdr, code="РП-5", lane=2, vessel="DISPATCHER",
                   start="2027-03-01T00:00:00", end="2027-03-02T00:00:00")
        assert made.status_code == 201, made.text
        # но удаление остаётся закрытым: «edit» не включает «delete»
        assert client.delete(f"/api/vessels/placements/{made.json()['id']}", headers=hdr).status_code == 403
    finally:
        client.patch(f"/api/admin/users/{uid}", json={"vessel_right": None}, headers=admin_hdr)

    assert client.get("/api/admin/users", headers=admin_hdr).json()  # не упало
    assert _mk(client, hdr, code="РП-5", lane=2, vessel="AFTER REVOKE",
               start="2027-04-01T00:00:00", end="2027-04-02T00:00:00").status_code == 403


def test_personal_none_closes_even_admin(client, admin_hdr):
    """Личное «нет доступа» перекрывает роль: админу закрыли — админ не видит."""
    uid = _user_id(client, admin_hdr, "admin")
    try:
        assert client.patch(f"/api/admin/users/{uid}", json={"vessel_right": "none"},
                            headers=admin_hdr).status_code == 200
        assert client.get("/api/vessels/placements", params=PERIOD, headers=admin_hdr).status_code == 403
        assert client.get("/api/vessels/berths", headers=admin_hdr).status_code == 403
    finally:
        assert client.patch(f"/api/admin/users/{uid}", json={"vessel_right": None},
                            headers=admin_hdr).status_code == 200
    assert client.get("/api/vessels/placements", params=PERIOD, headers=admin_hdr).status_code == 200


def test_view_only_right(client, admin_hdr):
    uid = _user_id(client, admin_hdr, "petrov")
    hdr = {"Authorization": f"Bearer {_token(client, 'petrov', 'demo1234')}"}
    try:
        assert client.patch(f"/api/admin/users/{uid}", json={"vessel_right": "view"},
                            headers=admin_hdr).status_code == 200
        assert client.get("/api/vessels/placements", params=PERIOD, headers=hdr).status_code == 200
        assert _mk(client, hdr, code="РП-6", lane=2, vessel="READER",
                   start="2027-05-01T00:00:00", end="2027-05-02T00:00:00").status_code == 403
    finally:
        client.patch(f"/api/admin/users/{uid}", json={"vessel_right": None}, headers=admin_hdr)


def test_bad_right_rejected(client, admin_hdr):
    uid = _user_id(client, admin_hdr, "sidorova")
    r = client.patch(f"/api/admin/users/{uid}", json={"vessel_right": "superuser"}, headers=admin_hdr)
    assert r.status_code == 400 and "none, view, edit, manage" in r.json()["detail"]


def test_manage_right_includes_delete(client, admin_hdr):
    uid = _user_id(client, admin_hdr, "sidorova")
    hdr = {"Authorization": f"Bearer {_token(client, 'sidorova', 'demo1234')}"}
    try:
        assert client.patch(f"/api/admin/users/{uid}", json={"vessel_right": "manage"},
                            headers=admin_hdr).status_code == 200
        pid = _mk(client, hdr, code="РП-1", lane=1, vessel="MANAGER DELETES",
                  start="2027-06-01T00:00:00", end="2027-06-02T00:00:00").json()["id"]
        assert client.delete(f"/api/vessels/placements/{pid}", headers=hdr).status_code == 204
    finally:
        client.patch(f"/api/admin/users/{uid}", json={"vessel_right": None}, headers=admin_hdr)


def test_rights_ship_with_the_user(client, admin_hdr):
    """Вход и /me отдают посчитанные права — фронт прячет вкладку сразу, без
    отдельного запроса настроек (иначе запрещённая вкладка успевает мигнуть)."""
    body = client.post("/api/auth/login", json={"login": "petrov", "password": "demo1234"}).json()
    assert body["user"]["vessel_rights"] == {"view": True, "edit": False, "delete": False}

    hdr = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.get("/api/auth/me", headers=hdr).json()["vessel_rights"]["view"] is True

    # закрываем просмотр менеджеру — права уезжают те же, что и у API
    assert client.put("/api/admin/settings", json={"vessels.view_roles": "admin,top"},
                      headers=admin_hdr).status_code == 200
    assert client.get("/api/auth/me", headers=hdr).json()["vessel_rights"] == {
        "view": False, "edit": False, "delete": False,
    }
    assert client.get("/api/vessels/placements", params=PERIOD, headers=hdr).status_code == 403
