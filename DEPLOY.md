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

Готово: скрипт выведет адрес и логин `admin`, а пароль покажет **один раз** — при
первом запуске. Дальше его читать так:

```bash
sed -n 's/^ADMIN_PASSWORD=//p' /etc/basis_digital/deploy.env
```

Что делает `deploy.sh`:
- проверяет docker и compose;
- ищет файл секретов в порядке `$BASIS_DIGITAL_HOME` → `/etc/basis_digital/deploy.env`
  → `~/.config/basis_digital/deploy.env` (первый найденный и есть настоящий); если
  файла нет — создаёт: root пишет в `/etc/basis_digital`, обычный пользователь — в
  `~/.config/basis_digital`. Путь больше не зависит только от `$HOME`: раньше запуск
  одного и того же сервера то из-под root, то из-под своей учётки создавал **второй**
  файл с новым `POSTGRES_PASSWORD`, том `pgdata` оставался со старым паролем, и
  кабинет умирал на старте (в браузере — 502 и «пропали пользователи»);
- генерирует `POSTGRES_PASSWORD`, `SECRET_KEY`, `ADMIN_PASSWORD` **один раз** и больше
  их не меняет: пароль БД PostgreSQL применяет только к пустому тому;
- **до сборки** проверяет, что `POSTGRES_PASSWORD` из секретов действительно подходит к
  уже инициализированному тому `pgdata` — через TCP и scram, то есть ровно так, как
  будет подключаться бэкенд. Статус `db (healthy)` ничего не доказывает: `pg_isready`
  засчитывает ответ сервера даже при отказе аутентификации. Не подошло — останов с
  инструкцией, а не 502 в проде;
- предупреждает, если рядом найден том `pgdata` из другой папки: имя compose-проекта =
  имя папки, поэтому запуск «из соседней папки» поднимает пустую базу (см. §7);
- запоминает `WEB_PORT`, HTTPS-режим и overlay-файлы в том же файле секретов: повторный
  `./deploy.sh` без флагов повторяет прошлый запуск, а не возвращает порт 8080 и не
  снимает overlay с TLS (иначе кабинет за хостовым nginx снова публиковал `0.0.0.0:8080`);
- после запуска ждёт зелёного `/healthz` через nginx — то есть проверяется вся цепочка
  `web → backend → PostgreSQL`, — а не просто «контейнеры в Up»; при неудаче печатает
  лог бэкенда и статус;
- `CORS_ORIGINS` не трогает: фронт и API на одном адресе (nginx проксирует
  `/api/`), запросы однопоточные по origin — CORS в проде не участвует.

Ключи: `--http` / `--https`, `--port 9000`, `--with deploy/nginx/docker-compose.host-nginx.yml`
(overlay, запоминается), `--fix-db-password` (привести пароль в базе к секрету),
`--show-password`, `--check` (только проверки, ничего не пересобирать), `--help`.

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
| 502 на `/api/...`, при этом кабинет открылся | `docker compose logs backend` — почти всегда бэкенд упал на старте, nginx просто некому отвечать |
| `FATAL: password authentication failed for user "statcab"` | секреты разошлись с томом: файл секретов пересоздали (другой пользователь, другой сервер, удалённый файл), а `pgdata` остался со старым паролем. `./deploy.sh` ловит это до сборки. Лечится: найти старый `deploy.env` (`find / -name deploy.env -not -path '/proc/*' 2>/dev/null -exec ls -la {} \;`) и взять `POSTGRES_PASSWORD` оттуда, либо `./deploy.sh --fix-db-password` |
| «Пропали» пользователи / база стала пустой | **данные никуда не делись, вы не оттуда смотрите.** Имя compose-проекта = имя папки, том называется `<папка>_pgdata`: `docker volume ls`, затем `docker volume inspect <том> --format '{{ index .Labels "com.docker.compose.project.working_dir" }}'` и запускать `./deploy.sh` из той папки |
| `db` показан `healthy`, но бэкенд не подключается | `pg_isready` проверяет только «сервер отвечает»; аутентификацию — `docker compose logs backend` |
| `POSTGRES_PASSWORD is missing a value` | вы не через `./deploy.sh` и не экспортировали переменные |
| 401 сразу после логина на HTTP | не хватает `COOKIE_SECURE=false` (это делает `./deploy.sh` без `--https`) |
| `db` не становится healthy | `docker compose logs db` |

⚠️ **`docker compose down -v` удаляет тома вместе с данными.** Останавливать —
`docker compose stop`. Если том всё же удалён, остаются только дампы из §6.

Подробности (ресурсы, HTTPS, nginx перед контейнером) — `docs/06_деплой_на_сервер.md`.
