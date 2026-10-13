"""Кабинет веб-статистики: вход для менеджеров и топов, данные из 1С."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .api import (admin, admin_settings, admin_warehouses, auth, meta, metrics, onec_push, reports,
                  settings)
from .config import get_settings
from .db import SessionLocal, get_db, init_db
from .deps import require_roles
from .models import Role
from .onec.sync import run_sync
from .seed import ensure_admin, seed_demo_users
from .services import app_settings, scheduling

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("statcab")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    init_db()

    db = SessionLocal()
    try:
        temp = ensure_admin(db)
        if temp:
            log.warning("Создан администратор «%s» с паролем «%s». Смените его сразу после входа.",
                        s.admin_login, temp)
        if s.seed_demo_data:
            run = run_sync(db)
            log.info("Витрина инициализирована источником %s: %s строк", run.source, run.rows)
        seed_demo_users(db)
    finally:
        db.close()

    # Планировщик заводим всегда, даже если sync.enabled=false: тогда job просто
    # не заведён, а админ включает синхронизацию из UI без перезапуска сервиса.
    # Интервал и наличие job-а считает scheduling.reschedule() по настройкам из стора.
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.start()
    app.state.scheduler = scheduler

    db = SessionLocal()
    try:
        log.info("Расписание синхронизации с 1С: %s", scheduling.reschedule(scheduler, db))
    finally:
        db.close()

    yield

    app.state.scheduler = None
    scheduler.shutdown(wait=False)


app = FastAPI(title=get_settings().app_name, version="0.1.0", lifespan=lifespan)
# Планировщик живёт в app.state: из PUT /api/admin/settings его перепланируют.
app.state.scheduler = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-OneC-Token"],
)

app.include_router(auth.router)
app.include_router(metrics.router)
app.include_router(meta.router)
app.include_router(admin.router)
app.include_router(admin_settings.router)
app.include_router(admin_warehouses.router)
app.include_router(settings.router)
app.include_router(reports.router)
app.include_router(onec_push.router)


@app.get("/healthz", tags=["service"])
def healthz(db: Session = Depends(get_db)):
    s = get_settings()
    return {"status": "ok", "env": s.environment,
            "one_c_source": app_settings.get(db, "onec.source"),
            "push_enabled": bool(app_settings.get(db, "onec.push_token"))}


@app.post("/api/sync/run", tags=["service"], dependencies=[Depends(require_roles(Role.ADMIN))])
def manual_sync(db: Session = Depends(get_db)):
    """Старый адрес пинка синхронизации: см. POST /api/admin/sync/run.

    Оставлен, чтобы не сломать существующие скрипты и документацию.
    """
    run = run_sync(db)
    return {"status": run.status, "rows": run.rows, "source": run.source, "error": run.error}


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Необработанная ошибка на %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})
