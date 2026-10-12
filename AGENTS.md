# Project Guide

## Overview
- **Project Name**: Кабинет веб-статистики (StatCab) · БазисПорт
- **Type**: Full-stack веб-сервис внутренней отчётности
- **Primary Language(s)**: Python (backend), TypeScript/React (frontend)
- **Framework(s)**: FastAPI + SQLAlchemy 2.0, React 18 + Vite + Tailwind
- **Description**: Авторизованный кабинет, где менеджеры и топ-менеджмент видят остатки
  грузов, загрузку (ёмкость) складов и динамику обработки. Все цифры приходят из 1С:
  коннектор складывает срез в локальную витрину, дашборд читает только витрину.

## Tech Stack
- **Runtime**: Python 3.11+ (в Docker — 3.12), Node 20
- **Backend**: FastAPI, SQLAlchemy 2.0 (sync), Pydantic v2, PyJWT, APScheduler, httpx
- **Frontend**: React 18 + TypeScript, Vite, TailwindCSS, react-router; графики — собственный SVG
- **Database**: PostgreSQL (прод) / SQLite (разработка) — один и тот же код
- **Auth**: логин+пароль (hashlib.scrypt), access JWT 30 мин, refresh в httpOnly-cookie с ротацией
- **Container**: Docker Compose (db + backend + nginx-фронт)

## Project Structure
```
statcab/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI, lifespan: init_db + первая загрузка витрины + scheduler
│   │   ├── config.py        # Settings из окружения (pydantic-settings)
│   │   ├── db.py            # engine/SessionLocal/get_db
│   │   ├── models.py        # User, Warehouse, UserWarehouseScope, RefreshToken, AuditLog, Fact*, SyncRun
│   │   ├── schemas.py       # Pydantic-схемы запросов/ответов
│   │   ├── security.py      # scrypt-пароли, JWT, refresh-токены
│   │   ├── deps.py          # current_user, require_roles, client_ip
│   │   ├── seed.py          # администратор при первом запуске + демо-пользователи (dev)
│   │   ├── api/             # auth.py, metrics.py, admin.py, settings.py (настройки), reports.py (отчёты+CSV)
│   │   ├── services/metrics.py  # SQL-запросы витрины с фильтром по доступным складам
│   │   ├── services/reports.py  # отчёты за произвольный период + итоги + тренд загрузки
│   │   └── onec/            # base.py (контракт источника), mock.py, http_source.py, sync.py
│   ├── tests/               # test_auth.py, test_reports.py, test_settings.py (27 тестов)
│   └── requirements*.txt    # prod (пин) / dev (свежие колёса) / prod+psycopg2
├── frontend/
│   ├── src/api.ts           # fetch-клиент: access в памяти, refresh в cookie, авто-повтор 401
│   ├── src/auth.tsx         # AuthProvider: восстановление сессии, login/logout
│   ├── src/pages/           # LoginPage, DashboardPage, ReportsPage
│   ├── src/components/      # AppShell (шапка+навигация), WidgetGrid (drag&drop плашек),
│   │                        # KpiCard, DynamicsChart (SVG, кликабельный), CapacityPanel,
│   │                        # CargoTable, DrilldownPanel (детализация за день / по складу)
│   └── nginx.conf           # прод-фронт: SPA + прокси /api → backend
├── docker-compose.yml       # db + backend + web
├── docs/                    # 01 постановка и вопросы, 02 архитектура, 03 контракт 1С
└── tools/ui_smoke.mjs       # браузерный smoke (playwright-core)
```

## Key Conventions
- **Граница с 1С**: в базу 1С не пишем и SQL туда не пускаем. Только HTTP-читалка → витрина `Fact*`.
- **Витрина плоская**: 1С отдаёт готовые строки, агрегаты считает кабинет (SQL), а не 1С.
- **Multi-warehouse scope**: пустой `UserWarehouseScope` = доступ ко всем складам (роль `top`);
  менеджер видит только назначенные. Любой эндпоинт метрик обязан фильтровать по scope.
- **access-токен** живёт только в памяти браузера (не в localStorage), refresh — httpOnly-cookie.
- **Персональные настройки** (`user_preferences`, JSON в TEXT): раскладка дашборда и фильтры
  отчётов. Ключи белым списком (`KNOWN_KEYS`), размер значения ограничен. Храним на сервере,
  а не в localStorage — настройка следует за пользователем между браузерами.
- **Раскладка виджетов**: `mergeLayout()` склеивает сохранённый порядок с кодовым списком
  виджетов — новые доезжают в конец, удалённые исчезают. Ширина — span в 12-колоночной сетке,
  высота — пиксели (`h`, ограничена 120–900 px). Ручки размера: правый край (ширина, шаг —
  колонка сетки), нижний край (высота), правый нижний угол (оба); двойной клик по заголовку —
  сброс. Графики рисуются в реальных пикселях (ResizeObserver + viewBox), поэтому при
  растягивании плашки текст не деформируется.
- **Отчёты** фильтруются по тому же scope, что и метрики: чужой склад в параметре
  `warehouses` = 403, а не чужие строки в ответе.
- **Детализация (drill-down)**: клик по столбцу графика (сутки) или строке ёмкости (склад)
  открывает панель `DrilldownPanel` — данные берутся из тех же `/api/reports/*`, поэтому цифры
  панели и отчёта всегда совпадают, а права фильтрует бэкенд. Панель вне сетки виджетов
  (фиксированная справа / снизу на мобильном), закрывается повторным кликом, «Закрыть» и Esc.
- **Время** храним наивным UTC (`models.utcnow()`), чтобы SQLite и PG вели себя одинаково.
- **Ошибки входа** — всегда один текст («Неверный логин или пароль»), чтобы не светить существование учётки.
- **Пароли**: `scrypt$N$r$salt$hash` (stdlib, без нативных зависимостей).
- Файлы/переменные — snake_case (Python), kebab/camel (TS); комментарии и UI-текст — по-русски.

## Commands
| Command | Description |
|---|---|
| `cd backend && .venv/Scripts/python.exe -m pytest` | тесты авторизации и доступа |
| `cd backend && uvicorn app.main:app --reload --port 8000` | dev-бэкенд |
| `cd frontend && npm run dev` | dev-фронт (проксирует `/api` на :8000) |
| `cd frontend && npm run build` | typecheck (`tsc -b`) + прод-сборка |
| `docker compose up -d --build` | полный стек |
| `curl -X POST .../api/sync/run -H "Authorization: Bearer <token>"` | ручной запуск загрузки из 1С |
| `node tools/ui_smoke.mjs` | браузерная проверка входа и дашборда |
| `node tools/ui_customization.mjs` | браузерная проверка drag&drop, отчётов и CSV |
| `node tools/ui_drilldown.mjs` | браузерная проверка кликабельных графиков и панели деталей |

## Definition of Done (фича готова, если)
1. Бэкенд: есть тест на новый сценарий, `pytest` зелёный.
2. Права: неавторизованный запрос = 401, чужая роль = 403, чужой склад не виден в ответе.
3. Фронт: `npm run build` проходит (strict TS), экран не ломается при пустых данных и ошибке API.
4. Данные: в ответе есть `snapshot_at`/`data_as_of` — пользователь видит, когда срез.

## Open blockers (ждём ответа заказчика, см. docs/01)
- Как 1С отдаёт данные (HTTP-сервис / OData / выгрузка файла) и в каких единицах.
- Что именно считать «остатками», «ёмкостью» и «динамикой обработки» (документы-источники).
- Нужна ли авторизация по ЭЦП (КриптоПро) вместо/вместе с паролем.
- Где живёт сервис (внутренний контур / VPN / хостинг) и сколько пользователей.
