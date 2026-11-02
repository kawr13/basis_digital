#!/usr/bin/env bash
#
# deploy.sh — поднять Basis.Digital одной командой, без ручного заполнения.
#
#   ./deploy.sh            # HTTP-сервер: сам сгенерирует секреты и запустит
#   ./deploy.sh --https    # сервер за HTTPS (cookie Secure, порт 80/443 спереди)
#   ./deploy.sh --port 9000   # свой внешний порт (по умолчанию 8080)
#
# Что делает:
#   1. Проверяет docker + compose.
#   2. Секреты (POSTGRES_PASSWORD, SECRET_KEY, ADMIN_PASSWORD) генерирует один
#      раз и кладёт в файл ВНЕ репозитория (~/.config/basis_digital/deploy.env,
#      chmod 600). При обновлении переиспользует их — пароль БД нельзя менять
#      на ходу, иначе он разойдётся с уже инициализированным томом pgdata.
#   3. docker compose up -d --build.
#
# .env в репозитории не появляется никогда: файл секретов лежит в домашней
# директории и в git не попадает.
#
set -euo pipefail

cd "$(dirname "$0")"

SECRET_DIR="${BASIS_DIGITAL_HOME:-$HOME/.config/basis_digital}"
SECRET_FILE="$SECRET_DIR/deploy.env"
WEB_PORT="8080"
COOKIE_SECURE="false"   # HTTP-сервер по IP: иначе браузер не вернёт cookie
HTTPS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --https) HTTPS=1; COOKIE_SECURE=""; shift;;
    --port)  WEB_PORT="${2:?--port требует значение}"; shift 2;;
    --port=*) WEB_PORT="${1#*=}"; shift;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "Неизвестный ключ: $1 (справка: ./deploy.sh --help)" >&2; exit 2;;
  esac
done

# --- 1. Docker ------------------------------------------------------------
command -v docker >/dev/null 2>&1 || {
  echo "Нужен Docker. Установка: https://docs.docker.com/engine/install/" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || {
  echo "Нужен плагин docker compose (v2)." >&2; exit 1; }
docker info >/dev/null 2>&1 || {
  echo "Docker не отвечает. Запущен демон? (sudo systemctl start docker)" >&2; exit 1; }

# --- 2. Секреты: сгенерировать один раз, дальше переиспользовать ----------
gen() { openssl rand -hex "${1:-16}"; }
if [ -f "$SECRET_FILE" ]; then
  echo "▶ Секреты: переиспользую $SECRET_FILE"
else
  mkdir -p "$SECRET_DIR"; chmod 700 "$SECRET_DIR"
  NEW_ADMIN="$(gen 8)"
  {
    echo "# Секреты Basis.Digital. НЕ для git. Создано deploy.sh $(date -Is)."
    echo "POSTGRES_PASSWORD=$(gen 16)"
    echo "SECRET_KEY=$(gen 32)"
    echo "ADMIN_PASSWORD=$NEW_ADMIN"
  } > "$SECRET_FILE"
  chmod 600 "$SECRET_FILE"
  echo "▶ Секреты созданы и сохранены: $SECRET_FILE (chmod 600)"
fi

# Достаём ADMIN_PASSWORD для показа (файл плоский KEY=value).
ADMIN_PASSWORD="$(sed -n 's/^ADMIN_PASSWORD=//p' "$SECRET_FILE")"

# --- 3. Запуск ------------------------------------------------------------
echo "▶ Сборка и запуск (порт $WEB_PORT, $([ "$HTTPS" = 1 ] && echo HTTPS || echo HTTP))…"
set -a; . "$SECRET_FILE"; set +a
export WEB_PORT COOKIE_SECURE
# CORS_ORIGINS не ставим: фронт и API на одном адресе (nginx проксирует /api),
# запросы однопоточные по origin — CORS в проде не участвует.
docker compose --env-file "$SECRET_FILE" up -d --build

# --- 4. Куда смотреть -----------------------------------------------------
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$IP" ] || IP="<IP-сервера>"
echo
echo "✔ Готово. Кабинет: http://$IP:$WEB_PORT"
echo "  Логин: admin"
echo "  Пароль: ${ADMIN_PASSWORD:-<в логе: docker compose logs backend>}"
echo "  Сменить пароль — при первом входе."
echo
echo "  Статус:  docker compose ps"
echo "  Логи:    docker compose logs -f backend"
echo "  Стоп:    docker compose down   (данные в томах останутся)"
