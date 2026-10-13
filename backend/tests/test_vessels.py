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
