from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _build_engine():
    s = get_settings()
    url = s.database_url
    if url.startswith("sqlite"):
        raw = url.split("///", 1)[-1]
        if raw and raw != ":memory:":
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
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True, future=True,
                         **pool_kwargs)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


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
]


def init_db() -> None:
    from . import models  # noqa: F401  — регистрирует таблицы

    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def _ensure_columns() -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    for table, column, ddl in _ADDED_COLUMNS:
        if table not in insp.get_table_names():
            continue
        if column in {c["name"] for c in insp.get_columns(table)}:
            continue
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def db_filename() -> str:
    return os.path.basename(get_settings().database_url.split("///", 1)[-1] or "")
