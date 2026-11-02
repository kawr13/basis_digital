# Выкат на сервер без .env

Кабинет собирается и живёт целиком из переменных окружения — **файл `.env`
не нужен ни на рабочей машине, ни на сервере** (в репозитории его нет и не
будет: `.env` в `.gitignore`, в git попадает только `.env.example` как
справочник). `docker compose` подставляет `${...}` из окружения оболочки,
поэтому достаточно один раз экспортировать переменные.

## 1. Пакеты и доступы

- Docker Engine + compose plugin (`docker compose version`).
- Открытые наружу порты: только `WEB_PORT` (по умолчанию 8080). PostgreSQL
  и бэкенд наружу не смотрят — у сервиса `db` портов нет вовсе.

## 2. Клонировать

```bash
git clone https://github.com/kawr13/basis_digital.git
cd basis_digital
```

## 3. Переменные окружения (вместо .env)

```bash
export POSTGRES_PASSWORD="$(openssl rand -hex 16)"   # пароль БД
export SECRET_KEY="$(openssl rand -hex 32)"          # подпись JWT
export ADMIN_PASSWORD="$(openssl rand -hex 8)"       # пароль админа при первом старте
export CORS_ORIGINS="http://<адрес-или-домен>:8080"  # откуда ходит браузер
export COOKIE_SECURE=false                           # сервер без HTTPS; при HTTPS — не задавать
```

| переменная | зачем | по умолчанию |
|---|---|---|
| `POSTGRES_PASSWORD` | пароль PostgreSQL, **обязательна** | — (compose не стартует) |
| `SECRET_KEY` | подпись токенов, **обязательна** | — (compose не стартует) |
| `ADMIN_PASSWORD` | пароль `admin` при первом запуске (печатается в лог) | случайный, тоже в лог |
| `CORS_ORIGINS` | адрес, с которого заходит браузер | `http://localhost:8080` |
| `COOKIE_SECURE` | `false` для HTTP; пусто = «Secure при ENVIRONMENT=prod» | пусто |
| `WEB_PORT` | порт кабинета на сервере | `8080` |
| `UVICORN_WORKERS` | воркеры бэкенда | `2` |
| `ONE_C_SOURCE` | `mock` (витрина на заглушке) или `http` (боевая 1С, см. `docs/03`) | `mock` |

Переменные живут до конца сессии оболочки. Чтобы не вводить их при каждом
ручном `docker compose ...`, держите их в `/etc/environment` сервиса или в
systemd-юните — но **не** в файле внутри репозитория.

## 4. Собрать и запустить

```bash
docker compose up -d --build
docker compose logs backend | grep администратор   # пароль первого админа
docker compose ps                                   # все три сервиса Up, db — healthy
```

Кабинет — на `http://<сервер>:8080`. Вход: `admin` + `ADMIN_PASSWORD`
(смените при первом входе).

## 5. Обновление

```bash
cd basis_digital
git pull
docker compose up -d --build    # пересоберёт только изменившиеся слои
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
| `POSTGRES_PASSWORD is missing a value` | переменная не экспортирована в этой оболочке |
| браузер не грузит, в консоли CORS | `CORS_ORIGINS` не совпадает с адресом браузера |
| 401 сразу после логина на HTTP | не хватает `COOKIE_SECURE=false` |
| `db` не становится healthy | `docker compose logs db` |

Подробности (ресурсы, HTTPS, nginx перед контейнером) — `docs/06_деплой_на_сервер.md`.
