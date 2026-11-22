"""Проба готовности /readyz (п. 19 TASKS2.md).

Проверяем поведение: код ответа, состав тела и состояние БД. Журнал загрузок
на время теста очищаем (фиксация строк до/после): он один на весь прогон, и
другие тесты считают витрину свежей. Настройки пишём прямо в стор — эндпоинт
читает тот же стор, что и админка.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import SyncRun, utcnow
from app.services import app_settings as store
from app.services import scheduling

READY = "/readyz"
READY_API = "/api/readyz"


@pytest.fixture()
def clear_runs():
    """Пустой журнал загрузок на время теста; после — точная копия как было."""
    db = SessionLocal()
    try:
        cols = [c.key for c in SyncRun.__table__.columns]
        before = [{c: getattr(r, c) for c in cols} for r in db.scalars(select(SyncRun))]
        for r in list(db.scalars(select(SyncRun))):
            db.delete(r)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        # сначала убрать строки, добавленные тестом: без этого SQLite вторично
        # выдаёт освобождённые id и восстановление упирается в UNIQUE
        for r in list(db.scalars(select(SyncRun))):
            db.delete(r)
        db.flush()
        for row in before:
            db.add(SyncRun(**row))
        db.commit()
    finally:
        db.close()


def _seed_run(status: str = "ok", age_sec: int = 0) -> None:
    """Загрузка в журнал с возрастом `age_sec` секунд от текущего момента."""
    moment = utcnow() - timedelta(seconds=age_sec)
    db = SessionLocal()
    try:
        db.add(SyncRun(source="mock", status=status, rows=1,
                       started_at=moment,
                       finished_at=moment if status == "ok" else None))
        db.commit()
    finally:
        db.close()


def _keys_deep(obj, acc: list[str]) -> list[str]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            acc.append(str(key))
            _keys_deep(value, acc)
    elif isinstance(obj, list):
        for item in obj:
            _keys_deep(item, acc)
    return acc


# --------------------------------------------------------------------- 200 ----

def test_readyz_ok_s_fresh_load(client, clear_runs):
    _seed_run(age_sec=5)
    r = client.get(READY)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert isinstance(body["seconds_since_sync"], int)
    assert 0 <= body["seconds_since_sync"] < 120
    assert body["sync_age_limit_sec"] == store.DEFAULTS["sync.age_limit_sec"]
    assert body["sync_age_limit_sec"] == 5400
    # токен не задан — доступ открыт, но мониторинг видит, что защиты нет
    assert body["token_required"] is False
    sched = body["scheduler"]
    assert isinstance(sched["running"], bool)
    assert isinstance(sched["jobs"], list)
    for job in sched["jobs"]:
        assert set(job) == {"id", "next"}


def test_readyz_odin_obrabotchik_dva_puti(client, clear_runs):
    """nginx проксирует только /api, докер-проба ходит напрямую — тело одинаковое."""
    _seed_run(age_sec=5)
    direct = client.get(READY)
    proxied = client.get(READY_API)
    assert direct.status_code == proxied.status_code == 200
    assert direct.json() == proxied.json()


def test_readyz_vyklyuchennyy_planirovshchik_ne_503(client, clear_runs, monkeypatch):
    """Кабинет с выключенным планировщиком работает — просто не грузит сам."""
    _seed_run(age_sec=5)
    monkeypatch.setattr(scheduling, "state",
                        lambda scheduler: {"running": False, "jobs": []})
    r = client.get(READY)
    assert r.status_code == 200, r.text
    assert r.json()["scheduler"] == {"running": False, "jobs": []}


def test_readyz_pokazyvaet_sostoyanie_planirovshchika(client, clear_runs, monkeypatch):
    _seed_run(age_sec=5)
    fake = {"running": True, "jobs": [{"id": "onec_sync", "next": "2026-11-20T12:00:00"}]}
    monkeypatch.setattr(scheduling, "state", lambda scheduler: fake)
    body = client.get(READY).json()
    assert body["scheduler"] == fake


# --------------------------------------------------------------------- 503 ----

def test_readyz_ustarevshaya_vitrina_503(client, clear_runs):
    _seed_run(age_sec=5401)          # на секунду старше порога по умолчанию
    r = client.get(READY)
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "unavailable"
    assert body["seconds_since_sync"] >= 5401
    assert body["sync_age_limit_sec"] == 5400
    assert isinstance(body["reason"], str) and body["reason"]
    # 503 — то же тело плюс человекочитаемая причина: у мониторинга одна схема
    assert set(body) == {"status", "db", "seconds_since_sync", "sync_age_limit_sec",
                         "scheduler", "token_required", "reason"}


def test_readyz_porog_nastroaivaemyy(client, clear_runs, settings_snapshot):
    """Порог берётся из стора: с 86400 с загрузка двухчасовой давности — снова 200."""
    _seed_run(age_sec=7200)
    assert client.get(READY).status_code == 503
    db = SessionLocal()
    try:
        store.apply(db, {"sync.age_limit_sec": 86400})
    finally:
        db.close()
    r = client.get(READY)
    assert r.status_code == 200, r.text
    assert r.json()["sync_age_limit_sec"] == 86400


def test_readyz_bez_uspeshnyh_zagruzok_503(client, clear_runs):
    """Ни одной успешной загрузки — кабинет без витрины, людям цифры отдавать нечего."""
    r = client.get(READY)
    assert r.status_code == 503
    body = r.json()
    assert body["seconds_since_sync"] is None
    assert body["reason"]

    # ошибки в журнале есть, а успешных нет — всё ещё не готов
    _seed_run(status="error")
    r = client.get(READY)
    assert r.status_code == 503
    assert r.json()["seconds_since_sync"] is None


def test_readyz_mertvaya_baza_503(client, monkeypatch):
    """Мёртвая база — 503 с телом, а не 500 из зависимости сессии."""
    def boom():
        raise RuntimeError("conn refused")

    monkeypatch.setattr("app.main.SessionLocal", boom)
    r = client.get(READY)
    assert r.status_code == 503
    body = r.json()
    assert body["db"] == "error"
    assert "баз" in body["reason"].lower()
    # текст исключения наружу не уезжает
    assert "conn refused" not in r.text


# ------------------------------------------------------------------- токен ----

def test_readyz_token(client, clear_runs, settings_snapshot):
    _seed_run(age_sec=5)
    db = SessionLocal()
    try:
        store.apply(db, {"service.readyz_token": "tok-777"})
    finally:
        db.close()

    assert client.get(READY).status_code == 403, "без токена вход закрыт"
    assert client.get(f"{READY}?token=tok-77").status_code == 403, "префикс — не токен"
    assert client.get(f"{READY}?token=nesuschestvuet").status_code == 403

    r = client.get(f"{READY}?token=tok-777")
    assert r.status_code == 200, r.text
    assert r.json()["token_required"] is True
    # и под /api тот же контроль доступа
    assert client.get(f"{READY_API}?token=tok-777").status_code == 200
    assert client.get(READY_API).status_code == 403


def test_readyz_token_env_ravnotsen_store(client, clear_runs, settings_snapshot, monkeypatch):
    """D3 (п. 19): токен принимается из стора ИЛИ из READY_TOKEN. Оператор мог
    завести токен только в секретах (env) — проба закрыта, но проходит со
    значением из env; админ потом завёл своё значение в UI — валидны оба,
    контейнер с env-токеном не краснеет."""
    _seed_run(age_sec=5)
    monkeypatch.setenv("READY_TOKEN", "env-tok")
    # стор пуст, env задан — наружу проба закрыта, env-токен проходит
    assert client.get(READY).status_code == 403
    assert client.get(f"{READY}?token=env-tok").status_code == 200
    db = SessionLocal()
    try:
        store.apply(db, {"service.readyz_token": "ui-tok"})
    finally:
        db.close()
    # в сторе своё значение — оба токена равноценны
    assert client.get(f"{READY}?token=ui-tok").status_code == 200
    assert client.get(f"{READY}?token=env-tok").status_code == 200, \
        "env-токен отвергнут: админ сменил токен в UI, и контейнер покраснеет (D3)"
    assert client.get(f"{READY}?token=nope").status_code == 403


def test_readyz_token_special_chars(client, clear_runs, settings_snapshot):
    """D2: healthcheck процент-экранирует токен перед отправкой (quote(t, safe='')
    в backend/Dockerfile). Эндпоинт обязан принять раскодированное значение как
    есть — иначе токен с пробелом или «&» не проходит и проба мёртвая."""
    from urllib.parse import quote
    _seed_run(age_sec=5)
    tricky = "a b&c=d/e+f%g"
    db = SessionLocal()
    try:
        store.apply(db, {"service.readyz_token": tricky})
    finally:
        db.close()
    assert client.get(f"{READY}?token={quote(tricky, safe='')}").status_code == 200


# ------------------------------------------------------------ разведданные ----

def test_readyz_ne_razdaet_razveddane(client, clear_runs):
    """Ни в 200, ни в 503 нет ключей про 1С, секреты, пароли и пути (п. 3 TASKS2.md)."""
    forbidden = ("onec", "secret", "password", "path", "config", "url")

    ok = client.get(READY)          # журнал пуст → 503, проходимся и по нему
    assert ok.status_code == 503
    for key in _keys_deep(ok.json(), []):
        assert not any(bad in key.lower() for bad in forbidden), key

    _seed_run(age_sec=5)
    ok = client.get(READY)
    assert ok.status_code == 200
    for key in _keys_deep(ok.json(), []):
        assert not any(bad in key.lower() for bad in forbidden), key
