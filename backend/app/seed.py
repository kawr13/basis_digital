"""Первичная инициализация: администратор, демо-пользователи, демо-витрина."""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Role, User, UserWarehouseScope, Warehouse
from .security import hash_password, random_password

log = logging.getLogger("statcab.seed")

DEMO_USERS = [
    # логин, ФИО, роль, склады (пусто = все)
    ("ivanova", "Иванова Анна Петровна", Role.TOP, []),
    ("petrov", "Петров Сергей Николаевич", Role.MANAGER, ["WH-01", "WH-02"]),
    ("sidorova", "Сидорова Мария Викторовна", Role.MANAGER, ["WH-04", "WH-05"]),
]


def ensure_admin(db: Session) -> str | None:
    """Создаёт администратора, если пользователей нет. Возвращает временный пароль."""
    if db.scalar(select(func.count(User.id))):
        return None

    s = get_settings()
    pwd = s.admin_password or random_password()
    db.add(User(
        login=s.admin_login.strip().lower(), full_name="Администратор кабинета",
        role=Role.ADMIN, password_hash=hash_password(pwd),
    ))
    db.commit()
    return pwd


def seed_demo_users(db: Session) -> None:
    """Только для dev: менеджеры, чтобы посмотреть разницу прав."""
    if get_settings().environment == "prod":
        return
    wh_by_code = {w.code: w.id for w in db.scalars(select(Warehouse))}
    pwd = "demo1234"
    for login, full_name, role, codes in DEMO_USERS:
        if db.scalar(select(User).where(User.login == login)):
            continue
        user = User(login=login, full_name=full_name, role=role,
                    password_hash=hash_password(pwd))
        db.add(user)
        db.flush()
        for code in codes:
            if code in wh_by_code:
                db.add(UserWarehouseScope(user_id=user.id, warehouse_id=wh_by_code[code]))
    db.commit()
    if db.scalar(select(User).where(User.login == "petrov")):
        log.warning("Демо-пользователи созданы (пароль %s). В prod их не будет.", pwd)
