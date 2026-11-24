"""П. 26–28 TASKS2.md: админ переводит кабинет на другую базу из браузера.

Переезд проверяем на SQLite: порядок «проверить кандидата → переехать → при
неудаче остаться на прежней базе» тот же, что с PostgreSQL, а PostgreSQL тестам
не нужен. Движок у процесса один, поэтому каждый тест обязан вернуть кабинет на
прежнюю базу — иначе следующий тест поедет уже на чужой.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from app import db as db_module
from app.db import SessionLocal, current_url
from app.models import AppSetting, Warehouse
from app.services import dbconfig, job_lock, lifecycle


@pytest.fixture()
def original_url() -> str:
    db_module.init_db()
    return current_url()


@pytest.fixture()
def restore_engine():
    """База и указатель на неё должны остаться ровно теми, что были до теста."""
    db_module.init_db()        # таблицы создаёт lifespan TestClient — не полагаемся на порядок
    original = current_url()
    yield
    if current_url() != original:
        db_module.rebuild_engine(original)
    with SessionLocal() as db:
        for rec in db.scalars(select(AppSetting).where(AppSetting.key.like("db.%"))).all():
            db.delete(rec)
        db.commit()


def sqlite_candidate(path) -> dict:
    return {"dialect": "sqlite", "host": "", "port": 5432, "user": "", "password": "",
            "database": str(path)}


def new_warehouses():
    """Только наш склад: тесты делят одну базу, считать всё подряд нельзя."""
    return select(func.count(Warehouse.id)).where(Warehouse.code == "NEW-DB")


def dead_candidate(password: str = "super-secret-pw") -> dict:
    """Порт 1 на localhost: отказ мгновенный, без ожидания таймаута соединения."""
    return {"dialect": "postgresql", "host": "127.0.0.1", "port": 1, "user": "statcab",
            "password": password, "database": "statcab"}


# ------------------------------------------------------------------ сборка URL ----

def test_parol_v_url_kodiruetsya():
    """Пароль с `@`, `:` и `%` не имеет права разламывать URL на чужой хост."""
    url = dbconfig.build_url({"dialect": "postgresql", "host": "db.internal", "port": 6543,
                              "user": "stat cab", "password": "p@ss:100%/x",
                              "database": "statcab"})
    assert url.startswith("postgresql+psycopg2://")
    assert "stat%20cab" in url
    assert "p%40ss%3A100%25%2Fx" in url
    shown = dbconfig.masked({"dialect": "postgresql", "host": "db.internal", "user": "u",
                             "password": "secret", "database": "d"})
    assert shown.endswith(":***@db.internal:5432/d")


def test_puti_sqlite_i_pustye_parametry():
    assert dbconfig.build_url({"dialect": "sqlite", "database": "D:/tmp/x.db"}) == "sqlite:///D:/tmp/x.db"
    assert dbconfig.build_url({"dialect": "sqlite", "database": "D:\\tmp\\x.db"}) == "sqlite:///D:/tmp/x.db"
    bad = [{"dialect": "sqlite", "database": ""},
           {"dialect": "mysql", "database": "d", "host": "h", "user": "u"},
           {"dialect": "postgresql", "host": "", "user": "u", "database": "d"},
           {"dialect": "postgresql", "host": "h", "user": "", "database": "d"},
           {"dialect": "postgresql", "host": "h", "user": "u", "database": ""},
           {"dialect": "postgresql", "host": "h", "user": "u", "database": "d", "port": 0}]
    for candidate in bad:
        with pytest.raises(ValueError):
            dbconfig.build_url(candidate)


# ------------------------------------------------------------------- проверка ----

def test_proverka_otkazyvaet_no_ne_slivaet_parol():
    result = dbconfig.test(dead_candidate())
    assert result["ok"] is False
    assert result["error"]
    assert "super-secret-pw" not in result["error"]
    assert "super-secret-pw" not in result["url"]


def test_proverka_zhivoy_sqlite_bazy_hodit(tmp_path):
    result = dbconfig.test(sqlite_candidate(tmp_path / "candidate.db"))
    assert result["ok"] is True, result["error"]
    assert result["version"] and result["version"] != "—"


# -------------------------------------------------------------------- переезд ----

def test_periezd_na_novuyu_bazu_i_vozvrat_k_env(tmp_path, restore_engine):
    before = current_url()
    target = tmp_path / "second.db"
    with SessionLocal() as db:
        # В общей тестовой базе лежат склады чужих тестов, поэтому сравниваем
        # не абсолютное число, а «столько же, сколько было до поездки».
        own = db.scalar(select(func.count(Warehouse.id)))

    with SessionLocal() as db:
        out = dbconfig.apply(db, sqlite_candidate(target))
    assert out["source"] == dbconfig.SOURCE_SETTINGS
    assert "second.db" in current_url().replace("\\", "/")

    # Новая база рабочая: схема на ней создана, с неё и пишут, и читают.
    with SessionLocal() as db:
        assert db.execute(text("SELECT 1")).scalar() == 1
        assert db.scalar(select(func.count(Warehouse.id))) == 0
        db.add(Warehouse(code="NEW-DB", name="Склад новой базы"))
        db.commit()
    with SessionLocal() as db:
        assert db.scalar(new_warehouses()) == 1
        assert dbconfig.source(db) == dbconfig.SOURCE_SETTINGS

    with SessionLocal() as db:
        back = dbconfig.revert(db)
    assert back["source"] == dbconfig.SOURCE_ENV
    assert current_url() == before
    with SessionLocal() as db:
        assert dbconfig.source(db) == dbconfig.SOURCE_ENV
        # Прежняя база не пострадала от поездок: ни чужого склада, ни потери своих.
        assert db.scalar(new_warehouses()) == 0
        assert db.scalar(select(func.count(Warehouse.id))) == own


def test_neudachnyy_periezd_ne_rushit_bazu(restore_engine, original_url, tmp_path):
    """Файл в несуществующем каталоге: база «не отвечает» — указатель не двигается."""
    with SessionLocal() as db:
        with pytest.raises(db_module.EngineSwapError):
            dbconfig.apply(db, sqlite_candidate(tmp_path / "nope" / "x.db"))
    assert current_url() == original_url
    with SessionLocal() as db:
        assert db.execute(text("SELECT 1")).scalar() == 1
        assert dbconfig.source(db) == dbconfig.SOURCE_ENV


def test_bootstrap_vezet_na_bazu_iz_nastroek(tmp_path, restore_engine, original_url):
    """После старта кабинет продолжает путь на базе, которую указал админ."""
    target = tmp_path / "bootstrap.db"
    with SessionLocal() as db:
        dbconfig.apply(db, sqlite_candidate(target))

    # Имитация рестарта: процесс снова смотрит в bootstrap-базу и по её
    # указателю уезжает на указанную админом.
    db_module.rebuild_engine(original_url)
    with SessionLocal() as db:
        moved = dbconfig.bootstrap(db)
    assert moved and not moved.get("error")
    assert "bootstrap.db" in current_url().replace("\\", "/")

    with SessionLocal() as db:
        dbconfig.revert(db)
    assert current_url() == original_url


# ------------------------------------------------------------------------ API ----

def test_api_bazy_trebuet_admina(client, manager_hdr):
    payload = sqlite_candidate("x.db")
    assert client.get("/api/admin/db", headers=manager_hdr).status_code == 403
    assert client.post("/api/admin/db/test", json=payload, headers=manager_hdr).status_code == 403
    assert client.put("/api/admin/db", json=payload, headers=manager_hdr).status_code == 403
    assert client.delete("/api/admin/db", headers=manager_hdr).status_code == 403
    assert client.post("/api/admin/service/restart", headers=manager_hdr).status_code == 403


def test_get_ne_otdaet_parol(client, admin_hdr, restore_engine):
    body = client.get("/api/admin/db", headers=admin_hdr).json()
    assert body["source"] in (dbconfig.SOURCE_ENV, dbconfig.SOURCE_SETTINGS)
    # Пароль не едет наружу даже пустой строкой: только флаг «задан».
    assert "password" not in body["parts"]
    assert "password" not in body["env_parts"]
    assert body["password_set"] is False
    assert body["url"] and body["env_url"] and body["server_version"]


def test_proverka_cherez_api_zapishet_audit(client, admin_hdr):
    r = client.post("/api/admin/db/test", json=dead_candidate(), headers=admin_hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and body["error"]
    audit = client.get("/api/admin/audit?limit=10", headers=admin_hdr).json()
    assert any(a["action"] == "admin.db_test" and "отказ" in (a["detail"] or "") for a in audit)


def test_otkaz_apply_ostavlyaet_kabinet_zhivym(client, admin_hdr, restore_engine, tmp_path):
    before = client.get("/api/admin/db", headers=admin_hdr).json()["url"]
    r = client.put("/api/admin/db", json=sqlite_candidate(tmp_path / "nope" / "x.db"),
                   headers=admin_hdr)
    assert r.status_code == 400, r.text
    assert "не отвечает" in r.json()["detail"]
    assert client.get("/api/admin/db", headers=admin_hdr).json()["url"] == before
    assert client.get("/api/meta/freshness", headers=admin_hdr).status_code == 200
    audit = client.get("/api/admin/audit?limit=10", headers=admin_hdr).json()
    assert any(a["action"] == "admin.db_apply" and "отказ" in (a["detail"] or "") for a in audit)


def test_periezd_cherez_api_otklyuchaet_tekushchiy_vhod(client, admin_hdr, tmp_path,
                                                        restore_engine, original_url):
    """Смена базы отключает текущие сессии: пользователей читает новая база."""
    r = client.put("/api/admin/db", json=sqlite_candidate(tmp_path / "api.db"),
                   headers=admin_hdr)
    assert r.status_code == 200, r.text
    assert r.json()["source"] == dbconfig.SOURCE_SETTINGS
    assert "api.db" in r.json()["url"].replace("\\", "/")

    # Токен выдан прежней базой — на новой такого пользователя нет.
    assert client.get("/api/admin/db", headers=admin_hdr).status_code in (401, 403)

    db_module.rebuild_engine(original_url)
    assert client.get("/api/admin/db", headers=admin_hdr).status_code == 200


def test_restart_zavershaet_process_i_pishet_audit(client, admin_hdr, monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(lifecycle, "schedule_exit", lambda after_sec=0.6: calls.append(after_sec))

    r = client.post("/api/admin/service/restart", headers=admin_hdr)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "restarting"
    assert calls, "процессу не поставлена задача завершиться"

    audit = client.get("/api/admin/audit?limit=10", headers=admin_hdr).json()
    assert any(a["action"] == "admin.service_restart" for a in audit)


def test_restart_ne_rvet_zagruzku(client, admin_hdr):
    """Пока витрину пишут, перезапуск нельзя: супервизор поднимет кабинет на полуслове."""
    with SessionLocal() as lock_db:
        assert job_lock.acquire(lock_db, job_lock.VITRINA, note="тест перезапуска")
        try:
            r = client.post("/api/admin/service/restart", headers=admin_hdr)
            assert r.status_code == 409, r.text
        finally:
            job_lock.release(lock_db, job_lock.VITRINA)


# ------------------------------------------------------- создание базы (п. 35) ----

def pg_candidate(**fields) -> dict:
    """Кандидат PostgreSQL; по умолчанию — заведомо мёртвый порт, чтобы тест не ждал."""
    base = {"dialect": "postgresql", "host": "127.0.0.1", "port": 1, "user": "statcab",
            "password": "pw", "database": "kabinet"}
    return base | fields


def test_quote_ident_ne_otpuskaet_kavychku():
    """`CREATE DATABASE` не принимает параметров: экранирование — единственная защита."""
    assert dbconfig.quote_ident("statcab") == '"statcab"'
    assert dbconfig.quote_ident('we"ird;drop database "x') == '"we""ird;drop database ""x"'
    for bad in ("", "a" * 64, "имя\0с"):
        with pytest.raises(ValueError):
            dbconfig.quote_ident(bad)


def test_sluzhebnaya_baza_menyaet_tolko_imya_bazy():
    """Создавать базу ходим в `postgres` того же кластера той же ролью."""
    cand = {"dialect": "postgresql", "host": "db.internal", "port": 6543, "user": "statcab",
            "password": "p@ss:100%", "database": "kabinet"}
    url = make_url(dbconfig.maintenance_url(cand))
    assert (url.database, url.host, url.port, url.username, url.password) == (
        "postgres", "db.internal", 6543, "statcab", "p@ss:100%")
    with pytest.raises(ValueError):
        dbconfig.maintenance_url(sqlite_candidate("x.db"))


def test_est_baza_ili_net(tmp_path):
    there = tmp_path / "there.db"
    there.write_bytes(b"")
    assert dbconfig.database_exists(sqlite_candidate(there)) == (True, None)
    assert dbconfig.database_exists(sqlite_candidate(tmp_path / "нет.db")) == (False, None)


def test_mertvyysya_server_ne_vydaet_sebya_za_otsutstvie_bazy():
    result = dbconfig.test(pg_candidate())
    assert result["ok"] is False
    assert result["exists"] is None, "отказ соединения — не «базы нет»: советовать создание нельзя"
    assert "pw" not in (result["error"] or "")


def test_sqlite_ne_vymyslyaet_sozdaniya_bazy(tmp_path):
    outcome = dbconfig.create_database(sqlite_candidate(tmp_path / "driver.db"))
    assert outcome["created"] is False and outcome["error"] is None


def test_flag_create_missing_ne_osedaet_v_stor(tmp_path, restore_engine, original_url):
    """Разовая просьба не должна становиться постоянным настроечным состоянием."""
    candidate = sqlite_candidate(tmp_path / "store.db") | {"create_missing": True}
    with SessionLocal() as db:
        assert dbconfig.apply(db, candidate)["created"] is False
    db_module.rebuild_engine(original_url)          # возвращаемся к базе с указателем
    with SessionLocal() as db:
        assert dbconfig.pointer(db) is True, "указатель на новую базу обязан сохраниться"
        # ключ не зарегистрирован в сторе — но и строкой в таблицу он осесть не должен
        stored = db.scalars(select(AppSetting).where(AppSetting.key.like("%create_missing%"))).all()
        assert stored == [], f"разовый флаг уехал в настройки: {[r.key for r in stored]}"


def test_sozdat_bazu_na_mertvom_servere_otkaz_do_periezda(client, admin_hdr, restore_engine):
    """Не удалось создать — кабинет не сдвинулся, пароль не показан, текст внятный."""
    before = client.get("/api/admin/db", headers=admin_hdr).json()["url"]
    r = client.put("/api/admin/db", json=dead_candidate() | {"create_missing": True},
                   headers=admin_hdr)
    assert r.status_code == 422, r.text
    assert "super-secret-pw" not in r.text
    assert client.get("/api/admin/db", headers=admin_hdr).json()["url"] == before
