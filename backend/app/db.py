from __future__ import annotations

import contextlib
import hashlib
import logging
import os
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

log = logging.getLogger("statcab.db")


class Base(DeclarativeBase):
    pass


# SQLite по умолчанию: журнал DELETE (читатель ждёт писателя), внешние ключи
# выключены, а «database is locked» отдаётся сразу, не подождав. Лечится PRAGMA на
# каждое соединение — пул отдаёт коннект уже настроенным (п. 12 TASKS2.md).
# foreign_keys=ON заодно сравнивает dev с боём: PostgreSQL внешние ключи соблюдает
# всегда, а в SQLite ON DELETE SET NULL без этого флага молча оставлял сирот.
_SQLITE_PRAGMAS: tuple[tuple[str, str], ...] = (
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),
    ("foreign_keys", "ON"),
    ("busy_timeout", "5000"),
)


def _apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        for name, value in _SQLITE_PRAGMAS:
            cursor.execute(f"PRAGMA {name}={value}")
    finally:
        cursor.close()


def _engine_for(url: str, *, create_dirs: bool = True):
    """Собрать движок под конкретный URL: пул и PRAGMA зависят от диалекта.

    `create_dirs` — только для старта: при переезде молча создать каталог
    нельзя, иначе опечатка в пути превращается в «кабинет переехал на пустую
    базу», и никто не заметит до конца месяца.
    """
    s = get_settings()
    if url.startswith("sqlite"):
        raw = url.split("///", 1)[-1]
        if create_dirs and raw and raw != ":memory:":
            Path(raw).parent.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False}
        # SQLite не принимает параметры пула: у него своя политика соединений,
        # pool_size для него — TypeError на старте кабинета.
        pool_kwargs: dict = {}
    else:
        connect_args = {}
        # Дефолтный пул (5 + 10 overflow на процесс) упирается в потолок на первой
        # же параллельной загрузке дашбордов, поэтому размеры — из настроек.
        # Сколько их ставить и как не превысить max_connections — docs/06, §9.
        pool_kwargs = {"pool_size": s.db_pool_size, "max_overflow": s.db_max_overflow,
                       "pool_recycle": s.db_pool_recycle_sec}
    eng = create_engine(url, connect_args=connect_args, pool_pre_ping=True, future=True,
                        **pool_kwargs)
    if url.startswith("sqlite"):
        event.listen(eng, "connect", _apply_sqlite_pragmas)
    return eng


def _build_engine():
    return _engine_for(get_settings().database_url)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


# ------------------------------------------------------------- переезд базы ----


class EngineSwapError(RuntimeError):
    """Новая база не приняла кабинет; прежний движок остался на месте."""


def current_url() -> str:
    """URL, на котором кабинет работает сейчас. С паролем — только внутрь кода."""
    return engine.url.render_as_string(hide_password=False)


def current_engine_url():
    """Объект URL работающей базы: его удобно сравнивать, не трогая пароль."""
    return engine.url


def masked_url() -> str:
    """Тот же URL для людей: пароль заменён на точки."""
    return engine.url.render_as_string()


def realm() -> str:
    """Отпечаток работающей базы; попадает в токен доступа (см. `security`).

    Секрет подписи один, а пользователей знает конкретная база. После переезда
    (п. 27 TASKS2.md) прежний токен обязан перестать действовать: под тем же `sub`
    на новой базе может лежать совсем другой человек — вплоть до администратора.
    """
    raw = engine.url.render_as_string(hide_password=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def server_version() -> str:
    """Версия сервера базы — чтобы в админке было видно, куда именно переехали."""
    info = engine.dialect.server_version_info
    if isinstance(info, tuple):
        return ".".join(str(part) for part in info)
    return str(info or "—")


def _reason(exc: BaseException, secret: str = "") -> str:
    """Человеческое объяснение отказа; пароль из текста ошибки вырезаем.

    SQLAlchemy щедро печатает URL соединения в тексте — без замены пароль
    уехал бы в аудит и в ответ админке.
    """
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    for variant in {secret, quote(secret, safe="")} - {""}:
        text = text.replace(variant, "***")
    return text[:300]


def rebuild_engine(url, *, secret: str = "") -> None:
    """Переехать на новую базу без рестарта процесса (п. 27 TASKS2.md).

    Порядок жёсткий: сначала кандидат держит `SELECT 1`, только потом мы его
    ставим. Неудача — прежний движок цел и кабинет продолжает работать на
    старой базе: админ имеет право ошибиться в порте, а вот остаться без
    кабинета из-за опечатки порт не имеет права.

    Цена переезда: соединения прежней базы закрываются, поэтому начатые в эту
    секунду запросы падают. Об этом админка предупреждает явно.
    """
    global engine
    previous = engine
    try:
        target = url if isinstance(url, str) else url.render_as_string(hide_password=False)
        candidate = _engine_for(target, create_dirs=False)
    except Exception as exc:  # noqa: BLE001 — URL мог быть любым
        raise EngineSwapError(f"Конфигурация базы не собралась: {_reason(exc, secret)}") from exc

    try:
        with candidate.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — драйверы говорят по-разному
        candidate.dispose()
        raise EngineSwapError(f"Новая база не отвечает: {_reason(exc, secret)}") from exc

    engine = candidate
    # Сессионную фабрику НЕ пересоздаём: на неё уже сослались по имени
    # (`from ..db import SessionLocal`) планировщик, эндпоинты и тесты.
    # configure() меняет привязку у того же объекта — и все новые сессии
    # уходят на новую базу, а начатые доработают на прежней.
    SessionLocal.configure(bind=candidate)
    try:
        init_db()
    except Exception as exc:  # noqa: BLE001 — прав на DDL может не хватить
        candidate.dispose()
        engine = previous
        SessionLocal.configure(bind=previous)
        raise EngineSwapError(
            f"На новой базе не удалось подготовить схему ({_reason(exc, secret)}); "
            "кабинет остался на прежней базе") from exc

    # Прежний пул отпускаем в последнюю очередь: пока не убедились, что новая
    # база жива и схема на месте, отменять нечего.
    previous.dispose()
    log.info("Кабинет переехал на базу %s", masked_url())


def get_db():
    """FastAPI dependency: сессия БД на запрос."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# create_all создаёт недостающие таблицы, но не добавляет колонки в существующие.
# Перечисляем такие колонки явно: это дешевле и предсказуемее, чем тянуть Alembic
# ради одной графы. DDL одинаково проходит и на SQLite, и на PostgreSQL.
_ADDED_COLUMNS = [
    ("users", "vessel_right", "VARCHAR(16)"),
    ("users", "sections", "VARCHAR(128)"),
    ("vessel_placements", "length_m", "INTEGER"),
    ("vessel_placements", "offset_m", "INTEGER NOT NULL DEFAULT 0"),
    ("vessel_placements", "at_other_terminal", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("vessel_placements", "color", "VARCHAR(7)"),
    ("vessel_placements", "moved_from_id", "INTEGER"),
    # нахлёст на соседний причал: судно заходит на чужой фронт. Колонка без
    # FOREIGN KEY — удаление причала не имеет права молча съесть чужой габарит,
    # поэтому висящие ссылки проверяет код (см. api/vessels)
    ("vessel_placements", "spill_berth_id", "INTEGER"),
    # предупреждения разбора среза 1С (п. 18 TASKS2.md): загрузка проходит,
    # но неполные данные должны быть видны в журнале «Последние загрузки»
    ("sync_runs", "warnings", "TEXT"),
]


# Ключ advisory-лока: «имя» мьютекса инициализации. Произвольное и постоянное:
# поменяешь — и два процесса перестанут конкурировать.
_INIT_LOCK_KEY = 754_300_001

# Признаки, что объект создал конкурент: диалекты формулируют отказ по-разному.
_ALREADY_THERE = ("already exists", "duplicate column name")


def _is_already_there(exc: DBAPIError) -> bool:
    lowered = str(exc).lower()
    return any(marker in lowered for marker in _ALREADY_THERE)


@contextlib.contextmanager
def _init_lock():
    """init_db на старте выполняют все воркеры uvicorn одновременно. На PostgreSQL
    такая DDL-гонка — это не только «duplicate column», но и взаимная блокировка на
    ALTER TABLE одной таблицы, поэтому процессы садятся в очередь advisory-локом.
    На SQLite DDL сериализован самой базой, а «уже существует» — штатный исход
    гонки, его обрабатывает _is_already_there (п. 13 TASKS2.md).
    """
    if engine.dialect.name != "postgresql":
        yield
        return
    conn = engine.connect()
    try:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _INIT_LOCK_KEY})
        yield
    finally:
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _INIT_LOCK_KEY})
        except DBAPIError:
            # Снимать нечем: соединение всё равно закрывается, лок отдаст разрыв.
            log.warning("Advisory-лок инициализации снят разрывом соединения")
        conn.close()


def init_db() -> None:
    from . import models  # noqa: F401  — регистрирует таблицы

    with _init_lock():
        _create_all()
        _ensure_columns()
        drop_obsolete_indexes()


def _create_all() -> None:
    """create_all сверяет наличие таблицы и только потом её создаёт; между этими
    шагами соседний воркер успевает создать её же."""
    try:
        Base.metadata.create_all(bind=engine)
    except DBAPIError as exc:
        if not _is_already_there(exc):
            raise
        log.info("Часть таблиц создал соседний процесс — повторяем сверку")
        Base.metadata.create_all(bind=engine)   # теперь таблицы есть: пройдёт молча


def _ensure_columns() -> None:
    insp = inspect(engine)
    for table, column, ddl in _ADDED_COLUMNS:
        if table not in insp.get_table_names():
            continue
        if column in {c["name"] for c in insp.get_columns(table)}:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        except DBAPIError as exc:
            if not _is_already_there(exc):
                raise
            log.info("Колонку %s.%s добавил соседний процесс", table, column)


# Дублирующие индексы, которые модели уже не объявляют (п. 32 TASKS2.md).
# Каждый полностью покрывался другим объектом той же таблицы — уникальным
# ограничением или составным индексом, где те же колонки шли первыми: planner
# обходился покрывающим объектом, лишний индекс только замедлял запись. Уникальные
# ограничения и составные индексы, на которые опираются уникальность срезов и
# каскад внешних ключей, в список не входили.
# (имя индекса, таблица) — таблица нужна, чтобы не слать DROP на каждую строку.
_OBSOLETE_INDEXES: tuple[tuple[str, str], ...] = (
    # Первые столбцы uq_user_wh и ix_placement_berth: по покрывающему объекту
    # внешние ключи проверяют и каскад, и RESTRICT.
    ("ix_user_warehouse_scopes_user_id", "user_warehouse_scopes"),
    ("ix_vessel_placements_berth_id", "vessel_placements"),
    # Витрина-срезы: (дата, склад) уже задан уникальными uq_cb_snap / uq_wc_snap
    # / uq_pd_day — под ними прячутся и одиночные индексы даты, и составные
    # ix_cb_snap_wh / ix_wc_snap_wh / ix_pd_day_wh.
    ("ix_fact_cargo_balance_snapshot_at", "fact_cargo_balance"),
    ("ix_cb_snap_wh", "fact_cargo_balance"),
    ("ix_fact_warehouse_capacity_snapshot_at", "fact_warehouse_capacity"),
    ("ix_wc_snap_wh", "fact_warehouse_capacity"),
    ("ix_fact_processing_dynamics_bucket_date", "fact_processing_dynamics"),
    ("ix_pd_day_wh", "fact_processing_dynamics"),
    # Смены «отчёта диспетчера»: (report_date, shift) — первый ключ uq_ds_shift
    # и составных ix_*_date; одиночная report_date отдельного индекса не просит.
    ("ix_fact_dispatcher_shift_report_date", "fact_dispatcher_shift"),
    ("ix_ds_date", "fact_dispatcher_shift"),
    ("ix_fact_vessel_operation_report_date", "fact_vessel_operation"),
    ("ix_fact_rail_operation_report_date", "fact_rail_operation"),
    ("ix_fact_truck_operation_report_date", "fact_truck_operation"),
    ("ix_fact_vessel_arrival_report_date", "fact_vessel_arrival"),
    ("ix_fact_rail_arrival_report_date", "fact_rail_arrival"),
    ("ix_fact_rail_departure_report_date", "fact_rail_departure"),
    ("ix_fact_rail_delivery_report_date", "fact_rail_delivery"),
    ("ix_fact_platform_balance_report_date", "fact_platform_balance"),
)


def drop_obsolete_indexes() -> None:
    """Снести индексы, которые модели уже не объявляют (п. 32 TASKS2.md).

    create_all создаёт недостающее, но лишнего не удаляет: на живой базе
    дубли доживали бы до ручного DROP. Список проходится идемпотентным
    DROP INDEX IF EXISTS — повторный init_db безопасен, конкурентный старт
    второго воркера тоже, а данные удаление индексов не затрагивает вовсе.
    """
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for name, table in _OBSOLETE_INDEXES:
        if table not in tables:
            continue
        # Сверка с наличием — только ради тихого лога: иначе после первой
        # чистки каждый старт рапортовал бы про 18 «снесённых» индексов.
        if not any(ix["name"] == name for ix in insp.get_indexes(table)):
            continue
        with engine.begin() as conn:
            conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
        log.info("Дублирующий индекс %s убран (п. 32 TASKS2.md)", name)


def db_filename() -> str:
    return os.path.basename(get_settings().database_url.split("///", 1)[-1] or "")
