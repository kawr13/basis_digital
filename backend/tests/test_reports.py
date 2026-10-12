"""Отчёты: период, разрез по складам, CSV, права."""
from __future__ import annotations

from datetime import date, timedelta


def test_processing_report_scoped_to_manager(client, manager_hdr):
    r = client.get("/api/reports/processing", headers=manager_hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    wh = client.get("/api/warehouses", headers=manager_hdr).json()
    allowed = {w["id"] for w in wh}

    assert body["rows"], "за 30 суток должны быть строки"
    assert {row["warehouse_id"] for row in body["rows"]} <= allowed
    assert {w["warehouse_id"] for w in body["per_warehouse"]} <= allowed
    assert body["totals"]["processed_t"] > 0


def test_processing_report_period_filter(client, admin_hdr):
    end = date.today()
    start = end - timedelta(days=2)
    r = client.get(f"/api/reports/processing?from={start}&to={end}", headers=admin_hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["period"]["from"] == start.isoformat()
    assert body["period"]["to"] == end.isoformat()
    dates = {row["bucket_date"] for row in body["rows"]}
    assert dates and all(start.isoformat() <= d <= end.isoformat() for d in dates)
    assert body["totals"]["days"] <= 3


def test_drilldown_day_and_warehouse_contract(client, admin_hdr, manager_hdr):
    """Детализация дашборда: клик по столбцу (сутки) и по строке ёмкости (склад)."""
    rows = client.get("/api/reports/processing", headers=admin_hdr).json()["rows"]
    assert rows, "демо-витрина обязана отдать данные"
    day = max(row["bucket_date"] for row in rows)

    r = client.get(f"/api/reports/processing?from={day}&to={day}", headers=admin_hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["period"] == {"from": day, "to": day}
    assert {row["bucket_date"] for row in body["rows"]} == {day}
    assert body["totals"]["days"] == 1
    keys = ("inbound_t", "outbound_t", "processed_t", "docs_count", "avg_dwell_hours")
    assert all(k in body["totals"] for k in keys)
    assert body["per_warehouse"] and all(all(k in w for k in keys) for w in body["per_warehouse"])

    # режим «склад»: менеджер видит только назначенный склад — и в обработке, и в остатках
    mine = client.get("/api/reports/warehouses", headers=manager_hdr).json()
    one = client.get(f"/api/reports/processing?warehouses={mine[0]['id']}", headers=manager_hdr)
    assert one.status_code == 200
    assert {row["warehouse_id"] for row in one.json()["rows"]} <= {mine[0]["id"]}
    cargo = client.get(f"/api/reports/cargo?warehouses={mine[0]['id']}", headers=manager_hdr)
    assert cargo.status_code == 200
    assert {row["warehouse_id"] for row in cargo.json()["rows"]} <= {mine[0]["id"]}


def test_report_period_is_capped(client, admin_hdr):
    """Нельзя вытащить «всю историю» одним запросом."""
    r = client.get("/api/reports/processing?from=2000-01-01", headers=admin_hdr)
    assert r.status_code == 200
    start = date.fromisoformat(r.json()["period"]["from"])
    assert (date.today() - start).days <= 366


def test_report_warehouse_filter_respects_rights(client, admin_hdr, manager_hdr):
    all_wh = client.get("/api/reports/warehouses", headers=admin_hdr).json()
    mine = client.get("/api/reports/warehouses", headers=manager_hdr).json()
    foreign = [w["id"] for w in all_wh if w["id"] not in {m["id"] for m in mine}]

    ok = client.get(f"/api/reports/processing?warehouses={mine[0]['id']}", headers=manager_hdr)
    assert ok.status_code == 200
    assert {row["warehouse_id"] for row in ok.json()["rows"]} == {mine[0]["id"]}

    denied = client.get(f"/api/reports/processing?warehouses={foreign[0]}", headers=manager_hdr)
    assert denied.status_code == 403

    bad = client.get("/api/reports/processing?warehouses=abc", headers=manager_hdr)
    assert bad.status_code == 400


def test_csv_export_format(client, manager_hdr):
    r = client.get("/api/reports/processing.csv", headers=manager_hdr)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    text = r.text
    assert text.startswith("\ufeff"), "BOM обязателен, иначе Excel не поймёт кириллицу"
    header, first = text.lstrip("\ufeff").splitlines()[:2]
    assert header.split(";")[0] == "Дата"
    assert ";" in header and "," not in header.split(";")[0]
    assert "," in first, "десятичный разделитель — запятая (русская локаль Excel)"


def test_cargo_report_and_csv(client, admin_hdr):
    r = client.get("/api/reports/cargo", headers=admin_hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["snapshot_at"] and body["rows"]
    assert body["totals"]["weight_t"] > 0
    assert body["totals"]["positions"] == len(body["rows"])

    csv_resp = client.get("/api/reports/cargo.csv", headers=admin_hdr)
    assert csv_resp.status_code == 200
    assert "Группа груза" in csv_resp.text


def test_capacity_trend(client, admin_hdr):
    r = client.get("/api/reports/capacity-trend", headers=admin_hdr)
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert rows
    assert 0 <= rows[0]["utilization"] <= 3


def test_reports_need_auth(client):
    for path in ["/api/reports/processing", "/api/reports/cargo",
                 "/api/reports/capacity-trend", "/api/reports/processing.csv"]:
        assert client.get(path).status_code == 401
