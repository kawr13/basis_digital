"""Авторизация кабинета веб-статистики.

Схема:
  POST /api/auth/login    логин+пароль → access (JWT, 30 мин) в теле + refresh в httpOnly-cookie
  POST /api/auth/refresh  ротация refresh → новый access
  POST /api/auth/logout   отзыв refresh
  GET  /api/auth/me       кто я и какие склады мне доступны
  POST /api/auth/change-password

Защита: scrypt-пароли, лимит неудачных попыток → временная блокировка,
троттлинг по IP, отзыв всей «семьи» refresh-токенов при повторном использовании.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import client_ip, current_user
from ..models import AuditLog, RefreshToken, User, UserWarehouseScope, utcnow
from ..schemas import ChangePasswordRequest, LoginRequest, TokensOut, UserOut
from ..services import app_settings
from ..security import (
    create_access_token, hash_refresh, new_refresh_token, password_is_strong, verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

REFRESH_COOKIE = "sc_refresh"

# Троттлинг в памяти процесса. Для нескольких воркеров/инстансов — перенести в Redis.
_hits: dict[str, deque[float]] = defaultdict(deque)


def _throttle(ip: str) -> None:
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= get_settings().login_throttle_per_ip:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток входа, подождите минуту")
    q.append(now)


def _audit(db: Session, user_id: int | None, login: str | None, action: str, ip: str, detail: str = "") -> None:
    db.add(AuditLog(user_id=user_id, login=login, action=action, ip=ip, detail=detail[:500] or None))
    db.commit()


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id, login=user.login, full_name=user.full_name, role=user.role.value,
        email=user.email, all_warehouses=user.all_warehouses,
        warehouse_ids=user.warehouse_ids, last_login_at=user.last_login_at,
        vessel_right=user.vessel_right,
    )


def _set_refresh_cookie(response: Response, raw: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE, value=raw,
        max_age=s.refresh_token_ttl_days * 86400,
        httponly=True, samesite="lax", secure=(s.environment == "prod"),
        path="/api/auth",
    )


def _issue(db: Session, user: User, response: Response, ip: str, ua: str,
           family_id: str | None = None) -> TokensOut:
    raw, rec = new_refresh_token(user.id, family_id=family_id, ip=ip, user_agent=ua)
    db.add(rec)
    db.commit()
    _set_refresh_cookie(response, raw)
    token, ttl = create_access_token(user.id, user.role.value)
    return TokensOut(access_token=token, expires_in=ttl, user=_user_out(user))


@router.post("/login", response_model=TokensOut)
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    ip, ua = client_ip(request), request.headers.get("User-Agent", "")
    _throttle(ip)

    user = db.scalar(select(User).where(User.login == payload.login.strip().lower()))

    # Единое сообщение: не даём разведать, какой логин существует.
    denied = HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")

    if not user:
        _audit(db, None, payload.login, "login.unknown_user", ip)
        raise denied

    if not user.is_active:
        _audit(db, user.id, user.login, "login.inactive", ip)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Учётка отключена, обратитесь к администратору")

    if user.locked_until and user.locked_until > utcnow():
        left = int((user.locked_until - utcnow()).total_seconds() // 60) + 1
        _audit(db, user.id, user.login, "login.locked", ip)
        raise HTTPException(status.HTTP_423_LOCKED, f"Учётка заблокирована, осталось {left} мин")

    if not verify_password(payload.password, user.password_hash):
        user.failed_attempts += 1
        detail = ""
        max_fails = int(app_settings.get(db, "security.max_failed_logins"))
        lock_minutes = int(app_settings.get(db, "security.lockout_minutes"))
        if user.failed_attempts >= max_fails:
            user.locked_until = utcnow() + timedelta(minutes=lock_minutes)
            user.failed_attempts = 0
            detail = f"блокировка {lock_minutes} мин"
            _audit(db, user.id, user.login, "login.lockout", ip, detail)
        else:
            _audit(db, user.id, user.login, "login.fail", ip, detail)
        db.commit()
        raise denied

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    db.commit()
    _audit(db, user.id, user.login, "login.ok", ip)
    return _issue(db, user, response, ip, ua)


@router.post("/refresh", response_model=TokensOut)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    """Ротация refresh. Повторное использование украденного токена роняет всю семью."""
    raw = request.cookies.get(REFRESH_COOKIE) or (request.query_params.get("refresh_token") or "")
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нет refresh-токена")

    rec = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh(raw)))
    ip, ua = client_ip(request), request.headers.get("User-Agent", "")
    if not rec:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Токен недействителен")

    if rec.revoked_at:
        # reuse detection: отзываем все токены семьи и разворачиваем пользователя
        for other in db.scalars(select(RefreshToken).where(RefreshToken.family_id == rec.family_id)):
            if not other.revoked_at:
                other.revoked_at = utcnow()
        db.commit()
        _audit(db, rec.user_id, None, "refresh.reuse", ip, f"family={rec.family_id}")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия завершена, войдите заново")

    if rec.expires_at <= utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла, войдите заново")

    user = db.get(User, rec.user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Учётка недоступна")

    rec.revoked_at = utcnow()
    db.commit()
    _audit(db, user.id, user.login, "refresh.ok", ip)
    return _issue(db, user, response, ip, ua, family_id=rec.family_id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE) or (request.query_params.get("refresh_token") or "")
    if raw:
        rec = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh(raw)))
        if rec and not rec.revoked_at:
            rec.revoked_at = utcnow()
            db.commit()
            _audit(db, rec.user_id, None, "logout", client_ip(request))
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return _user_out(user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: ChangePasswordRequest, request: Request,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Текущий пароль неверный")
    ok, msg = password_is_strong(payload.new_password)
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, msg)

    from ..security import hash_password

    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = utcnow()
    # смена пароля разворачивает все активные сессии
    for rec in db.scalars(select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))):
        rec.revoked_at = utcnow()
    db.commit()
    _audit(db, user.id, user.login, "password.change", client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
