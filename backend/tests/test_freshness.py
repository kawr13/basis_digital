"""Свежесть данных: `/api/meta/freshness` и настройка периода автообновления."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import update

from app.db import SessionLocal
from app.models import SyncRun, utcnow


def test_freshness_shape(client, admin_hdr):
    r = client.get("/api/meta/freshness", headers=admin_hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"data_as_of", "last_sync", "stale", "sync_interval_minutes", "server_time"}
    # демо-витрина наполняется при старте — метка среза обязана быть
    assert body["data_as_of"]
    assert body["sync_interval_minutes"] >= 1


def test_freshness_available_for_manager(client, manager_hdr):
    """Менеджер не админ, но возраст данных видеть обязан."""
    assert client.get("/api/meta/freshness", headers=manager_hdr).status_code == 200


def test_freshness_unauthorized(client):
    assert client.get("/api/meta/freshness").status_code == 401


def test_stale_after_long_pause(client, admin_hdr):
    """Если успешный синк был дольше 2.5 интервалов назад — данные помечены устаревшими."""
    # в тестовом окружении интервал поднят до 999 минут, фиксируем свой
    assert client.put("/api/admin/settings", headers=admin_hdr,
                      json={"sync.interval_minutes": 15}).status_code == 200

    run = client.post("/api/admin/sync/run", headers=admin_hdr)
    assert run.status_code == 200, run.text
    assert client.get("/api/meta/freshness", headers=admin_hdr).json()["stale"] is False

    # откатываем ЗАВЕРШЕНИЕ ВСЕХ успешных синков на 3 часа назад: иначе
    # эндпоинт честно возьмёт ближайший свежий из остальных (в тестовой БД
    # их несколько после других тестов)
    session = SessionLocal()
    try:
        back = utcnow() - timedelta(hours=3)
        session.execute(
            update(SyncRun)
            .where(SyncRun.status == "ok", SyncRun.finished_at.is_not(None))
            .values(finished_at=back, started_at=back - timedelta(minutes=1))
        )
        session.commit()
    finally:
        session.close()

    body = client.get("/api/meta/freshness", headers=admin_hdr).json()
    assert body["stale"] is True
    assert body["last_sync"]["status"] == "ok"

    # и снова свежо после ручной загрузки
    client.post("/api/admin/sync/run", headers=admin_hdr)
    assert client.get("/api/meta/freshness", headers=admin_hdr).json()["stale"] is False

    client.post("/api/admin/settings/reset", headers=admin_hdr, json=["sync.interval_minutes"])


def test_refresh_seconds_setting(client, admin_hdr, manager_hdr):
    """Период автообновления доступен фронту через публичный набор и валидируется."""
    pub = client.get("/api/settings/public", headers=manager_hdr)
    assert pub.status_code == 200
    assert pub.json()["ui.refresh_seconds"] == 60

    ok = client.put("/api/admin/settings", headers=admin_hdr, json={"ui.refresh_seconds": 30})
    assert ok.status_code == 200, ok.text
    assert client.get("/api/settings/public", headers=manager_hdr).json()["ui.refresh_seconds"] == 30

    for bad in (3, 5000):
        r = client.put("/api/admin/settings", headers=admin_hdr, json={"ui.refresh_seconds": bad})
        assert r.status_code == 422, r.text

    # возвращаем дефолт, чтобы не влиять на другие тесты
    client.post("/api/admin/settings/reset", headers=admin_hdr, json=["ui.refresh_seconds"])
