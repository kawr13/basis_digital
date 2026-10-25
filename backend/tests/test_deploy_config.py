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

# Переменные, без которых пункт 9 (воркеры и пул) и пункт 10 (таймаут пробы)
# просто не чем будет настроить: дефолты в коде — это дефолты, а не настройка.
NEW_VARS = ("UVICORN_WORKERS", "DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_RECYCLE_SEC",
            "ONE_C_PROBE_TIMEOUT_SEC")


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
    assert "${UVICORN_WORKERS:-2}" in line, (
        "Dockerfile: воркеры должны браться из UVICORN_WORKERS с запасным значением — "
        "иначе менять их придётся пересборкой образа"
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
