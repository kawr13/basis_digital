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
│   │   ├── main.py          # FastAPI, lifespan: init_db + первая загрузка витрины + scheduler в app.state
│   │   ├── config.py        # Settings из окружения (pydantic-settings)
│   │   ├── db.py            # engine/SessionLocal/get_db
│   │   ├── models.py        # User, Warehouse, UserWarehouseScope, RefreshToken, AuditLog, AppSetting,
│   │   │                    # Fact*, SyncRun, UserPreference
│   │   ├── schemas.py       # Pydantic-схемы запросов/ответов
│   │   ├── security.py      # scrypt-пароли, JWT, refresh-токены
│   │   ├── deps.py          # current_user, require_roles, client_ip
│   │   ├── seed.py          # администратор при первом запуске + демо-пользователи (dev)
│   │   ├── api/             # auth.py, metrics.py, admin.py (пользователи+аудит), admin_settings.py
│   │   │                    # (настройки, 1С, журнал загрузок), admin_warehouses.py, settings.py
│   │   │                    # (персональные + /public), onec_push.py (приём среза от 1С), reports.py
│   │   ├── services/metrics.py  # SQL-запросы витрины с фильтром по доступным складам
│   │   ├── services/reports.py  # отчёты за произвольный период + итоги + тренд загрузки
│   │   ├── services/app_settings.py  # настройки времени выполнения: БД → .env → DEFAULTS
│   │   ├── services/scheduling.py  # reschedule(): интервал/наличие job-а по настройкам
│   │   └── onec/            # base.py (контракт), mock.py, http_source.py, parse.py (разбор среза),
│   │                        # sync.py (get_source/apply_snapshot/run_sync)
│   ├── tests/               # test_auth.py, test_reports.py, test_settings.py,
│   │                        # test_admin_settings.py (45 тестов)
│   └── requirements*.txt    # prod (пин) / dev (свежие колёса) / prod+psycopg2
├── frontend/
│   ├── src/api.ts           # fetch-клиент: access в памяти, refresh в cookie, авто-повтор 401
│   ├── src/auth.tsx         # AuthProvider: восстановление сессии, login/logout
│   ├── src/pages/           # LoginPage, DashboardPage, ReportsPage, AdminPage (вкладки админки)
│   ├── src/components/      # AppShell (шапка+навигация), WidgetGrid (drag&drop плашек),
│   │                        # KpiCard (+Sparkline), DynamicsChart (SVG: сплейн, тултип, перекрестие), CapacityPanel,
│   │                        # CargoTable, DrilldownPanel (детализация за день / по складу)
│   │                        # admin/ — вкладки админки (UsersTab, WarehousesTab, OneCTab,
│   │                        # SettingsTab, AuditTab), SettingsForm (форма из specs бэкенда)
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
- **Настройки времени выполнения** (`app_settings`, JSON в TEXT): приоритет БД → `.env` →
  `DEFAULTS` из `services/app_settings.py`, поэтому существующий `.env` работает, пока админ
  ничего не менял в UI. Секреты (`onec.password`, `onec.push_token`) наружу не отдаются —
  маска `***` и флаг `..._set`; каждая запись оставляет след в `audit_log`.
- **Расписание 1С** живёт в `app.state.scheduler`: `PUT /api/admin/settings` зовёт
  `scheduling.reschedule()` — меняет интервал или снимает job при `sync.enabled=false`
  без перезапуска процесса.
- **Push из 1С** (`POST /api/onec/push`, заголовок `X-OneC-Token`): тот же контракт, что и
  GET-методы; токен не задан → 503 (приём выключен). Сохранение общее — `apply_snapshot()`,
  поэтому повторный срез не плодит дубликатов (ключ: срез × склад × группа груза/сутки).
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
- **Администрирование** (`/admin`, только роль `admin`: маршрут закрывается редиректом, вкладка в
  шапке не рисуется остальным). Страница тонкая — вкладки в `components/admin/*`; форма настроек
  строится из `specs` ответа `GET /api/admin/settings`, поэтому диапазоны и типы не дублируются во
  фронте. Секретные поля (`onec.password`, `onec.push_token`) показываются пустыми: пустое поле =
  «не менять», бэкенд возвращает маску `***` и флаг `..._set`. Удаления нет ни у пользователей, ни у
  складов — только выключение. Статус учётки (активен/отключён/заблокирован) UI доопределяет сам:
  `UserOut` полей `is_active`/`locked_until` не отдаёт, поэтому изменённое в сессии держим локально.
- **Время** храним наивным UTC (`models.utcnow()`), чтобы SQLite и PG вели себя одинаково.
- **Ошибки входа** — всегда один текст («Неверный логин или пароль»), чтобы не светить существование учётки.
- **Пароли**: `scrypt$N$r$salt$hash` (stdlib, без нативных зависимостей).
- **Тема оформления** — светлая, плашки — **матовое стекло**: `rgba(255,255,255,.62)` +
  `linear-gradient(160deg, rgba(255,255,255,.78), rgba(255,255,255,.42))` +
  `backdrop-filter: blur(18px) saturate(150%)`, рамка `rgba(255,255,255,.7)`, тень `shadow-panel`
  c внутренним бликом `inset 0 1px 0 rgba(255,255,255,.9)`. Чтобы blur было ЧТО размывать,
  на `body` лежат 3 радиальных пятна (brand.sky/mid/navy, 12–18%) и сетка 44px
  (`background-attachment: fixed`); без них панель = просто белая. Шапка — `.glass-navy`
  (`rgba(27,51,96,.82)` + `blur(16px) saturate(140%)`), модалка и панель деталей получают
  стекло от `.panel`, подложка модалки `rgba(15,27,45,.35)`.
  Цвета фирменные, сняты с `cab.basisport.ru`
  (`brand.navy #1b3360` — шапка, `brand.sky #2fbaec` — акцент на синем, `accent #1668a8` — на белом).
  Шкала `ink` в светлой теме перевёрнута (100 — тёмный текст, 900 — белая поверхность).
  Контраст проверяем расчётом (`node tools/contrast_check.mjs`): второстепенный текст не ниже
  4.5:1 — в худшей точке стекла ink-400 = 4.59:1, ink-300 = 6.28:1, ink-100 = 15.9:1,
  белый на шапке = 7.4:1; из-за полупрозрачности шапки `text-white/60` поднят до `text-white/75`.
- **«Почти реальное время»**: сокета нет (данные из 1С приходят раз в N минут). Фронт раз в
  15 с опрашивает `GET /api/meta/freshness` (когда вкладка скрыта — не опрашивает), а как только
  меняется `data_as_of`, сам перезапрашивает метрики. Полная перезагрузка дашборда — по
  `ui.refresh_seconds` (5…900, по умолчанию 60). `stale` на бэкенде = успешного синка не было
  дольше 2.5 интервалов; в шапке тогда висит бейдж «данные устарели».
- **Логотип БазисПорта** — `frontend/public/logo.svg` (светлые фоны) и `logo-light.svg`
  (navy-шапка и синяя панель входа). Оригинал на тёмном фоне не читается: `#063058` по `#1b3360`.
- **`space-y-*` и `fixed` не дружат**: у панели деталей `top` считается от CSS-переменной
  `--header-h` (её публикует `AppShell` через `ResizeObserver`), а сам элемент получает
  `!mt-0` — иначе `space-y-5` у `<main>` добавляет соседям `margin-top:20px`, и наклонная
  панель уезжает на 20px ниже шапки (селектор `space-y-*` специфичнее `.mt-0`, нужен `!`).
- **Постановка судов — не витрина**: таблицы `berths`/`vessel_placements` синхронизация 1С
  не трогает. Время кратное слоту (00/06/12/18), `ends_at` — начало последнего занятого слота
  (включительно), пересечение на одной дорожке = 409. Правила в `app/services/vessels.py`.
- **В Excel-плане суда — это фигуры, а не ячейки**: в `26 05 ПОСТАНОВКА.xlsx` все названия
  судов лежат в `xl/drawings/drawing1.xml` (`twoCellAnchor` col/row), значения ячеек пустые.
  Открыть такой файл openpyxl-ом и увидеть план нельзя — нужен разбор якорей фигур.
- **`backdrop-filter` + `position: fixed` не дружат**: у `.panel` стоит `backdrop-filter`,
  поэтому карточка-родитель становится containing block для `fixed` — модалка, вложенная
  в карточку админки, запиралась внутри неё (310px вместо экрана) и обрастала чужими
  ползунками. Все диалоги рендерятся через `createPortal(…, document.body)` (см. `Modal`
  в `components/admin/ui.tsx`), окно — flex-колонка с прокруткой только в середине, кнопки
  формы — в `footer`-пропсе, `body` на время открытия блокируется от прокрутки.
- **Питфалли фронтенда**: правки в `tailwind.config.js` dev-сервер подхватывает не всегда —
  после смены палитры перезапускать vite и чистить `node_modules/.vite`, иначе браузер
  отдаёт старый CSS и проверки «по скриншотам» врут. Проверять вычисленными стилями
  (`getComputedStyle`), а не глазами. Браузерные смоки ходят на :5173 (`BASE=...`),
  старый `vite preview` на :4173 — мёртвая сборка, его держать поднятым нельзя.
- Файлы/переменные — snake_case (Python), kebab/camel (TS); комментарии и UI-текст — по-русски.

## Commands
| Command | Description |
|---|---|
| `cd backend && .venv/Scripts/python.exe -m pytest` | тесты авторизации и доступа |
| `cd backend && uvicorn app.main:app --reload --port 8000` | dev-бэкенд |
| `cd frontend && npm run dev` | dev-фронт (проксирует `/api` на :8000) |
| `cd frontend && npm run build` | typecheck (`tsc -b`) + прод-сборка |
| `docker compose up -d --build` | полный стек |
| `curl -X POST .../api/admin/sync/run -H "Authorization: Bearer <token>"` | ручной запуск загрузки из 1С |
| `curl -X POST .../api/admin/onec/test -H "Authorization: Bearer <token>"` | проверка связи с 1С (4 GET, ошибки в ответе) |
| `node tools/ui_smoke.mjs` | браузерная проверка входа и дашборда |
| `node tools/ui_customization.mjs` | браузерная проверка drag&drop, отчётов и CSV |
| `node tools/ui_drilldown.mjs` | браузерная проверка кликабельных графиков и панели деталей |
| `node tools/ui_admin.mjs` | браузерная проверка админки (нужны `ADMIN_LOGIN` и `PASS`) |
| `node tools/ui_vessels.mjs` | браузерная проверка «Постановки судов»: создание из UI, конфликт дорожек, любой период, права |
| `node tools/ui_live.mjs` | «почти реальное время»: опрос свежести, авто-перезапрос после нового среза 1С (идёт ~2 мин — ждёт перехода минуты) |
| `node tools/ui_glass_chart.mjs` | проверка стиля вычисленными свойствами: стекло, тултип, анимации, `prefers-reduced-motion` |
| `node tools/contrast_check.mjs` | расчёт WCAG-контраста для стекла (скриншоты не нужны) |

**После браузерных смоков чистим dev-БД** — тесты создают учётки `qa*`/`zz*` и склады `QA-*`:
```sql
delete from users where login like 'qa%' or login like 'zz%';
delete from warehouses where code like 'QA-%';
delete from app_settings;
```
`tools/ui_customization.mjs` перед прогоном сам сбрасывает сохранённую раскладку администратора
через `DELETE /api/settings/preferences/dashboard_layout` — иначе проверки размеров и порядка
плашек зависят от того, что осталось от прошлого прогона (это и было причиной «плавающих» провалов).

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
