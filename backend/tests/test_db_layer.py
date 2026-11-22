"""П. 8, 12–16, 32 TASKS2.md: общая аренда работы, ретention, DDL-гонка, зависшие загрузки, дубли индексов.

Это слой, который не видно ни в одном ответе API: он проверяет, что два процесса
кабинета не пишут витрину одновременно, что база не растёт бесконечно и что старт
второго воркера не роняет первый.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import UniqueConstraint, delete, func, inspect, select, text

from app import db as db_mod
from app.db import SessionLocal
from app.models import AuditLog, JobLock, RefreshToken, SyncRun, User, Warehouse, utcnow
from app.onec.sync import reconcile_hung_runs
from app.services import job_lock, retention

JOB = "test_job"
MARK = "pytest"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Таблицы создаёт lifespan приложения; этот файл работает без HTTP-клиента,
    поэтому поднимает схему сам — иначе не проходит отдельно от остальных."""
    db_mod.init_db()


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def free_lock(db):
    """Аренду держит процесс, а не тест: до и после теста её быть не должно."""
    db.execute(delete(JobLock).where(JobLock.name == JOB))
    db.commit()
    yield
    db.execute(delete(JobLock).where(JobLock.name == JOB))
    db.commit()


@pytest.fixture
def clean_service_rows(db):
    """Служебные записи теста не должны остаться в общей базе прогона."""
    yield
    db.execute(delete(RefreshToken).where(RefreshToken.token_hash.like(f"{MARK}%")))
    db.execute(delete(User).where(User.login == f"{MARK}user"))
    db.execute(delete(AuditLog).where(AuditLog.action.like(f"{MARK}.%")))
    db.execute(delete(SyncRun).where(SyncRun.source == MARK))
    db.commit()


# --- аренда работы ------------------------------------------------------------

def test_arenada_na_kabinet_odna(db, free_lock):
    """Второй пришелец не ждёт очереди: он получает «занято» и уходит."""
    assert job_lock.acquire(db, JOB) is True
    assert job_lock.acquire(db, JOB) is False
    job_lock.release(db, JOB)
    assert job_lock.acquire(db, JOB) is True


def test_prosrochennuyu_arenadu_zabirayut(db, free_lock):
    """Владелец мог умереть вместе с контейнером — кабинет нельзя вешать навсегда."""
    assert job_lock.acquire(db, JOB, ttl_minutes=0) is True
    assert job_lock.acquire(db, JOB, ttl_minutes=0) is True


def test_chuzhuyu_arenadu_release_ne_trogayet(db, free_lock):
    db.add(JobLock(name=JOB, owner="drugoy-process", acquired_at=utcnow()))
    db.commit()
    job_lock.release(db, JOB)
    assert db.scalar(select(JobLock.owner).where(JobLock.name == JOB)) == "drugoy-process"


def test_held_osvobozhdaet_arenadu_posle_raboty(db, free_lock):
    with job_lock.held(db, JOB) as got:
        assert got is True
        assert job_lock.acquire(db, JOB) is False      # пока работаем — занято
    assert job_lock.info(db, JOB) is None


def test_held_snimaet_arenadu_i_pri_oshibke(db, free_lock):
    """Упавшая загрузка не должна блокировать следующие (кроме срока аренды)."""
    with pytest.raises(RuntimeError):
        with job_lock.held(db, JOB):
            raise RuntimeError("загрузка упала")
    assert job_lock.info(db, JOB) is None


def test_info_pokazyvaet_vladelca(db, free_lock):
    job_lock.acquire(db, JOB, note="pull-синхронизация 1С")
    info = job_lock.info(db, JOB)
    assert info["owner"] == job_lock.owner() and info["note"] == "pull-синхронизация 1С"


# --- зависшие загрузки --------------------------------------------------------

def test_zavisshie_zagruzki_zakryvayutsya_pri_starte(db, clean_service_rows):
    now = utcnow()
    live = SyncRun(source=MARK, status="ok", started_at=now, finished_at=now, rows=5)
    hung = SyncRun(source=MARK, status="running", started_at=now - timedelta(hours=2))
    db.add_all([live, hung])
    db.commit()
    ids = {"live": live.id, "hung": hung.id}

    assert reconcile_hung_runs(db) == 1

    assert db.scalar(select(SyncRun.status).where(SyncRun.id == ids["hung"])) == "error"
    closed = db.get(SyncRun, ids["hung"])
    assert closed.finished_at is not None and "остановкой кабинета" in closed.error
    # успешную строку трогать нельзя: по ней дашборд считает свежесть витрины
    assert db.scalar(select(SyncRun.status).where(SyncRun.id == ids["live"])) == "ok"


# --- ретention ----------------------------------------------------------------

def test_retention_rezhet_staroe_i_ne_trogayet_svezhee(db, clean_service_rows):
    now = utcnow()
    old_log = AuditLog(created_at=now - timedelta(days=800), action=f"{MARK}.old")
    fresh_log = AuditLog(created_at=now, action=f"{MARK}.fresh")
    old_run = SyncRun(source=MARK, status="ok", started_at=now - timedelta(days=400),
                      finished_at=now - timedelta(days=400))
    hung_run = SyncRun(source=MARK, status="running", started_at=now - timedelta(days=400))
    db.add_all([old_log, fresh_log, old_run, hung_run])
    db.commit()
    ids = {"old_run": old_run.id, "hung_run": hung_run.id}

    counts = retention.purge(db)

    assert counts["audit"] >= 1 and counts["runs"] >= 1
    assert _count(db, AuditLog, AuditLog.action, f"{MARK}.old") == 0
    assert _count(db, AuditLog, AuditLog.action, f"{MARK}.fresh") == 1
    assert _count_by_id(db, SyncRun, ids["old_run"]) == 0
    # «running» — это обрыв, а не старость: его закрывает reconcile, а не срок
    assert _count_by_id(db, SyncRun, ids["hung_run"]) == 1


def test_retention_rezaet_protuhshie_sessii(db, clean_service_rows):
    # RefreshToken висит на users.id — заводим своего человека, чтобы файл проходил
    # и без lifespan-а, который заводит администратора.
    now = utcnow()
    owner = User(login=f"{MARK}user", full_name="Тест ретentionа", password_hash="x")
    db.add(owner)
    db.commit()
    admin_id = owner.id
    expired = RefreshToken(user_id=admin_id, token_hash=f"{MARK}expired", family_id="f-1",
                           issued_at=now - timedelta(days=100), expires_at=now - timedelta(days=90))
    revoked = RefreshToken(user_id=admin_id, token_hash=f"{MARK}revoked", family_id="f-2",
                           issued_at=now - timedelta(days=40), expires_at=now + timedelta(days=1),
                           revoked_at=now - timedelta(days=35))
    live = RefreshToken(user_id=admin_id, token_hash=f"{MARK}live", family_id="f-3",
                        issued_at=now, expires_at=now + timedelta(days=1))
    db.add_all([expired, revoked, live])
    db.commit()

    counts = retention.purge(db)

    assert counts["sessions"] >= 2
    assert _count(db, RefreshToken, RefreshToken.token_hash, f"{MARK}expired") == 0
    assert _count(db, RefreshToken, RefreshToken.token_hash, f"{MARK}revoked") == 0
    assert _count(db, RefreshToken, RefreshToken.token_hash, f"{MARK}live") == 1


# --- DDL-гонка ----------------------------------------------------------------

def test_gonku_alter_proglotyvayut(monkeypatch):
    """Колонку мог добавить соседний воркер, пока мы смотрели в inspect."""
    table, column, _ddl = db_mod._ADDED_COLUMNS[0]
    real_inspect = db_mod.inspect

    class _BlindInspector:
        """Картина «проверили и не нашли», хотя колонка уже есть."""

        def __getattr__(self, name):
            return getattr(real_inspect(db_mod.engine), name)

        def get_columns(self, _table):
            return []

    monkeypatch.setattr(db_mod, "inspect", lambda _engine: _BlindInspector())
    db_mod._ensure_columns()

    assert column in {c["name"] for c in real_inspect(db_mod.engine).get_columns(table)}


def test_nastoyashchuyu_oshibku_ddl_ne_proglotyvayut(monkeypatch):
    """Проглочен должен быть именно «уже существует», а не любой отказ базы."""
    monkeypatch.setattr(db_mod, "_ADDED_COLUMNS", [("users", "order", "INTEGER")])
    with pytest.raises(Exception):                       # noqa: BLE001 — любой DBAPIError
        db_mod._ensure_columns()


# --- дубли индексов (п. 32) ----------------------------------------------------

# Живая база переживает чистку моделей — старые индексы в ней никто не отменял.
# Эмулируем «старую базу» репрезентативным набором: дубль под уникальным
# ограничением, дубль под первым столбцом составного индекса и дубль на колонке
# внешнего ключа.
_STALE = (
    ("ix_fact_warehouse_capacity_snapshot_at", "fact_warehouse_capacity", "snapshot_at"),
    ("ix_fact_rail_operation_report_date", "fact_rail_operation", "report_date"),
    ("ix_user_warehouse_scopes_user_id", "user_warehouse_scopes", "user_id"),
    ("ix_vessel_placements_berth_id", "vessel_placements", "berth_id"),
)


def _index_names(table: str) -> set[str]:
    return {ix["name"] for ix in inspect(db_mod.engine).get_indexes(table)}


def test_init_db_snosit_dubli_iz_staroy_bazy():
    """Модели почистили, а в живой базе индексы-дубли доживали до ручного DROP:
    init_db обязан сносить их сам."""
    with db_mod.engine.begin() as conn:
        for name, table, column in _STALE:
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"))
    db_mod.init_db()
    for name, table in db_mod._OBSOLETE_INDEXES:
        assert name not in _index_names(table), name


def test_pokryvayushchie_indeksy_i_unikalnost_na_meste():
    """Сносим дубли, а не опору: составные индексы и уникальные ограничения на
    месте — на них держатся выборки, уникальность срезов и каскад внешних ключей."""
    assert "ix_ro_date" in _index_names("fact_rail_operation")
    assert "ix_vo_date" in _index_names("fact_vessel_operation")
    assert "ix_placement_berth" in _index_names("vessel_placements")
    insp = inspect(db_mod.engine)
    for table, name in (
        ("fact_cargo_balance", "uq_cb_snap"),
        ("fact_warehouse_capacity", "uq_wc_snap"),
        ("fact_processing_dynamics", "uq_pd_day"),
        ("fact_dispatcher_shift", "uq_ds_shift"),
    ):
        assert name in {c["name"] for c in insp.get_unique_constraints(table)}


def test_povtornyy_init_db_bezopasen_a_dannye_na_meste(db):
    """Чистка идёт на каждом старте: повторные вызовы должны проходить молча,
    а строки витрины удаление индексов не затрагивает вовсе."""
    code = f"{MARK}idx"
    db.add(Warehouse(code=code, name=f"{MARK} дедупликация"))
    db.commit()
    try:
        db_mod.init_db()
        db_mod.init_db()
        db.expire_all()
        assert _count(db, Warehouse, Warehouse.code, code) == 1
        assert "ix_ro_date" in _index_names("fact_rail_operation")
    finally:
        db.execute(delete(Warehouse).where(Warehouse.code == code))
        db.commit()


def test_v_modelyah_net_dublikatov_indeksov():
    """Страховка от возврата дублей: не-уникальный индекс не имеет права
    повторять начало другого индекса или уникального ограничения той же таблицы.
    Уникальные объекты не проверяем: они сами держат уникальность и дублями не
    считаются (п. 32 TASKS2.md)."""
    for table in db_mod.Base.metadata.sorted_tables:
        entries = [
            (tuple(c.name for c in ix.columns), ix.name or "?", False)
            for ix in table.indexes
        ]
        entries += [
            (tuple(c.name for c in cons.columns), cons.name or "unique", True)
            for cons in table.constraints
            if isinstance(cons, UniqueConstraint)
        ]
        for cols, label, unique in entries:
            if unique:
                continue
            for ocols, olabel, _ in entries:
                if olabel == label:
                    continue
                if len(cols) <= len(ocols) and ocols[: len(cols)] == cols:
                    pytest.fail(
                        f"{table.name}: индекс {label} {cols} "
                        f"покрыт {olabel} {ocols}"
                    )


# ------------------------------------------------------------------ помощники --

def _count(db, model, column, value) -> int:
    return db.scalar(select(func.count()).select_from(model).where(column == value)) or 0


def _count_by_id(db, model, row_id) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.id == row_id)) or 0
