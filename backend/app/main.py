"""Кабинет веб-статистики: вход для менеджеров и топов, данные из 1С."""
from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .api import (admin, admin_settings, admin_warehouses, auth, dispatcher, meta, metrics, onec_push,
                  reports, settings, vessels)
from .config import get_settings
from .db import SessionLocal, get_db, init_db, masked_url
from .deps import require_roles
from .models import Role, SyncRun, utcnow
from .onec.http_source import close_clients
from .onec.sync import SYNC_BUSY, reconcile_hung_runs, run_sync, try_sync
from .seed import ensure_admin, seed_demo_users
from .services import access_log, app_settings, dbconfig, scheduling
from .services.dispatcher import auto_import
from .services.vessels import ensure_park, seed_berths

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("statcab")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    init_db()

    # Кабинет стартует на `DATABASE_URL` — больше брать неоткуда. Если админ
    # переехал на другую базу из админки (п. 27 TASKS2.md), знает об этом только
    # та, первая база: читаем указатель и переезжаем до всего остального, чтобы
    # админы, витрина и планировщик жили уже на новой.
    with SessionLocal() as boot:
        moved = dbconfig.bootstrap(boot)
    if moved and moved.get("error"):
        log.error("База из настроек недоступна (%s). Кабинет работает на %s",
                  moved["error"], masked_url())
    elif moved:
        log.info("Кабинет продолжил старт на базе из настроек: %s", moved["url"])

    db = SessionLocal()
    try:
        # Строка «running» означает «загрузка идёт прямо сейчас». После kill -9 или
        # рестарта контейнера такая строка повисла бы навсегда: дашборд писал бы
        # «идёт загрузка», хотя грузить уже некому (п. 16 TASKS2.md).
        reconcile_hung_runs(db)
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
    scheduling.register_fixed_jobs(scheduler)

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


# В проде паспорт API наружу не отдаём: /docs, /redoc и /openapi.json — готовый
# список ручек для подбора доступа. Ключевое слово-параметр FastAPI понимает None.
IS_PROD = get_settings().environment == "prod"
app = FastAPI(title=get_settings().app_name, version="0.1.0", lifespan=lifespan,
              docs_url=None if IS_PROD else "/docs",
              redoc_url=None if IS_PROD else "/redoc",
              openapi_url=None if IS_PROD else "/openapi.json")


# Планировщик живёт в app.state: из PUT /api/admin/settings его перепланируют.
app.state.scheduler = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    # X-Request-ID (п. 25 TASKS2.md): клиент вправе прислать свой id — значит
    # он участвует в preflight; expose_headers — чтобы фронт увидел id ответа и
    # продиктовал его в заявке, когда кабинет ответил ошибкой.
    allow_headers=["Authorization", "Content-Type", "X-OneC-Token", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)

# add_middleware кладёт каждую следующую прослойку ПОВЕРХ предыдущей, поэтому
# логатор добавлен после CORS и оказывается внешним: строка доступа пишется и
# на отклонённый CORS-запрос, и X-Request-ID дописан к ответу раньше, чем CORS
# решил, отдавать ли заголовки доступа (свои он навешивает на готовый ответ и
# X-Request-ID не трогает). Аутентификация — не прослойка, а зависимости самих
# ручек (deps.current_user): они отрабатывают внутри, поэтому логин в состоянии
# запроса готов к моменту, когда пишется строка доступа.
app.add_middleware(access_log.AccessLogMiddleware)

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
    """Живость процесса и базы. Подробностей про 1С здесь нет: проба доступна без
    аутентификации, а конфигурация источника — разведданные. Свежесть витрины
    смотрит /readyz (п. 19 TASKS2.md)."""
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


def _readyz_payload(request: Request, token: str | None) -> tuple[int, dict]:
    """Собирает тело пробы готовности: база, возраст последней успешной загрузки,
    планировщик. Состав одинаков для 200 и 503 — мониторинг разбирает одну схему
    (п. 19 TASKS2.md). Секретов, путей и конфига 1С здесь нет: /readyz смотрит наружу.
    """
    body: dict = {"status": "ok", "db": "ok", "seconds_since_sync": None,
                  "sync_age_limit_sec": int(app_settings.DEFAULTS["sync.age_limit_sec"]),
                  "scheduler": {"running": False, "jobs": []},
                  "token_required": False}
    reasons: list[str] = []

    # Не Depends(get_db): мёртвая база должна дать 503 с телом, а не 500 из зависимости.
    db = None
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        readyz_token = str(app_settings.get(db, "service.readyz_token") or "")
        # Токен принимается из стора ИЛИ из READY_TOKEN (env). Иначе он жил бы в двух
        # местах с одним смыслом: админ заводит/меняет токен в UI, контейнер про него
        # не знает и краснеет, а оператор, наоборот, заводит только env — и проба
        # остаётся открытой (п. 19 TASKS2.md, дефект D3).
        env_token = os.environ.get("READY_TOKEN", "")
        limit = int(app_settings.get(db, "sync.age_limit_sec"))
        body["sync_age_limit_sec"] = limit
        body["token_required"] = bool(readyz_token or env_token)
        if body["token_required"] and not (
            (readyz_token and hmac.compare_digest(token or "", readyz_token))
            or (env_token and hmac.compare_digest(token or "", env_token))
        ):
            return status.HTTP_403_FORBIDDEN, {"detail": "Токен готовности отсутствует или неверен"}
        last_ok = db.scalar(
            select(SyncRun.finished_at)
            .where(SyncRun.status == "ok", SyncRun.finished_at.is_not(None))
            .order_by(SyncRun.finished_at.desc())
            .limit(1))
        if last_ok is None:
            # Кабинет без витрины людям цифры не показывает.
            reasons.append("успешных загрузок из 1С ещё не было")
        else:
            age = int((utcnow() - last_ok).total_seconds())
            body["seconds_since_sync"] = age
            if age > limit:
                reasons.append(f"витрина устарела: последняя успешная загрузка {age} с назад "
                               f"(порог {limit} с)")
    except Exception:                        # noqa: BLE001 — база лежит → 503, а не 500
        log.exception("/readyz: проверка базы не прошла")
        body["db"] = "error"
        reasons.append("база данных не отвечает")
    finally:
        if db is not None:
            db.close()

    try:
        body["scheduler"] = scheduling.state(request.app.state.scheduler)
    except Exception:                        # noqa: BLE001 — сломанный планировщик не ослепляет пробу
        log.exception("/readyz: состояние планировщика не прочитать")

    if reasons:
        body["status"] = "unavailable"
        body["reason"] = "; ".join(reasons)
        return status.HTTP_503_SERVICE_UNAVAILABLE, body
    return status.HTTP_200_OK, body


@app.get("/readyz", tags=["service"])
@app.get("/api/readyz", tags=["service"])
def readyz(request: Request, token: str | None = None):
    """Внешняя готовность (п. 19 TASKS2.md): 200 — только когда база жива и витрина
    не устарела. Выключенный планировщик — НЕ 503: кабинет, который сам не грузит,
    людям отдавать можно то, что уже загружено.

    Один обработчик на двух путях: nginx проксирует только /api, а docker-проба
    внутри контейнера достучится до /readyz напрямую. Пользовательского JWT нет:
    мониторинг про сессии не знает, вместо него сервисный токен — принимается
    значение из стора настроек (service.readyz_token) ИЛИ из переменной READY_TOKEN.
    Ни там, ни там токена нет — доступ открыт, но это видно в теле
    (token_required: false), чтобы мониторинг молча не принял 200 чужого сервиса.
    """
    code, payload = _readyz_payload(request, token)
    return JSONResponse(status_code=code, content=payload)


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
    # Request-id в тексте (п. 25 TASKS2.md): по нему traceback и строку доступа
    # одного запроса находят друг друга одним grep.
    log.exception("Необработанная ошибка на %s (request-id %s)", request.url.path,
                  access_log.request_id_of(request))
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})
