"""Админка: настройки времени выполнения, интеграция с 1С, справочники и журнал.

Проверяем не только коды ответов, но и то, что настройки действительно применяются
в рантайме (период динамики, пороги, блокировка входа, расписание), а секреты
никогда не уходят наружу.

Важно: настройки глобальные на весь процесс, поэтому фикс `settings_snapshot`
возвращает таблицу `app_settings` к прежнему виду после каждого теста.
Логинимся минимально — троттлинг входа считается по IP всего тест-прогона.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (
    AppSetting, AuditLog, FactCargoBalance, FactProcessingDynamics, FactWarehouseCapacity,
    RefreshToken, SyncRun, User, VesselPlacement, Warehouse,
)
from app.security import new_refresh_token
from app.services import app_settings as store

SETTINGS_URL = "/api/admin/settings"
PUSH_URL = "/api/onec/push"
PUSH_TOKEN = "test-push-token-0001"
SYNC_JOB = "onec_sync"

# Срез для push берём в прошлом: он не должен перетягивать «последний срез»
# витрины на себя и влиять на тесты метрик и отчётов.
PUSH_SNAPSHOT = "2020-01-15 10:00:00"


@pytest.fixture()
def settings_snapshot():
    """После теста — точная копия таблицы настроек из до-состояния."""
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


def _patch(client, admin_hdr, payload: dict):
    return client.put(SETTINGS_URL, json=payload, headers=admin_hdr)


# ------------------------------------------------------------------ настройки ----

def test_settings_envelope_and_secret_masked(client, admin_hdr):
    r = client.get(SETTINGS_URL, headers=admin_hdr)
    assert r.status_code == 200, r.text
    body = r.json()

    assert set(body) >= {"settings", "defaults", "overridden", "specs"}
    settings, defaults = body["settings"], body["defaults"]

    # defaults — это DEFAULTS стора, их админ видит, чтобы «вернуть как было»
    assert defaults["sync.interval_minutes"] == store.DEFAULTS["sync.interval_minutes"]
    assert defaults["thresholds.capacity_crit"] == 95

    # секрет наружу не отдаётся: только маска/пусто + флаг «задан»
    assert settings["onec.password"] in ("", "***")
    assert settings["onec.password_set"] is False
    assert settings["onec.push_token"] in ("", "***")

    # приоритет .env: в тестах SYNC_INTERVAL_MINUTES=999, а DEFAULTS — 15
    assert settings["sync.interval_minutes"] == 999
    assert "sync.interval_minutes" not in body["overridden"]


def test_settings_patch_applies_everywhere(client, admin_hdr, settings_snapshot):
    r = _patch(client, admin_hdr, {"ui.dynamics_days": 21, "reports.default_days": 60})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["settings"]["ui.dynamics_days"] == 21
    assert "ui.dynamics_days" in body["overridden"]
    assert "reports.default_days" in body["overridden"]

    again = client.get(SETTINGS_URL, headers=admin_hdr).json()["settings"]
    assert again["ui.dynamics_days"] == 21, "патч пережил запрос"

    # настройка применяется к метрикам: сутки динамики по умолчанию
    dyn = client.get("/api/metrics/processing-dynamics", headers=admin_hdr).json()
    assert dyn["days"] == 21
    assert len(dyn["points"]) <= 21

    # и к отчётам: период по умолчанию
    rep = client.get("/api/reports/processing", headers=admin_hdr).json()["period"]
    start = datetime.fromisoformat(rep["from"]).date()
    end = datetime.fromisoformat(rep["to"]).date()
    assert (end - start).days + 1 == 60


def test_settings_validation_returns_422_in_russian(client, admin_hdr, settings_snapshot):
    bad = [
        ({"sync.interval_minutes": 0}, "не меньше"),
        ({"onec.timeout_sec": 9999}, "не больше"),
        ({"ui.dynamics_days": "много"}, "нужно число"),
        ({"sync.enabled": "как-то-то-так"}, "да или нет"),
        ({"onec.source": "ftp"}, "допустимо только"),
        ({"no.such.key": 1}, "Неизвестные настройки"),
    ]
    for payload, needle in bad:
        r = _patch(client, admin_hdr, payload)
        assert r.status_code == 422, f"{payload} → {r.status_code}: {r.text}"
        assert needle in r.json()["detail"], r.text

    after = client.get(SETTINGS_URL, headers=admin_hdr).json()
    assert after["overridden"] == [], "неудачный патч ничего не записал"


def test_thresholds_must_be_ordered_and_are_applied(client, admin_hdr, settings_snapshot):
    bad = _patch(client, admin_hdr, {"thresholds.capacity_warn": 90,
                                     "thresholds.capacity_crit": 60})
    assert bad.status_code == 422
    assert "не может быть ниже" in bad.json()["detail"]

    ok = _patch(client, admin_hdr, {"thresholds.capacity_warn": 50})
    assert ok.status_code == 200, ok.text

    rows = client.get("/api/metrics/warehouse-capacity", headers=admin_hdr).json()["rows"]
    assert rows
    for row in rows:
        util = row["utilization"]
        expected = "critical" if util >= 0.95 else ("warning" if util >= 0.50 else "ok")
        assert row["status"] == expected, row
    assert any(row["status"] == "warning" for row in rows), "порог 50 % обязан подкрасить склады"


def test_password_never_leaves_server(client, admin_hdr, settings_snapshot):
    r = _patch(client, admin_hdr, {"onec.password": "s3cret-1C", "onec.login": "statcab_ro"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["settings"]["onec.password"] == "***"
    assert body["settings"]["onec.password_set"] is True
    assert "s3cret-1C" not in r.text

    again = client.get(SETTINGS_URL, headers=admin_hdr)
    assert "s3cret-1C" not in again.text, "секрет не светится и в GET"

    # UI вернул маску — значит пароль не меняли
    keep = _patch(client, admin_hdr, {"onec.password": "***"})
    assert keep.status_code == 200
    assert keep.json()["settings"]["onec.password_set"] is True
    with SessionLocal() as db:
        rec = db.scalar(select(AppSetting).where(AppSetting.key == "onec.password"))
        assert rec and "s3cret" in rec.value, "в БД лежит настоящий пароль, а не маска"

    # UI имеет право вернуть весь набор целиком: маски и флаги *_set не ломают PUT
    whole = client.get(SETTINGS_URL, headers=admin_hdr).json()["settings"]
    back = _patch(client, admin_hdr, whole)
    assert back.status_code == 200, back.text
    assert back.json()["settings"]["onec.password_set"] is True


def test_public_settings_for_any_role(client, admin_hdr, manager_hdr):
    pub = client.get("/api/settings/public", headers=manager_hdr)
    assert pub.status_code == 200, pub.text
    assert set(pub.json()) == set(store.PUBLIC_KEYS)
    assert "onec.password" not in pub.text and "onec.base_url" not in pub.text

    assert client.get(SETTINGS_URL, headers=manager_hdr).status_code == 403
    assert client.post("/api/admin/onec/test", headers=manager_hdr).status_code == 403
    assert client.get("/api/admin/sync/runs", headers=manager_hdr).status_code == 403
    assert client.get(SETTINGS_URL).status_code == 401
    assert client.get("/api/settings/public").status_code == 401


# ------------------------------------------------- скрытые разделы кабинета ----

def test_hidden_sections_default_public_and_admin_editable(client, admin_hdr, manager_hdr, settings_snapshot):
    """Раздел можно прятать всем: значение в публичном срезе, нормализация, защита от «всего»."""
    pub = client.get("/api/settings/public", headers=manager_hdr).json()
    assert pub["ui.hidden_sections"] == [], "по умолчанию все разделы видимы"

    # порядок и дубли нормализует бэк, порядок — как в choices
    r = client.put(SETTINGS_URL, json={"ui.hidden_sections": ["warehouses", "statistics", "warehouses"]},
                   headers=admin_hdr)
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["ui.hidden_sections"] == ["statistics", "warehouses"]
    assert "ui.hidden_sections" in r.json()["overridden"]

    # публичный срез видит новое значение (по нему фронт прячет вкладки)
    pub = client.get("/api/settings/public", headers=manager_hdr).json()
    assert pub["ui.hidden_sections"] == ["statistics", "warehouses"]

    # неизвестный раздел — 422 по-русски
    r = client.put(SETTINGS_URL, json={"ui.hidden_sections": ["kassa"]}, headers=admin_hdr)
    assert r.status_code == 422 and "неизвестные значения" in r.text, r.text

    # спрятать все разделы нельзя — кабинет станет пустым
    r = client.put(SETTINGS_URL,
                   json={"ui.hidden_sections": ["statistics", "warehouses", "reports", "vessels"]},
                   headers=admin_hdr)
    assert r.status_code == 422 and "хотя бы один" in r.text, r.text

    # менеджерам настройки недоступны, как и все остальные
    r = client.put(SETTINGS_URL, json={"ui.hidden_sections": ["reports"]}, headers=manager_hdr)
    assert r.status_code == 403


# ----------------------------------------------------------------------- 1С ----

def test_onec_test_on_mock_is_ok(client, admin_hdr):
    r = client.post("/api/admin/onec/test", headers=admin_hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "mock"
    assert body["ok"] is True
    assert body["checks"] and body["checks"][0]["ok"] is True
    assert body["checks"][0]["error"] is None
    assert body["checks"][0]["ms"] >= 0


def test_onec_test_on_bad_http_url_does_not_crash(client, admin_hdr, settings_snapshot):
    put = _patch(client, admin_hdr, {"onec.source": "http",
                                     "onec.base_url": "http://127.0.0.1:9/StatCabinet",
                                     "onec.timeout_sec": 5})
    assert put.status_code == 200, put.text

    r = client.post("/api/admin/onec/test", headers=admin_hdr)
    assert r.status_code == 200, "проверка связи не имеет права ронять сервис"
    body = r.json()
    assert body["source"] == "http"
    assert body["ok"] is False
    assert [c["path"] for c in body["checks"]] == ["/warehouses", "/balance", "/capacity", "/dynamics"]
    for check in body["checks"]:
        assert check["ok"] is False
        assert check["error"], check
        assert check["status"] is None


def test_onec_test_without_base_url_reports_error(client, admin_hdr, settings_snapshot):
    assert _patch(client, admin_hdr, {"onec.source": "http", "onec.base_url": ""}).status_code == 200
    r = client.post("/api/admin/onec/test", headers=admin_hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["checks"][0]["error"]


def test_manual_sync_and_history(client, admin_hdr, manager_hdr):
    r = client.post("/api/admin/sync/run", headers=admin_hdr)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "ok", run["error"]
    assert run["rows"] > 0
    assert run["source"] == "mock"
    assert run["started_at"] and run["finished_at"]
    assert run["counts"]["rows"] == run["rows"]

    hist = client.get("/api/admin/sync/runs?limit=5", headers=admin_hdr)
    assert hist.status_code == 200
    runs = hist.json()
    assert runs and runs[0]["id"] == run["id"], "свежие записи сверху"
    assert len(runs) <= 5
    ids = [x["id"] for x in runs]
    assert ids == sorted(ids, reverse=True)

    assert client.post("/api/admin/sync/run", headers=manager_hdr).status_code == 403


# -------------------------------------------------------------------- push ----

def _push_payload() -> dict:
    return {
        "snapshot_at": PUSH_SNAPSHOT,
        "warehouses": [{"code": "WH-01", "name": "Склад №1 (морозильник)", "kind": "cold",
                        "capacity": 3200, "unit": "т"}],
        "balance": [
            {"warehouse_code": "WH-01", "cargo_group": "Минтай с/м", "qty": 100, "weight_t": 250.5,
             "overdue_weight_t": 12, "avg_age_days": 20},
            {"warehouse_code": "WH-01", "cargo_group": "Скумбрия с/м", "qty": 40, "weight_t": 90},
        ],
        "capacity": [{"warehouse_code": "WH-01", "used": 1900, "capacity": 3200, "unit": "т"}],
        "dynamics": [
            {"warehouse_code": "WH-01", "date": "2020-01-14", "inbound_t": 100, "outbound_t": 80,
             "processed_t": 180, "docs_count": 12, "avg_dwell_hours": 60},
            {"warehouse_code": "WH-01", "date": "2020-01-15", "inbound_t": 70, "outbound_t": 60,
             "processed_t": 130, "docs_count": 9, "avg_dwell_hours": 55},
        ],
    }


def _fact_counts(snapshot_at: str) -> dict:
    moment = datetime.fromisoformat(snapshot_at)
    with SessionLocal() as db:
        return {
            "cargo_rows": len(db.scalars(select(FactCargoBalance).where(
                FactCargoBalance.snapshot_at == moment)).all()),
            "capacity_rows": len(db.scalars(select(FactWarehouseCapacity).where(
                FactWarehouseCapacity.snapshot_at == moment)).all()),
            "dynamics_rows": len(db.scalars(select(FactProcessingDynamics).where(
                FactProcessingDynamics.bucket_date >= datetime(2020, 1, 1).date(),
                FactProcessingDynamics.bucket_date <= datetime(2020, 12, 31).date())).all()),
        }


def test_push_disabled_without_token(client, admin_hdr, settings_snapshot):
    r = client.post(PUSH_URL, json=_push_payload())
    assert r.status_code == 503
    assert "не настроен" in r.json()["detail"]


def test_push_auth_and_idempotency(client, admin_hdr, settings_snapshot):
    put = _patch(client, admin_hdr, {"onec.push_token": PUSH_TOKEN})
    assert put.status_code == 200, put.text
    assert put.json()["settings"]["onec.push_token"] == "***"
    assert put.json()["settings"]["onec.push_token_set"] is True

    # без токена и с неверным — 401
    assert client.post(PUSH_URL, json=_push_payload()).status_code == 401
    wrong = client.post(PUSH_URL, json=_push_payload(), headers={"X-OneC-Token": PUSH_TOKEN + "x"})
    assert wrong.status_code == 401

    # верный токен — срез лёг в витрину
    ok = client.post(PUSH_URL, json=_push_payload(), headers={"X-OneC-Token": PUSH_TOKEN})
    assert ok.status_code == 200, ok.text
    first = ok.json()
    assert first["status"] == "ok" and first["source"] == "push"
    assert first["snapshot_at"].startswith("2020-01-15")
    assert first["counts"] == {"warehouses": 1, "cargo": 2, "capacity": 1, "dynamics": 2}
    assert first["rows"] == 6

    before = _fact_counts(PUSH_SNAPSHOT)
    assert before["cargo_rows"] == 2 and before["capacity_rows"] == 1 and before["dynamics_rows"] == 2

    # повторный тот же срез — идемпотентен, строки не дублируются
    again = client.post(PUSH_URL, json=_push_payload(), headers={"X-OneC-Token": PUSH_TOKEN})
    assert again.status_code == 200, again.text
    after = _fact_counts(PUSH_SNAPSHOT)
    assert after == before, "повторный push не плодит дубликаты"

    with SessionLocal() as db:
        runs = db.scalars(select(SyncRun).where(SyncRun.source == "push")).all()
        assert len(runs) == 2 and all(r.status == "ok" for r in runs)


def test_push_bad_payload_is_400_not_500(client, admin_hdr, settings_snapshot):
    assert _patch(client, admin_hdr, {"onec.push_token": PUSH_TOKEN}).status_code == 200
    hdr = {"X-OneC-Token": PUSH_TOKEN}

    bad = [
        {},
        {"balance": [{"cargo_group": "без склада"}]},
        {"dynamics": [{"warehouse_code": "WH-01", "date": "не дата"}]},
        {"balance": "не список"},
    ]
    for payload in bad:
        r = client.post(PUSH_URL, json=payload, headers=hdr)
        assert r.status_code == 400, f"{payload} → {r.status_code}: {r.text}"
        assert "Срез не принят" in r.json()["detail"]

    with SessionLocal() as db:
        failed = db.scalars(select(SyncRun).where(SyncRun.source == "push",
                                                  SyncRun.status == "error")).all()
        assert len(failed) == len(bad), "непринятые срезы оседают в журнале"


# --------------------------------------------------- пользователи: блок и удали ----

def _create_user(client, admin_hdr, login: str) -> int:
    r = client.post("/api/admin/users", headers=admin_hdr, json={
        "login": login, "full_name": f"Тестов {login.capitalize()}", "password": "Str0ng-Pass",
        "role": "manager", "warehouse_ids": [],
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _login(client, login: str, password: str):
    return client.post("/api/auth/login", json={"login": login, "password": password})


def test_lockout_uses_settings_and_unlock_works(client, admin_hdr, manager_hdr, settings_snapshot):
    """Порог блокировки — из стора настроек, unlock снимает блокировку."""
    put = _patch(client, admin_hdr, {"security.max_failed_logins": 3,
                                     "security.lockout_minutes": 1})
    assert put.status_code == 200, put.text

    uid = _create_user(client, admin_hdr, "oleg")
    for attempt in range(3):
        r = _login(client, "oleg", "wrong-pass")
        assert r.status_code == 401, f"попытка {attempt}: {r.text}"

    locked = _login(client, "oleg", "Str0ng-Pass")
    assert locked.status_code == 423, locked.text
    assert "заблокирована" in locked.json()["detail"]

    r = client.post(f"/api/admin/users/{uid}/unlock", headers=admin_hdr)
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        user = db.get(User, uid)
        assert user.locked_until is None and user.failed_attempts == 0

    assert _login(client, "oleg", "Str0ng-Pass").status_code == 200, "после unlock вход открыт"
    assert client.post("/api/admin/users/999999/unlock", headers=admin_hdr).status_code == 404
    assert client.post(f"/api/admin/users/{uid}/unlock", headers=manager_hdr).status_code == 403


def test_user_deactivate_then_delete(client, admin_hdr):
    """«Отключить» (PATCH) и «удалить» (DELETE) — разные действия.

    Увольнение — учётку выключают, история остаётся. Оплошность администратора
    (лишняя учётка, опечатка в логине) — удаляют, иначе «отключён» висит в
    списке навсегда. И проверка, что после удаления не остаётся сирот: SQLite
    внешние ключи не включает, поэтому всё чистит код, а не база.
    """
    admin_id = client.get("/api/auth/me", headers=admin_hdr).json()["id"]
    assert client.delete(f"/api/admin/users/{admin_id}", headers=admin_hdr).status_code == 400

    uid = _create_user(client, admin_hdr, "kirill")
    with SessionLocal() as db:
        _, rec = new_refresh_token(uid, family_id="test-family")
        db.add(rec)
        # учётка успела поставить судно: строка плана обязана пережить автора
        plan = VesselPlacement(berth_id=1, lane=1, vessel="DELETE TEST SHIP",
                               starts_at=datetime(2026, 3, 1, 6),
                               ends_at=datetime(2026, 3, 1, 12),
                               created_by=uid, updated_by=uid)
        db.add(plan)
        db.commit()
        token_id, plan_id = rec.id, plan.id

    off = client.patch(f"/api/admin/users/{uid}", headers=admin_hdr, json={"is_active": False})
    assert off.status_code == 200, off.text
    with SessionLocal() as db:
        assert db.get(User, uid).is_active is False, "PATCH выключает учётку, не удаляя"
        assert db.get(RefreshToken, token_id).revoked_at is not None, "сессии отзваны"

    gone = client.delete(f"/api/admin/users/{uid}", headers=admin_hdr)
    assert gone.status_code == 204, gone.text
    with SessionLocal() as db:
        assert db.get(User, uid) is None, "DELETE удаляет учётку целиком"
        assert db.get(RefreshToken, token_id) is None, "сессии не остаются сиротами"
        row = db.get(VesselPlacement, plan_id)
        assert row is not None, "строка плана пережила автора"
        assert row.created_by is None and row.updated_by is None, "автор снят с строки"
        assert db.query(AuditLog).filter(AuditLog.user_id == uid).count() == 0, \
            "в журнале не осталось ссылок на удалённую учётку"
        kept = db.scalars(select(AuditLog).where(AuditLog.action == "admin.user_create",
                                               AuditLog.login == "kirill")).first()
        assert kept is not None, "журнал переживает учётку: автор в нём записан текстом"

    assert client.delete("/api/admin/users/999999", headers=admin_hdr).status_code == 404


# ------------------------------------------------------------------ склады ----

def test_warehouse_crud_and_delete(client, admin_hdr, manager_hdr):
    created = client.post("/api/admin/warehouses", headers=admin_hdr, json={
        "code": "TST-01", "name": "Тестовый склад", "kind": "warehouse",
        "capacity": 500, "unit": "т",
    })
    assert created.status_code == 201, created.text
    wh = created.json()
    assert wh["is_active"] is True and wh["code"] == "TST-01"

    dup = client.post("/api/admin/warehouses", headers=admin_hdr, json={
        "code": "tst-01", "name": "Дубль", "kind": "warehouse",
    })
    assert dup.status_code == 409, dup.text
    assert "уже используется" in dup.json()["detail"]

    bad_kind = client.post("/api/admin/warehouses", headers=admin_hdr, json={
        "code": "TST-02", "name": "Странный", "kind": "загон",
    })
    assert bad_kind.status_code == 400 and "Вид склада" in bad_kind.json()["detail"]

    patched = client.patch(f"/api/admin/warehouses/{wh['id']}", headers=admin_hdr,
                           json={"name": "Тестовый склад №2", "capacity": 750})
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Тестовый склад №2" and patched.json()["capacity"] == 750

    # активный склад виден в метриках
    assert wh["id"] in {w["id"] for w in client.get("/api/warehouses", headers=admin_hdr).json()}

    # выключить — PATCH: склад уходит из метрик, но остаётся в базе
    off = client.patch(f"/api/admin/warehouses/{wh['id']}", headers=admin_hdr,
                       json={"is_active": False})
    assert off.status_code == 200, off.text
    assert off.json()["is_active"] is False
    assert wh["id"] not in {w["id"] for w in client.get("/api/warehouses", headers=admin_hdr).json()}
    client.patch(f"/api/admin/warehouses/{wh['id']}", headers=admin_hdr,
                 json={"is_active": True})

    # склад без статистики удаляется без следа — для этого он и создаётся руками
    gone = client.delete(f"/api/admin/warehouses/{wh['id']}", headers=admin_hdr)
    assert gone.status_code == 204, gone.text
    with SessionLocal() as db:
        assert db.get(Warehouse, wh["id"]) is None, "склад удалён из справочника"
    assert wh["id"] not in {w["id"] for w in client.get("/api/warehouses", headers=admin_hdr).json()}

    # склад со статистикой молча не удаляется: вместе с ним уехали бы отчёты
    with_data = client.post("/api/admin/warehouses", headers=admin_hdr, json={
        "code": "TST-03", "name": "Со статистикой", "kind": "warehouse",
    }).json()
    with SessionLocal() as db:
        db.add(FactCargoBalance(snapshot_at=datetime(2026, 3, 1), warehouse_id=with_data["id"],
                                cargo_group="тест", qty=1, weight_t=2))
        db.add(FactWarehouseCapacity(snapshot_at=datetime(2026, 3, 1),
                                     warehouse_id=with_data["id"], used=10, capacity=100))
        db.commit()
    blocked = client.delete(f"/api/admin/warehouses/{with_data['id']}", headers=admin_hdr)
    assert blocked.status_code == 409, blocked.text
    assert "2 строк статистики" in blocked.json()["detail"], blocked.text
    with SessionLocal() as db:
        assert db.get(Warehouse, with_data["id"]) is not None, "409 ничего не удаляет"

    purge = client.delete(f"/api/admin/warehouses/{with_data['id']}",
                          params={"purge": True}, headers=admin_hdr)
    assert purge.status_code == 204, purge.text
    with SessionLocal() as db:
        assert db.get(Warehouse, with_data["id"]) is None
        left = db.query(FactCargoBalance).filter(
            FactCargoBalance.warehouse_id == with_data["id"]).count()
        assert left == 0, "витрина удаляется вместе со складом, а не осиротевшей"

    assert client.delete("/api/admin/warehouses/999999", headers=admin_hdr).status_code == 404

    assert client.patch("/api/admin/warehouses/999999", headers=admin_hdr,
                        json={"name": "??"}).status_code == 404
    assert client.get("/api/admin/warehouses", headers=manager_hdr).status_code == 403


# -------------------------------------------------------------------- журнал ----

def test_audit_log_records_actions(client, admin_hdr, manager_hdr, settings_snapshot):
    assert _patch(client, admin_hdr, {"reports.default_days": 90}).status_code == 200

    r = client.get("/api/admin/audit?limit=50", headers=admin_hdr)
    assert r.status_code == 200, r.text
    entries = r.json()
    assert entries, "журнал не пустой"
    assert {"id", "created_at", "login", "action", "detail"} <= set(entries[0])

    settings_rows = [e for e in entries if e["action"] == "admin.settings_update"]
    assert settings_rows and settings_rows[0]["login"] == "admin"
    assert settings_rows[0]["detail"].startswith("ключи:")
    assert "reports.default_days" in settings_rows[0]["detail"]

    assert len(client.get("/api/admin/audit?limit=1", headers=admin_hdr).json()) == 1
    assert client.get("/api/admin/audit", headers=manager_hdr).status_code == 403


# ------------------------------------------------------- перепланирование 1С ----

def test_reschedule_from_settings(client, admin_hdr, settings_snapshot):
    """PUT настроек меняет расписание живого процесса: интервал и наличие job-а."""
    scheduler = app.state.scheduler
    assert scheduler is not None and scheduler.running
    assert scheduler.get_job(SYNC_JOB) is None, "в тестах SYNC_ENABLED=false"

    on = _patch(client, admin_hdr, {"sync.enabled": True, "sync.interval_minutes": 120})
    assert on.status_code == 200, on.text
    assert on.json()["scheduler"]["job"] == "added"
    assert on.json()["scheduler"]["interval_minutes"] == 120

    job = scheduler.get_job(SYNC_JOB)
    assert job is not None
    assert int(job.trigger.interval.total_seconds() // 60) == 120

    moved = _patch(client, admin_hdr, {"sync.interval_minutes": 30})
    assert moved.json()["scheduler"]["job"] == "rescheduled"
    assert int(scheduler.get_job(SYNC_JOB).trigger.interval.total_seconds() // 60) == 30

    off = _patch(client, admin_hdr, {"sync.enabled": False})
    assert off.json()["scheduler"]["job"] == "removed"
    assert scheduler.get_job(SYNC_JOB) is None
