"""Зависимости FastAPI: текущий пользователь, проверка ролей и доступа к разделам."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import Role, User
from .security import decode_access_token
from .services import sections as section_svc

_UNAUTH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Требуется авторизация",
    headers={"WWW-Authenticate": "Bearer"},
)


def bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise _UNAUTH
    return header[7:].strip()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    payload = decode_access_token(bearer_token(request))
    if not payload:
        raise _UNAUTH
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise _UNAUTH
    # Строка доступа (п. 25 TASKS2.md) пишет логин: в JWT его нет, а вторично
    # расшифровывать токен и ходить в базу ради одной строки лога — работа для
    # каждой ручки. Строка, а не объект: к моменту записи лога сессия закрыта.
    request.state.login = user.login
    return user


def require_roles(*roles: Role):
    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
        return user

    return checker


def require_section(*keys: str):
    """Зависимость эндпоинта: раздел кабинета («вкладка») человеку доступен.

    Несколько ключей — «хотя бы один»: так одни и те же данные показывают два
    раздела (детализация дашборда «Склады 1С» берёт строки из /api/reports/*).
    Пересечение глобальной галки, личного списка и роли считает
    `services/sections.py`.
    """

    def checker(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
        if any(section_svc.section_allowed(db, user, key) for key in keys):
            return user
        if len(keys) == 1:
            detail = section_svc.deny(db, user, keys[0])
        else:
            detail = "Нет доступа к разделам: " + ", ".join(section_svc.label(k) for k in keys)
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail)

    return checker


def client_ip(request: Request) -> str:
    """IP, с которого пришли. X-Forwarded-For принимаем только когда перед кабинетом
    заведомо стоит наш прокси (TRUST_PROXY_HEADERS): заголовок дописывается клиентом
    бесплатно, и без флага любой желающий вписывает себе адрес в AuditLog и обходит
    ограничение подбора пароля по IP (п. 19–21 TASKS2.md).
    """
    direct = request.client.host if request.client else "unknown"
    st = get_settings()
    if not st.trust_proxy_headers:
        return direct
    hops = [h.strip() for h in request.headers.get("X-Forwarded-For", "").split(",") if h.strip()]
    if not hops:
        return direct
    # Справа — адреса, добавленные нашими прокси; клиентский контроль заканчивается
    # перед ними, поэтому берём запись отступив справа на число своих hops.
    index = len(hops) - max(1, st.trust_proxy_hops)
    return hops[index] if index >= 0 else hops[0]
