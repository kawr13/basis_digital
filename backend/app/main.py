"""Кабинет веб-статистики: вход для менеджеров и топов, данные из 1С."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .api import (admin, admin_settings, admin_warehouses, auth, dispatcher, meta, metrics, onec_push,
                  reports, settings, vessels)
from .config import get_settings
from .db import SessionLocal, get_db, init_db
from .deps import require_roles
from .models import Role
from .onec.http_source import close_clients
from .onec.sync import SYNC_BUSY, run_sync, try_sync
from .seed import ensure_admin, seed_demo_users
from .services import app_settings, scheduling
from .services.dispatcher import auto_import
from .services.vessels import ensure_park, seed_berths

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
        # Причалы — не данные 1С, это часть планировочного документа «Постановка
        # судов»: при пустом справочнике заводим типовые, иначе новому пользователю
        # некуда ставить суда.
        created = seed_berths(db)
        if created:
            log.info("Справочник причалов заполнен: %d шт.", created)
        # Строка «Ожидание» — часть документа, как и причалы: заводим и на новой
        # базе, и на той, что жила до появления кармана.
        if ensure_park(db):
            log.info("Заведена строка «Ожидание»: суда вне причалов.")
        # Отчёт диспетчера — реальные цифры страницы «Статистика». Файл лежит на
        # сервере (пока 1С не отдаёт отчёт по HTTP), грузим его только когда он
        # новее витрины; ошибка не мешает запуску кабинета.
        try:
            log.info("Отчёт диспетчера: %s", auto_import(db))
        except Exception:                       # noqa: BLE001 — старт важнее
            log.exception("Не удалось импортировать отчёт диспетчера")
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
    # Общий httpx.Client живёт до остановки процесса: соединение с 1С должно
    # закрываться здесь, а не висеть до милости сборщика мусора.
    if close_clients():
        log.info("HTTP-клиенты 1С закрыты")


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
app.include_router(vessels.router)
app.include_router(dispatcher.router)
app.include_router(dispatcher.admin_router)
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
    run = try_sync(db)
    if run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, SYNC_BUSY)
    return {"status": run.status, "rows": run.rows, "source": run.source, "error": run.error}


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Необработанная ошибка на %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})
