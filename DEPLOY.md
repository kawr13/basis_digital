# Выкат на сервер без .env

Кабинет поднимается **одной командой** — `./deploy.sh`. Скрипт сам генерирует
секреты, сам всё подставляет и сам запускает контейнеры. **Файл `.env` в
репозитории не появляется никогда** (`.env` в `.gitignore`, в git попадает
только `.env.example` как справочник). Ниже есть и ручная дорожка через
`export` — если хочется контролировать каждую переменную.

## 1. Пакеты и доступы

- Docker Engine + compose plugin (`docker compose version`).
- Открытые наружу порты: только `WEB_PORT` (по умолчанию 8080). PostgreSQL
  и бэкенд наружу не смотрят — у сервиса `db` портов нет вовсе.

## 2. Выкат одной командой

```bash
git clone https://github.com/kawr13/basis_digital.git
cd basis_digital
./deploy.sh
```

Готово: скрипт выведет адрес, логин `admin` и пароль. Смените пароль при
первом входе.

Что делает `deploy.sh`:
- проверяет docker и compose;
- генерирует `POSTGRES_PASSWORD`, `SECRET_KEY`, `ADMIN_PASSWORD` **один раз**
  и кладёт их в файл ВНЕ репозитория — `~/.config/basis_digital/deploy.env`
  (chmod 600). При обновлении переиспользует: пароль БД нельзя менять на
  ходу, иначе он разойдётся с уже инициализированным томом `pgdata`;
- `CORS_ORIGINS` не трогает: фронт и API на одном адресе (nginx проксирует
  `/api/`), запросы однопоточные по origin — CORS в проде не участвует;
- `COOKIE_SECURE=false` для HTTP (на `https://` сервере — `./deploy.sh --https`).

Ключи: `--port 9000` (внешний порт), `--https` (сервер за HTTPS), `--help`.

---

## 3. Ручная дорожка (если без скрипта)

Только две переменные по-настоящему обязательны — без них compose не стартует.

```bash
export POSTGRES_PASSWORD="$(openssl rand -hex 16)"   # пароль БД
export SECRET_KEY="$(openssl rand -hex 32)"          # подпись JWT
export ADMIN_PASSWORD="$(openssl rand -hex 8)"       # пароль админа при первом старте
export COOKIE_SECURE=false                           # HTTP-сервер; при HTTPS — не задавать
docker compose up -d --build
```

| переменная | зачем | по умолчанию |
|---|---|---|
| `POSTGRES_PASSWORD` | пароль PostgreSQL, **обязательна** | — (compose не стартует) |
| `SECRET_KEY` | подпись токенов, **обязательна** | — (compose не стартует) |
| `ADMIN_PASSWORD` | пароль `admin` при первом запуске (иначе случайный — в лог) | пусто |
| `COOKIE_SECURE` | `false` для HTTP; пусто = «Secure при ENVIRONMENT=prod» | пусто |
| `WEB_PORT` | порт кабинета на сервере | `8080` |
| `UVICORN_WORKERS` | воркеры бэкенда | `2` |
| `ONE_C_SOURCE` | `mock` (витрина на заглушке) или `http` (боевая 1С, см. `docs/03`) | `mock` |

`CORS_ORIGINS` в проде не нужен (тот же origin). Нужен только если фронт
отдельно от бэкенда — тогда укажите адрес браузера.

## 4. Проверить запуск

```bash
docker compose ps                                   # все три сервиса Up, db — healthy
docker compose logs backend | grep администратор     # пароль первого админа (ручная дорожка)
```

Кабинет — на `http://<сервер>:8080`. Вход: `admin` + `ADMIN_PASSWORD`
(смените при первом входе).

## 5. Обновление

```bash
cd basis_digital
git pull
./deploy.sh            # секреты переиспользуются, пересоберутся только новые слои
```

Данные БД живут в томе `pgdata`, данные кабинета — в `statcab_data`;
`git pull` и пересборка их не трогают.

## 6. Бэкап и откат

```bash
docker compose exec db pg_dump -U statcab statcab | gzip > statcab-$(date +%F).sql.gz
# откат кода:
git log --oneline -5
git checkout <коммит>
docker compose up -d --build
```

## 7. Не заводится

| симптом | куда смотреть |
|---|---|
| `POSTGRES_PASSWORD is missing a value` | вы не через `./deploy.sh` и не экспортировали переменные |
| 401 сразу после логина на HTTP | не хватает `COOKIE_SECURE=false` (это делает `./deploy.sh` без `--https`) |
| `db` не становится healthy | `docker compose logs db` |

Подробности (ресурсы, HTTPS, nginx перед контейнером) — `docs/06_деплой_на_сервер.md`.
