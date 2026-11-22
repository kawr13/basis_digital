#!/usr/bin/env bash
#
# deploy/backup.sh — бэкап базы кабинета StatCab (TASKS2.md, п. 22).
#
# Зачем отдельный скрипт: прежняя «бэкап-строка» из доки (pg_dump | gzip > file)
# молча писала пустой или битый файл при любой осечке pg_dump — без pipefail
# код gzip'а всегда был нулевой. Здесь дамп сначала пишется во временный файл,
# проверяется (pg_restore --list / PRAGMA integrity_check + минимальный размер)
# и только потом атомарно переносится на место. Ненулевой выход = «бэкапа нет»,
# cron заметит сам.
#
# Цели:
#   * PostgreSQL — рабочая база (в docker compose это сервис `db`);
#   * SQLite     — dev/демо-базы (backend/.env, data/statcab.db).
#
# Запуск:
#   deploy/backup.sh                 # обычный прогон (например из cron)
#   DRY_RUN=1 deploy/backup.sh       # только проверка доступности и настроек,
#                                    # живую базу и каталог не трогает
#   deploy/backup.sh --help
#
# Настройка — переменными окружения (дефолты под типовую установку из
# docs/06_деплой_на_сервер.md):
#   POSTGRES_HOST/PORT/USER/PASSWORD/DB  — как в .env compose-сервиса db;
#   SQLITE_PATH   — путь к файлу sqlite (dev-база кабинета);
#   BACKUP_DIR    — куда писать дампы;
#   RETENTION_DAYS— сколько дней хранить дампы (ротация по маске имени);
#   MIN_DUMP_BYTES— ниже этого размера дамп считается мусором;
#   BACKUP_TARGET — auto | postgres | sqlite (по умолчанию auto: всё, что есть).
#
# Пароль PostgreSQL лучше класть не в аргументы, а в окружение (PGPASSWORD)
# или в ~/.pgpass — в аргументах его видно в ps.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077   # дампы содержат персональные данные — не читаемы «для всех»

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---------- конфигурация (переопределяется окружением) ----------
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-statcab}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
POSTGRES_DB="${POSTGRES_DB:-statcab}"
SQLITE_PATH="${SQLITE_PATH:-$ROOT_DIR/data/statcab.db}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
MIN_DUMP_BYTES="${MIN_DUMP_BYTES:-1024}"
BACKUP_TARGET="${BACKUP_TARGET:-auto}"
DRY_RUN="${DRY_RUN:-0}"

# Имя дампа обязательно с датой-временем UTC и узнаваемым префиксом:
# по нему же ротация отличает «наши» файлы от чужих в том же каталоге.
STAMP="$(date -u +%Y%m%d-%H%M%S)"
DUMP_GLOB='statcab-*.dump.gz'

log()  { printf '[backup] %s\n' "$*"; }
warn() { printf '[backup] ВНИМАНИЕ: %s\n' "$*" >&2; }
# "$1", а не "$*": IFS=$'\n\t', и «$*» склеила бы сообщение с кодом переводами строк.
die()  { printf '[backup] ОТКАЗ: %s\n' "$1" >&2; exit "${2:-1}"; }

# ---------- аварийная уборка ----------
# Если умрём посередине, в BACKUP_DIR не должен остаться недописанный
# «дамп» — его потом легко принять за настоящий.
TMP_FILES=()
cleanup() {
    local f
    for f in "${TMP_FILES[@]:-}"; do
        # if, а не «&&»: падающий список последняя команда цикла —
        # иначе EXIT-трап сам триггерит ERR-трап и портит код возврата.
        if [[ -n "$f" && -e "$f" ]]; then rm -f "$f"; fi
    done
    return 0
}
on_err() {
    local rc=$?
    printf '[backup] ОШИБКА: «%s» на строке %s (код %s)\n' "$BASH_COMMAND" "$LINENO" "$rc" >&2
    cleanup
    return 0
}
trap on_err ERR
trap cleanup EXIT

usage() {
    cat <<'EOF'
deploy/backup.sh — бэкап базы кабинета StatCab (TASKS2.md, п. 22).

Запуск:
  deploy/backup.sh              — обычный прогон (cron); дамп пишется только
                                  прошедший проверку (pg_restore --list /
                                  PRAGMA integrity_check, минимальный размер).
  DRY_RUN=1 deploy/backup.sh    — проверить настройки и доступность, живую
                                  базу и каталог дампов не трогать.

Настройка окружением (дефолты — типовая установка из docs/06):
  POSTGRES_HOST/PORT/USER/PASSWORD/DB — как в .env compose-сервиса db;
  SQLITE_PATH                    — файл dev/демо-базы sqlite;
  BACKUP_DIR                     — куда писать дампы;
  RETENTION_DAYS                 — срок хранения дампов, дней;
  MIN_DUMP_BYTES                 — меньше этого размера дамп считается мусором;
  BACKUP_TARGET                  — auto | postgres | sqlite.

Код возврата 0 — дамп есть и он проверен; ненулевой — бэкапа нет.
EOF
}

# ---------- аргументы ----------
# Разбираем до проверки целей: --help обязан работать и на машине без БД.
case "${1:-}" in
    --help|-h) usage; exit 0 ;;
    "") ;;
    *) die "неизвестный аргумент '$1' (есть только --help)" 2 ;;
esac

# ---------- цели бэкапа ----------
# auto = «то, что реально найдено»; при явном BACKUP_TARGET отсутствующая
# цель — ошибка, а не тихий пропуск (молча «успешный» cron хуже падающего).
WANT_PG=0
WANT_SQLITE=0
case "$BACKUP_TARGET" in
    postgres) WANT_PG=1 ;;
    sqlite)   WANT_SQLITE=1 ;;
    auto)
        command -v pg_dump >/dev/null 2>&1 && WANT_PG=1 || true
        [[ -f "$SQLITE_PATH" ]] && WANT_SQLITE=1 || true
        ;;
    *) die "BACKUP_TARGET='$BACKUP_TARGET' — ожидалось auto|postgres|sqlite" 2 ;;
esac

if [[ $WANT_PG -eq 1 ]]; then
    command -v pg_dump    >/dev/null 2>&1 || die "BACKUP_TARGET=$BACKUP_TARGET: утилита pg_dump не найдена в PATH" 2
    command -v pg_restore >/dev/null 2>&1 || die "BACKUP_TARGET=$BACKUP_TARGET: утилита pg_restore не найдена в PATH (без неё дамп не проверить)" 2
fi
if [[ $WANT_SQLITE -eq 1 ]]; then
    command -v sqlite3 >/dev/null 2>&1 || die "BACKUP_TARGET=$BACKUP_TARGET: утилита sqlite3 не найдена в PATH" 2
    [[ -f "$SQLITE_PATH" ]] || die "SQLite-база не найдена: $SQLITE_PATH (задайте SQLITE_PATH или BACKUP_TARGET=postgres)" 2
fi
if [[ $WANT_PG -eq 0 && $WANT_SQLITE -eq 0 ]]; then
    die "бэкапить нечего: файла $SQLITE_PATH нет, а pg_dump не установлен. Живую базу кабинета задайте через POSTGRES_* (или BACKUP_TARGET=postgres), dev-базу — через SQLITE_PATH" 2
fi
if [[ $WANT_PG -eq 1 && -z "$POSTGRES_PASSWORD" ]]; then
    warn "POSTGRES_PASSWORD пуст — полагаюсь на ~/.pgpass или trust-аутентификацию"
fi

# ---------- PostgreSQL ----------
DONE_FILES=()
dump_postgres() {
    local dest="$BACKUP_DIR/statcab-pg-$STAMP.dump.gz"
    local list size
    # temp — в самом BACKUP_DIR: mv внутри одного ФС атомарен, и на чужом
    # ФС (разные тома /var и /backup) не «полуписнет» при not-supposed-to-exist.
    local tmp; tmp="$(mktemp "$BACKUP_DIR/.statcab-pg-XXXXXX")"
    TMP_FILES+=("$tmp")
    log "PostgreSQL: pg_dump $POSTGRES_USER@$POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB"
    # --format=custom — дамп самовсканируемый (pg_restore -l) и сжимается лучше
    # плосого SQL. Пишем в файл, а не в трубу: TOC у custom-format лежит в конце,
    # и чтение каталога из не-seekable-канала (gunzip -c | pg_restore --list) —
    # поведение на грани документированного; проверяем дамп как файл, gzip —
    # вторым слоем уже поверх, как просит дока по восстановлению.
    PAGER=cat PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
        --host="$POSTGRES_HOST" --port="$POSTGRES_PORT" \
        --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
        --format=custom --no-password \
        --file="$tmp"
    # Проверка «не битый и не пустой»: pg_restore читает TOC из файла.
    list="$(mktemp "$BACKUP_DIR/.statcab-pg-list-XXXXXX")"
    TMP_FILES+=("$list")
    pg_restore --list "$tmp" > "$list"
    [[ -s "$list" ]] || die "pg_restore --list вернул пустоту — дамп битый: $tmp"
    size=$(wc -c < "$tmp" | tr -d '[:space:]')
    if (( size < MIN_DUMP_BYTES )); then
        die "дамп всего $size байт (минимум $MIN_DUMP_BYTES) — похоже на пустышку, не публикуем"
    fi
    local gz="$tmp.gz"
    gzip -6 -c "$tmp" > "$gz"
    gzip -t "$gz"
    TMP_FILES+=("$gz")
    mv -f "$gz" "$dest"
    DONE_FILES+=("$dest")
}

# ---------- SQLite ----------
dump_sqlite() {
    local dest="$BACKUP_DIR/statcab-sqlite-$STAMP.dump.gz"
    local res size
    # .backup — горячее консистентное копирование через sqlite3 (WAL-безопасно);
    # cp живой базы ловит базу на середине транзакции.
    local tmp; tmp="$(mktemp "$BACKUP_DIR/.statcab-sqlite-XXXXXX")"
    TMP_FILES+=("$tmp")
    log "SQLite: $SQLITE_PATH"
    sqlite3 "$SQLITE_PATH" ".backup '$tmp'"
    # Проверка после — уже по копии: если копия побиталась, живой базе не верим.
    res="$(sqlite3 "$tmp" 'PRAGMA integrity_check;')"
    [[ "$res" == "ok" ]] || die "PRAGMA integrity_check копии: $res"
    local gz="$tmp.gz"
    gzip -6 -c "$tmp" > "$gz"
    gzip -t "$gz"
    TMP_FILES+=("$gz")
    size=$(wc -c < "$gz" | tr -d '[:space:]')
    if (( size < MIN_DUMP_BYTES )); then
        die "дамп всего $size байт (минимум $MIN_DUMP_BYTES) — похоже на пустышку, не публикуем"
    fi
    mv -f "$gz" "$dest"
    DONE_FILES+=("$dest")
}

# ---------- ротация ----------
# Трогаем только файлы со своим префиксом и датой в имени: в /var/backups
# statcab может лежать и дамп тома (.tgz) — его удалять нельзя.
rotate() {
    local n=0 f
    while IFS= read -r -d '' f; do
        rm -f "$f"
        n=$((n + 1))
    done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name "$DUMP_GLOB" -mtime +"$RETENTION_DAYS" -print0)
    ROTATED=$n
}

# ---------- dry-run: ничего не пишем, живую базу не трогаем ----------
if [[ "$DRY_RUN" == "1" ]]; then
    log "dry-run: только проверка, дампы писать не буду"
    if [[ $WANT_PG -eq 1 ]]; then
        log "  postgres: $POSTGRES_USER@$POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB -> $BACKUP_DIR/statcab-pg-$STAMP.dump.gz"
    fi
    if [[ $WANT_SQLITE -eq 1 ]]; then
        log "  sqlite:   $SQLITE_PATH -> $BACKUP_DIR/statcab-sqlite-$STAMP.dump.gz"
    fi
    if [[ $WANT_PG -eq 1 && "${DRY_RUN_SKIP_PING:-0}" != "1" ]] && command -v pg_isready >/dev/null 2>&1; then
        if PGPASSWORD="$POSTGRES_PASSWORD" pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q; then
            log "  postgres: сервер отвечает"
        else
            warn "postgres: $POSTGRES_HOST:$POSTGRES_PORT не отвечает — реальный прогон упадёт"
        fi
    fi
    if [[ -d "$BACKUP_DIR" && -w "$BACKUP_DIR" ]]; then
        log "  каталог $BACKUP_DIR доступен для записи"
    else
        die "каталог $BACKUP_DIR не существует или недоступен для записи" 2
    fi
    log "dry-run: всё в порядке"
    exit 0
fi

# ---------- основной путь ----------
mkdir -p "$BACKUP_DIR"
if [[ $WANT_PG -eq 1 ]]; then
    dump_postgres
fi
if [[ $WANT_SQLITE -eq 1 ]]; then
    dump_sqlite
fi

ROTATED=0
rotate

log "готово:"
for f in "${DONE_FILES[@]}"; do
    log "  $f ($(du -h "$f" | cut -f1))"
done
log "ротация: удалено $ROTATED шт. старше ${RETENTION_DAYS} дн. (маска $DUMP_GLOB)"
