#!/usr/bin/env bash
#
# deploy.sh — поднять Basis.Digital одной командой, повторный запуск безопасен.
#
#   ./deploy.sh                    # собрать и запустить (HTTP-сервер)
#   ./deploy.sh --https            # сервер за HTTPS: refresh-cookie с Secure
#   ./deploy.sh --http             # сервер по HTTP без TLS: Secure снять
#   ./deploy.sh --port 9000        # внешний порт кабинета (по умолчанию 8080)
#   ./deploy.sh --with deploy/nginx/docker-compose.host-nginx.yml
#                                  # overlay (TLS на хостовом nginx, Caddy и т.п.);
#                                  # запоминается и применяется при каждом запуске
#   ./deploy.sh --fix-db-password  # то же самое в лоб: согласовать пароль в базе
#                                  #   с секретом (обычно не нужно — ./deploy.sh
#                                  #   делает это сам)
#   ./deploy.sh --no-auto-fix      # найти расхождение пароля, но не менять его
#   ./deploy.sh --show-password    # показать пароль администратора
#   ./deploy.sh --check            # только проверки, ничего не пересобирать
#   ./deploy.sh --help
#
# Что делает и почему именно так:
#
# 1. Секреты ищет в порядке: $BASIS_DIGITAL_HOME → /etc/basis_digital →
#    ~/.config/basis_digital. Первый найденный файл и есть «настоящий».
#    Раньше путь зависел только от $HOME: запуск того же сервера из-под root и из-под
#    своего пользователя создавал ВТОРОЙ файл с НОВЫМ POSTGRES_PASSWORD, а том
#    pgdata оставался со старым → бэкенд умирал на старте с
#    «password authentication failed for user», nginx отвечал 502, а в браузере это
#    выглядело как «пропали пользователи».
#
# 2. POSTGRES_PASSWORD, SECRET_KEY и ADMIN_PASSWORD генерируются один раз и больше
#    не меняются. Пароль БД нельзя подменять на ходу: PostgreSQL применяет его
#    только при инициализации пустого тома.
#
# 3. WEB_PORT, HTTPS-режим и overlay-файлы compose хранятся в том же файле секретов
#    и подставляются, если ключ не передан флагом. Иначе `./deploy.sh` без флагов
#    молча возвращал порт 8080 и снимал overlay — кабинет за хостовым nginx снова
#    публиковал 0.0.0.0:8080 рядом с HTTPS.
#
# 4. ДО сборки проверяется, что POSTGRES_PASSWORD из секрета действительно подходит
#    к тому pgdata — по IP контейнера базы, то есть из той же подсети и по тому же
#    scram, что и приложение. К 127.0.0.1 внутри контейнера pg_hba часто пускает по
#    trust, и проверка на этом пути врёт (так и получили ложное «ок» при мёртвом
#    бэкенде). Рядом прогоняется заведомо неверный пароль: пустит и с ним — значит
#    путь trust, и скрипт честно скажет, что проверить не может, а не соврёт «ок».
#    Расхождение чинится автоматически ALTER USER-ом (том с данными не трогается);
#    --no-auto-fix отключает починку. Хелсчек `pg_isready` индикатором не является:
#    для него «сервер ответил» уже равно «healthy», даже если аутентификация не прошла.
#
# 5. Рядом ищутся тома pgdata из ДРУГИХ папок проекта. Имя compose-проекта = имя
#    папки, поэтому запуск из соседней директории поднимает пустую базу и выглядит
#    как потеря данных.
#
# 6. После запуска ждёт зелёного /healthz через nginx (то есть проверяется вся
#    цепочка web → backend → PostgreSQL), а не просто «контейнеры в Up».
#
# 7. Сверяет POSTGRES_PASSWORD внутри работающего контейнера backend с секретом:
#    compose считает контейнер актуальным, пока не изменилось описание сервиса, а
#    переменная из --env-file в нём могла остаться от прошлой жизни. Расхождение —
#    пересоздаём контейнер, а не подгоняем пароль базы под него.
#
# .env в репозитории не появляется никогда: файл секретов лежит вне репозитория.
#
set -euo pipefail

cd "$(dirname "$0")"

# справка — шапка этого файла. Одна проверка if/else обязательна: два отдельных
# правила со /^#/ не годятся, потому что sub() меняет $0, и второе правило видит
# уже не комментарий и выходит на второй же строке
usage() { awk 'NR>1 { if (/^#/) { sub(/^# ?/, ""); print } else exit }' "$0"; }

say()  { printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }
die()  { printf '✖ %s\n' "$*" >&2; exit 1; }

# --- флаги -------------------------------------------------------------------
ACTION="deploy"
FORCE_DB_FIX=0
AUTO_FIX=1
SHOW_PASSWORD=0
HTTPS_FLAG=""       # пусто = не указано; 1 = --https; 0 = --http
PORT_FLAG=""
OVERRIDE_FLAGS=""

while [ $# -gt 0 ]; do
  case "$1" in
    --https)        HTTPS_FLAG=1; shift;;
    --http)         HTTPS_FLAG=0; shift;;
    --port)         PORT_FLAG="${2:?--port требует значение}"; shift 2;;
    --port=*)       PORT_FLAG="${1#*=}"; shift;;
    --with)         OVERRIDE_FLAGS="$OVERRIDE_FLAGS,${2:?--with требует путь к compose-файлу}"; shift 2;;
    --with=*)       OVERRIDE_FLAGS="$OVERRIDE_FLAGS,${1#*=}"; shift;;
    --fix-db-password) FORCE_DB_FIX=1; shift;;
    --no-auto-fix)      AUTO_FIX=0; shift;;
    --show-password)   SHOW_PASSWORD=1; shift;;
    --check)           ACTION="check"; shift;;
    -h|--help)      usage; exit 0;;
    *)              die "Неизвестный ключ: $1 (справка: ./deploy.sh --help)";;
  esac
done

# --- 1. Docker ---------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "Нужен Docker. Установка: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "Нужен плагин docker compose (v2)."
docker info >/dev/null 2>&1 || die "Docker не отвечает. Запущен демон? (sudo systemctl start docker)"

# --- 2. Файл секретов: найти настоящий, а не создать новый -------------------
# Порядок важен: /etc не зависит от пользователя, поэтому один и тот же сервер,
# обслуженный то из-под root, то из-под своей учётки, видит ОДИН секрет.
find_secret_file() {
  local c candidates=""
  # BASIS_DIGITAL_HOME задан явно — значит читаем и пишем только там: иначе флаг
  # «переезда» не работает, и файл молча создастся в $HOME другого пользователя
  if [ -n "${BASIS_DIGITAL_HOME:-}" ]; then
    printf '%s/deploy.env' "$BASIS_DIGITAL_HOME"
    return 0
  fi
  candidates="/etc/basis_digital/deploy.env $HOME/.config/basis_digital/deploy.env"
  for c in $candidates; do
    if [ -f "$c" ]; then printf '%s' "$c"; return 0; fi
  done
  # файла ещё нет — пишем туда, куда дадут права: root в /etc, остальные в $HOME
  if [ "$(id -u)" = "0" ]; then
    printf '/etc/basis_digital/deploy.env'
  else
    printf '%s/.config/basis_digital/deploy.env' "$HOME"
  fi
}

SECRET_FILE="$(find_secret_file)"
SECRET_DIR="$(dirname "$SECRET_FILE")"
CREATED_SECRETS=0

# Значения в файле — без пробелов (списки через запятую), потому что файл ещё и
# источится через `set -a; . file`: VAR=a b в Shell означает «запустить команду b».
secret_get() { sed -n "s|^$1=||p" "$SECRET_FILE" 2>/dev/null | tail -n 1; }

secret_set() {
  local key="$1" val="$2" tmp
  # временный файл рядом с целевым: mv остаётся переименованием в пределах одной
  # файловой системы (атомарно) и не зависит от прав на /tmp
  tmp="$(mktemp "${SECRET_FILE}.XXXXXX")"
  if grep -q "^$key=" "$SECRET_FILE" 2>/dev/null; then
    sed "s|^$key=.*|$key=$val|" "$SECRET_FILE" > "$tmp"
  else
    cat "$SECRET_FILE" > "$tmp"
    printf '%s=%s\n' "$key" "$val" >> "$tmp"
  fi
  chmod 600 "$tmp"
  mv "$tmp" "$SECRET_FILE"
}

gen() { openssl rand -hex "${1:-16}"; }

if [ -f "$SECRET_FILE" ]; then
  say "▶ Секреты: $SECRET_FILE"
else
  mkdir -p "$SECRET_DIR"; chmod 700 "$SECRET_DIR"
  {
    echo "# Секреты Basis.Digital. НЕ для git. Создано deploy.sh $(date -Is)."
    echo "# POSTGRES_PASSWORD менять нельзя: PostgreSQL применяет его только к пустому тому."
  } > "$SECRET_FILE"
  chmod 600 "$SECRET_FILE"
  secret_set POSTGRES_PASSWORD "$(gen 16)"
  secret_set SECRET_KEY "$(gen 32)"
  secret_set ADMIN_PASSWORD "$(gen 8)"
  CREATED_SECRETS=1
  say "▶ Секреты созданы: $SECRET_FILE (chmod 600)"
fi

# --- 3. Параметры запуска: флаг → файл секретов → значение по умолчанию ------
# Храним их рядом с секретами, чтобы повторный `./deploy.sh` без флагов повторял
# прошлый запуск, а не откатывал порт и overlay.
if [ -n "$HTTPS_FLAG" ]; then secret_set HTTPS "$HTTPS_FLAG"; fi
HTTPS="$(secret_get HTTPS)"; HTTPS="${HTTPS:-0}"
if [ "$HTTPS" = "1" ]; then
  COOKIE_SECURE=""          # пусто = Secure при ENVIRONMENT=prod
else
  COOKIE_SECURE="false"     # HTTP-сервер по IP: иначе браузер не вернёт cookie
fi

if [ -n "$PORT_FLAG" ]; then secret_set WEB_PORT "$PORT_FLAG"; fi
WEB_PORT="$(secret_get WEB_PORT)"; WEB_PORT="${WEB_PORT:-8080}"

# overlay-файлы: список через запятую, дубликаты отбрасываем. Опечатка в пути —
# останов, а не «запомнили и мучайтесь»: docker compose на отсутствующем -f
# падает молча, а ключ к тому времени уже был бы записан в секреты надолго
OVERRIDES="$(secret_get COMPOSE_OVERRIDES)"
if [ -n "$OVERRIDE_FLAGS" ]; then
  for f in ${OVERRIDE_FLAGS//,/ }; do
    f="${f#,}"
    [ -n "$f" ] || continue
    [ -f "$f" ] || die "overlay $f не найден в $PWD — проверь путь (./deploy.sh --help)"
    case ",$OVERRIDES," in
      *",$f,") : ;;
      *) OVERRIDES="${OVERRIDES:+$OVERRIDES,}$f";;
    esac
  done
  secret_set COMPOSE_OVERRIDES "$OVERRIDES"
fi

COMPOSE_ARGS=(--env-file "$SECRET_FILE" -f docker-compose.yml)
for f in ${OVERRIDES//,/ }; do
  [ -n "$f" ] || continue
  [ -f "$f" ] || die "В $SECRET_FILE записан overlay $f, которого больше нет в $PWD.
   Удали строку COMPOSE_OVERRIDES из файла секретов (или верни файл) и повтори."
  COMPOSE_ARGS+=(-f "$f")
done
compose() { docker compose "${COMPOSE_ARGS[@]}" "$@"; }

PG_USER="$(secret_get POSTGRES_USER)";  PG_USER="${PG_USER:-statcab}"
PG_DB="$(secret_get POSTGRES_DB)";      PG_DB="${PG_DB:-statcab}"
PG_PW="$(secret_get POSTGRES_PASSWORD)"
ADMIN_LOGIN="$(secret_get ADMIN_LOGIN)"; ADMIN_LOGIN="${ADMIN_LOGIN:-admin}"
ADMIN_PASSWORD="$(secret_get ADMIN_PASSWORD)"

if [ -z "$PG_PW" ]; then
  die "В $SECRET_FILE нет POSTGRES_PASSWORD. Дописать вручную — безопасно; удалить файл и создать заново — способ потерять доступ к существующей базе"
fi

# --- 4. Чужие тома: «данные пропали» чаще значит «смотрим не оттуда» ---------
warn_foreign_volumes() {
  local v dir here found=0
  here="$(readlink -f "$PWD" 2>/dev/null || printf '%s' "$PWD")"
  for v in $(docker volume ls -q --filter name=pgdata 2>/dev/null); do
    dir="$(docker volume inspect "$v" --format '{{ index .Labels "com.docker.compose.project.working_dir" }}' 2>/dev/null || true)"
    [ -n "$dir" ] || continue
    dir="$(readlink -f "$dir" 2>/dev/null || printf '%s' "$dir")"
    # свой том и том проекта из уже удалённой папки не интересуют: во первом
    # мы и так живём, второго на диске нет — предупреждение было бы шумом
    if [ "$dir" != "$here" ] && [ -d "$dir" ]; then
      warn "⚠ Том $v создан проектом из $dir"
      found=1
    fi
  done
  if [ "$found" = 1 ]; then
    warn "  Если кабинет раньше работал из той папки — данные там, а не здесь."
    warn "  Имя compose-проекта = имя папки, от него зависит имя тома pgdata."
  fi
}
warn_foreign_volumes

# --- 5. Проверка пароля PostgreSQL -------------------------------------------
db_running() { [ -n "$(compose ps --status running -q db 2>/dev/null)" ]; }

start_db() {
  compose up -d db >/dev/null 2>&1 || compose up -d db
  local i
  for i in $(seq 1 30); do
    if compose exec -T -e PGUSER="$PG_USER" -e PGDATABASE="$PG_DB" db pg_isready -q 2>/dev/null; then
      return 0
    fi
    sleep 2
  done
  warn "--- последние строки лога базы ---"
  compose logs --tail=20 db 2>&1 || true
  die "PostgreSQL не принимает соединения (см. лог выше)"
}

# Проверка идёт по IP контейнера базы, а не к 127.0.0.1: к localhost pg_hba.conf
# часто пускает по trust, и «пароль подходит» на trust — ложь (именно так и
# получили ложное «ок» при мёртвом бэкенде). Приложение приходит из другой подсети,
# для него работает scram — тот же путь проверяем и мы.
db_target_host() {
  local h
  h="$(compose exec -T db hostname -i 2>/dev/null | awk '{print $1}' || true)"
  DB_HOST="${h:-127.0.0.1}"
}

psql_try() {   # $1 — пароль; 0 = пустило
  compose exec -T -e PGPASSWORD="$1" -e PGUSER="$PG_USER" -e PGDATABASE="$PG_DB" \
    db psql -w -h "$DB_HOST" -tAc 'select 1' >/dev/null 2>&1
}

db_password_ok() { psql_try "$PG_PW"; }

# Негативный контроль: пускает и с заведомо неверным паролем — значит путь идёт по
# trust и проверять тут нечего. Вместо «ок» скажем прямо, что проверка невозможна.
db_check_meaningful() { ! psql_try "nope-$RANDOM-$RANDOM"; }

# Основной путь починки: local-сокет в официальном образе обычно trust → пускает без
# пароля, и мы ставим в базе тот пароль, что лежит в секрете. Данные не трогаются.
db_fix_via_socket() {
  compose exec -T -e PGUSER="$PG_USER" db psql -w -U "$PG_USER" -d "$PG_DB" \
    -c "ALTER USER \"$PG_USER\" WITH PASSWORD '$PG_PW';" </dev/null >/dev/null 2>&1
}

# Запасной путь: local тоже под паролем. Временно ставим свою строку trust в начало
# pg_hba, меняем пароль, конфиг возвращаем — обратный ход безусловен, иначе
# оставим сервер с trust.
db_fix_via_pghba() {
  local conf rc=1
  conf="$(compose exec -T -e PGUSER="$PG_USER" db psql -w -tAc 'show hba_file' 2>/dev/null | tr -d '\r\n' || true)"
  case "$conf" in /*) : ;; *) conf="/var/lib/postgresql/data/pg_hba.conf";; esac
  say "▶ local в pg_hba.conf под паролем — временно включаю trust, меняю пароль, возвращаю конфиг…"
  compose exec -T db sh -c "cp -p '$conf' '$conf.deploybak' && { printf 'local all all trust\n'; cat '$conf'; } > '$conf.tmp' && mv '$conf.tmp' '$conf'" || return 1
  compose exec -T db sh -c 'kill -HUP 1' >/dev/null 2>&1 || true
  sleep 2
  if db_fix_via_socket; then rc=0; fi
  compose exec -T db sh -c "mv -f '$conf.deploybak' '$conf'; kill -HUP 1" >/dev/null 2>&1
  sleep 1
  return $rc
}

# Пароль в томе и в секрете обязаны совпадать. Расхождение чинится само: PostgreSQL
# применяет POSTGRES_PASSWORD только к пустому тому, а том уже с данными, поэтому
# единственный безопасный ход — ALTER USER (том не трогается, данные не трогает).
ensure_db_password() {
  if ! db_running; then
    if [ "$ACTION" = "check" ]; then
      say "▶ PostgreSQL сейчас не запущен — проверку пароля пропускаю"
      return 0
    fi
    start_db
  fi
  db_target_host
  if [ "$FORCE_DB_FIX" != 1 ] && db_password_ok; then
    if db_check_meaningful; then
      say "▶ PostgreSQL: пароль из секрета подходит"
    else
      warn "⚠ pg_hba.conf пускает по trust — проверить пароль снаружи невозможно."
      warn "  Если бэкенд упадёт с «password authentication failed» — разбор в DEPLOY.md, §7."
    fi
    return 0
  fi
  if [ "$ACTION" = "check" ]; then
    warn "⚠ Пароль в томе pgdata НЕ совпадает со значением в $SECRET_FILE."
    warn "  Обычный ./deploy.sh приведёт его в порядок сам (ALTER USER), данные не тронет."
    return 1
  fi
  warn "Пароль PostgreSQL в томе не совпадает со значением в $SECRET_FILE — привожу к секрету."
  warn "Так бывает, когда секреты пересоздали (другой пользователь, другой сервер,"
  warn "удалённый файл), а том с данными остался старым. Данные целы: меняется только"
  warn "пароль учётки, том никто не трогает."
  if [ "$AUTO_FIX" = 1 ] || [ "$FORCE_DB_FIX" = 1 ]; then
    # || true на вызове обязателен: внутри этих функций есть заведомо падающие
    # команды (psql без пароля, когда local под паролем), а set -e иначе убивает
    # скрипт на полуслове — и pg_hba.conf остался бы с временным trust
    db_fix_via_socket || db_fix_via_pghba || true
    if db_password_ok; then
      say "✔ Пароль в базе приведён к значению из $SECRET_FILE"
      return 0
    fi
  fi
  die "Не удалось согласовать пароль PostgreSQL с $SECRET_FILE (данные в томе не тронуты).
   1) можно взять POSTGRES_PASSWORD из старого deploy.env:
      find / -name deploy.env -not -path '/proc/*' 2>/dev/null -exec ls -la {} \;
      — и повторить ./deploy.sh;
   2) или посмотреть, что отвечает база: docker compose logs db"
}

ensure_db_password

if [ "$ACTION" = "check" ]; then
  say "✔ Проверки пройдены. Порт: $WEB_PORT, HTTPS: $HTTPS, overlay: ${OVERRIDES:-нет}"
  exit 0
fi

# --- 6. Сборка и запуск ------------------------------------------------------
say "▶ Сборка и запуск (порт $WEB_PORT, $([ "$HTTPS" = "1" ] && echo HTTPS || echo HTTP), overlay: ${OVERRIDES:-нет})…"
set -a; . "$SECRET_FILE"; set +a
export WEB_PORT COOKIE_SECURE
# CORS_ORIGINS не ставим: фронт и API на одном адресе (nginx проксирует /api/),
# запросы однопоточные по origin — CORS в проде не участвует.
compose up -d --build

# env внутри работающего контейнера мог достаться от прошлой жизни: compose считает
# контейнер актуальным, пока не изменилось описание сервиса. Сравниваем пароль,
# который реально внутри, с секретом, и при расхождении пересоздаём контейнер.
BACKEND_ID="$(compose ps -q backend 2>/dev/null || true)"
if [ -n "$BACKEND_ID" ]; then
  BACKEND_PW="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$BACKEND_ID" 2>/dev/null | sed -n 's/^POSTGRES_PASSWORD=//p' | head -n 1 || true)"
  if [ -n "$BACKEND_PW" ] && [ "$BACKEND_PW" != "$PG_PW" ]; then
    say "▶ В работающем контейнере backend другой POSTGRES_PASSWORD — пересоздаю…"
    compose up -d --force-recreate backend
  fi
fi

# --- 7. Ждём живой кабинет, а не «контейнеры в Up» ---------------------------
probe() {
  if command -v curl >/dev/null 2>&1; then curl -fsS -o /dev/null "$1" 2>/dev/null
  else wget -q -O /dev/null "$1" 2>/dev/null; fi
}
HEALTH_URL="http://127.0.0.1:$WEB_PORT/healthz"
# число попыток можно уменьшить переменной HEALTH_TRIES (по умолчанию 45 × 2 с)
HEALTH_OK=0
for i in $(seq 1 "${HEALTH_TRIES:-45}"); do
  if probe "$HEALTH_URL"; then HEALTH_OK=1; break; fi
  sleep 2
done
if [ "$HEALTH_OK" != 1 ]; then
  warn "--- backend, последние 30 строк ---"
  compose logs --tail=30 backend 2>&1 || true
  warn "--- статус ---"
  compose ps 2>&1 || true
  die "Кабинет не ответил на $HEALTH_URL за 90 секунд"
fi

# --- 8. Куда смотреть --------------------------------------------------------
IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
if [ -z "$IP" ]; then IP="<IP-сервера>"; fi
say ""
say "✔ Готово, /healthz отвечает. Кабинет: http://$IP:$WEB_PORT"
say "  Логин: $ADMIN_LOGIN"
if [ "$CREATED_SECRETS" = 1 ] || [ "$SHOW_PASSWORD" = 1 ]; then
  say "  Пароль: $ADMIN_PASSWORD"
  if [ "$CREATED_SECRETS" = 1 ]; then say "  Смени его при первом входе."; fi
else
  say "  Пароль: sed -n 's/^ADMIN_PASSWORD=//p' $SECRET_FILE"
fi
say ""
say "  Проверка:  docker compose ps && curl -I http://localhost:$WEB_PORT/"
say "  Логи:      docker compose logs -f backend"
say "  Стоп:      docker compose stop      (данные останутся в томах)"
say "  ВНИМАНИЕ:  docker compose down -v удалит тома вместе с данными"
