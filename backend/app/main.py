"""Кабинет веб-статистики: вход для менеджеров и топов, данные из 1С."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import admin, auth, metrics
from .config import get_settings
from .db import SessionLocal, init_db
from .deps import require_roles
from .models import Role
from .onec.sync import run_sync
from .seed import ensure_admin, seed_demo_users

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
            run = run_sync(db, days=30)
            log.info("Витрина инициализирована источником %s: %s строк", run.source, run.rows)
        seed_demo_users(db)
    finally:
        db.close()

    scheduler = None
    if s.sync_enabled:
        from apscheduler.schedulers.background import BackgroundScheduler

        def job() -> None:
            session = SessionLocal()
            try:
                run_sync(session, days=30)
            finally:
                session.close()

        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(job, "interval", minutes=s.sync_interval_minutes,
                          max_instances=1, coalesce=True)
        scheduler.start()
        log.info("Синхронизация с 1С каждые %d мин (источник: %s)",
                 s.sync_interval_minutes, s.one_c_source)

    yield

    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title=get_settings().app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router)
app.include_router(metrics.router)
app.include_router(admin.router)


@app.get("/healthz", tags=["service"])
def healthz():
    s = get_settings()
    return {"status": "ok", "env": s.environment, "one_c_source": s.one_c_source}


@app.post("/api/sync/run", tags=["service"], dependencies=[Depends(require_roles(Role.ADMIN))])
def manual_sync():
    """Пинком обновить витрину из 1С (админ)."""
    db = SessionLocal()
    try:
        run = run_sync(db, days=30)
        return {"status": run.status, "rows": run.rows, "source": run.source, "error": run.error}
    finally:
        db.close()


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Необработанная ошибка на %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})
