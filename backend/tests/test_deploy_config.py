"""Конфигурация продакшена: `backend/Dockerfile`, `docker-compose.yml`, `.env.example`.

Ни живого Docker, ни compose-демона в тестах нет, а регресс отсюда бьёт по
пользователям сразу: кабинет стартует с одним воркером, молча возвращается на
медленный парсер h11, «проверка связи» с 1С вешает поток на две минуты или всё
упирается в `FATAL: too many connections`. Поэтому читаем текст конфигурации
как есть — по образцу `test_web_config.py`, который так же проверяет nginx.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DOCKERFILE = ROOT / "backend" / "Dockerfile"
REQUIREMENTS = ROOT / "backend" / "requirements.txt"
REQUIREMENTS_PROD = ROOT / "backend" / "requirements-prod.txt"
COMPOSE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"
HOST_NGINX_OVERLAY = ROOT / "deploy" / "nginx" / "docker-compose.host-nginx.yml"

# Переменные, без которых пункт 9 (воркеры и пул) и пункт 10 (таймаут пробы)
# просто не чем будет настроить: дефолты в коде — это дефолты, а не настройка.
NEW_VARS = ("UVICORN_WORKERS", "DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_RECYCLE_SEC",
            "ONE_C_PROBE_TIMEOUT_SEC", "TRUST_PROXY_HEADERS", "TRUST_PROXY_HOPS")


def _text(path: Path) -> str:
    assert path.is_file(), f"не найден файл конфигурации: {path}"
    # В рабочей копии строки могут быть с CRLF — на смысл это не влияет.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_uvicorn_cmd_is_shell_form_with_workers_and_httptools():
    """Exec-форма CMD не подставляет переменные, а дефолтный парсер — это h11."""
    text = _text(DOCKERFILE)
    cmd = re.search(r"(?m)^CMD[ \t]+(.+)$", text)
    assert cmd, "Dockerfile: CMD не найден"
    line = cmd.group(1)
    assert not line.lstrip().startswith("["), (
        "Dockerfile: CMD в exec-форме — docker не подставит ${UVICORN_WORKERS}, "
        "и воркеров всегда будет столько, сколько написано в образе"
    )
    assert "${UVICORN_WORKERS:-1}" in line, (
        "Dockerfile: воркеры должны браться из UVICORN_WORKERS с запасным значением 1: "
        "планировщик и блокировка синка живут в процессе, второй воркер — вторая "
        "синхронизация за интервал (п. 8 TASKS2.md)"
    )
    assert "--http httptools" in line, "Dockerfile: без флага uvicorn берёт медленный h11"
    assert re.match(r"exec[ \t]", line), (
        "Dockerfile: shell-форма CMD без exec оставляет PID 1 за /bin/sh — uvicorn "
        "не получит SIGTERM от `docker stop` и будет убит по таймауту"
    )
    assert "USER statcab" in text, "Dockerfile: кабинет смотрит в интернет — только не root"


def test_httptools_is_pinned_and_reaches_prod_requirements():
    """`requirements-prod.txt` тянет требования через -r, пин обязан дойти до образа."""
    pinned = re.findall(r"(?m)^httptools==(\S+)$", _text(REQUIREMENTS))
    assert pinned, "requirements.txt: httptools не запинен — образ соберётся наугад"
    assert re.fullmatch(r"\d+\.\d+(\.\d+)?", pinned[0]), f"странный пин: {pinned[0]}"
    assert re.search(r"(?m)^-r[ \t]+requirements\.txt\s*$", _text(REQUIREMENTS_PROD)), (
        "requirements-prod.txt должен подтягивать requirements.txt через -r"
    )


def test_compose_passes_tuning_variables_to_backend():
    """Без проброса в контейнер переменны из `.env` до приложения не доходят."""
    text = _text(COMPOSE)
    backend = text.split("  backend:", 1)[-1].split("\n  web:", 1)[0]
    for var in NEW_VARS:
        assert re.search(rf"(?m)^\s+{var}: \$\{{{var}:-", backend), (
            f"docker-compose.yml: в backend.environment нет {var} — настройка из .env "
            "до кабинета не дойдёт"
        )


def test_env_example_documents_new_variables():
    text = _text(ENV_EXAMPLE)
    for var in NEW_VARS:
        assert re.search(rf"(?m)^{var}=", text), f".env.example: не описана {var}"
    # число воркеров без формулы — это гадание, в файле обязано быть правило
    assert "vCPU" in text, ".env.example: нет подсказки, сколько воркеров ставить"


def test_backend_image_has_healthcheck():
    """Без HEALTHCHECK «жив и не отвечает» — не повод поднимать контейнер заново
    (`restart` смотрит только на «процесс умер»). С п. 19 TASKS2.md проба смотрит
    в /readyz: «процесс жив, а витрина протухла» — тоже неисправность. curl из
    образа убран: проба ходит python-ом, он в базовом образе есть. Токен в URL
    обязан экранироваться (дефект D2): токен вводится произвольной строкой, и
    пробел или «&» в нём ломали сам запрос пробы. 403 — здоров (дефект D3):
    иначе админ заводит токен в UI, а контейнер краснеет без объяснений."""
    text = _text(DOCKERFILE)
    assert re.search(r"(?m)^HEALTHCHECK\b", text), "Dockerfile: нет HEALTHCHECK"
    assert "/readyz" in text, "Dockerfile: проба должна смотреть в готовность, а не в живость"
    assert "p.quote(t,safe='')" in text, \
        "Dockerfile: токен в URL пробы не экранируется — спецсимволы ломают пробу (D2)"
    assert "200,403" in text, \
        "Dockerfile: 403 должен считаться «приложение живо», иначе токен из UI красит контейнер (D3)"


def test_probe_command_is_identical_in_image_and_compose():
    """healthcheck в compose перекрывает HEALTHCHECK образа: две версии одной
    команды неизбежно разъезжаются незаметно (на сервере работает одна, в CI
    и при `docker run` — другая). Держим их дословно равными (п. 19, дефект D2)."""
    docker_cmd = re.search(r'CMD python -c "([^"]+)"', _text(DOCKERFILE))
    assert docker_cmd, "Dockerfile: не найдена команда пробы в HEALTHCHECK"
    compose_line = re.search(r'(?m)^\s*test: \["CMD-SHELL", "(python -c .+)"\]', _text(COMPOSE))
    assert compose_line, "docker-compose.yml: не найдена команда пробы backend"
    # YAML экранирует двойные кавычки внутри строки: возвращаем их для сверки
    compose_cmd = re.search(r'python -c "([^"]+)"', compose_line.group(1).replace('\\"', '"'))
    assert compose_cmd, "docker-compose.yml: проба backend не в форме python -c \"…\""
    assert compose_cmd.group(1) == docker_cmd.group(1), \
        "команда пробы в compose и в образе разошлась: compose перекрывает образ, расхождение незаметно"
    # Равенство строк не ловит главного: это исходник Python, и лишняя кавычка
    # (`else '''))`) делает контейнер unhealthy навсегда, пока тест зелёный,
    # потому что обе строки идентичны. Компилируем ровно то, что исполнит docker.
    try:
        compile(docker_cmd.group(1), "<healthcheck>", "exec")
    except SyntaxError as e:
        raise AssertionError(f"команда пробы невалидна как python-код: {e}") from e


def test_host_nginx_overlay_honours_web_port_and_bind():
    """`./deploy.sh --port 9000 --with deploy/nginx/docker-compose.host-nginx.yml`:
    overlay с жёстким `127.0.0.1:8080` возвращал порт назад, а цикл готовности
    ждал кабинет на 9000 и отменял удачный выкат (дефект D4)."""
    text = _text(HOST_NGINX_OVERLAY)
    assert re.search(r'"\$\{WEB_BIND:-127\.0\.0\.1\}:\$\{WEB_PORT:-8080\}:80"', text), \
        "overlay хостового nginx обязан публиковать порт через ${WEB_BIND}/${WEB_PORT}, а не литералом"


def test_compose_declares_project_name_and_log_rotation():
    """Имя проекта фиксирует имя тома (запуск из другой папки ≠ пустая база),
    ротация логов — что /var/lib/docker не кончится посреди рабочей смены."""
    text = _text(COMPOSE)
    assert re.search(r"(?m)^name:[ \t]+statcab[ \t]*$", text), \
        "docker-compose.yml: нет name: statcab — имя тома зависит от имени папки"
    assert "max-size" in text and "json-file" in text, \
        "docker-compose.yml: логи контейнеров без ротации"
    assert text.count("healthcheck:") >= 3, \
        "docker-compose.yml: проба должна быть у db, backend и web"
    assert "${WEB_BIND:-127.0.0.1}:${WEB_PORT" in text, \
        "docker-compose.yml: без WEB_BIND кабинет обязан торчать только на loopback — " \
        "publish-порт docker не слушает hosts-ограничения (п. 21 TASKS2.md)"
    assert re.search(r"(?m)^WEB_BIND=127\.0\.0\.1[ \t]*$", _text(ENV_EXAMPLE)), \
        ".env.example: WEB_BIND не описан — оператор не узнает, как открыть кабинет наружу"
