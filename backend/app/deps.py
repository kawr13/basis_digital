"""Зависимости FastAPI: текущий пользователь, проверка ролей."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .db import get_db
from .models import Role, User
from .security import decode_access_token

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
    return user


def require_roles(*roles: Role):
    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
        return user

    return checker


def client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
