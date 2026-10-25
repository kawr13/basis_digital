"""Конфиги nginx кабинета: `frontend/nginx.conf` и `deploy/nginx/statcab.vhost.conf`.

Живого nginx в CI нет, а регресс здесь бьёт по пользователю сразу: 413 на импорте
«Отчёта диспетчера», несжатый JSON витрины вместо gzip, старый `index.html` после
релиза. Проверяем поэтому текст конфигов как есть — какие директивы в них написаны.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_CONF = ROOT / "frontend" / "nginx.conf"
VHOST_CONF = ROOT / "deploy" / "nginx" / "statcab.vhost.conf"


def _text(path: Path) -> str:
    assert path.is_file(), f"не найден конфиг nginx: {path}"
    # В рабочей копии строки могут быть с CRLF — на смысл директив это не влияет.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _values(text: str, directive: str) -> list[str]:
    """Все значения директивы: `gzip on;` → ['on'] (соседний `gzip_types` не трогает)."""
    found = re.findall(rf"(?m)^[ \t]*{re.escape(directive)}[ \t]+([^;]+?)[ \t]*;", text)
    return [f.strip() for f in found]


def _location_body(text: str, location_re: str) -> str:
    """Тело location с нужным адресом; вложенных блоков в этих конфигах нет."""
    match = re.search(rf"location[ \t]+{location_re}[ \t]*\{{([^}}]*)\}}", text)
    return match.group(1) if match else ""


def _cache_controls(text: str) -> list[str]:
    return [c.strip() for c in re.findall(r'add_header[ \t]+Cache-Control[ \t]+"?([^";]+)', text)]


def test_client_max_body_size_16m_in_both_configs():
    """Импорт .xlsm едет из браузера в base64 (+33 % к весу), при 2m nginx отвечал 413."""
    for path in (FRONTEND_CONF, VHOST_CONF):
        values = _values(_text(path), "client_max_body_size")
        assert values, f"нет client_max_body_size в {path} — сработает дефолт nginx в 1m"
        assert all(v.lower().replace(" ", "") == "16m" for v in values), (
            f"{path.name}: client_max_body_size обязан быть 16m, найдено {values}"
        )


def test_frontend_gzip_covers_proxied_api():
    """Без gzip_proxied nginx проксируемые ответы не сжимает, а весь API — это proxy_pass."""
    text = _text(FRONTEND_CONF)
    assert any(v == "on" for v in _values(text, "gzip")), (
        "frontend/nginx.conf: нужен `gzip on;` — иначе ни статика, ни API не сжимаются"
    )
    assert any("any" in v for v in _values(text, "gzip_proxied")), (
        "frontend/nginx.conf: нужен `gzip_proxied any;` — по умолчанию проксированные "
        "ответы не сжимаются, и /api/dispatcher/overview уходит несжатым"
    )
    assert any("application/json" in v for v in _values(text, "gzip_types")), (
        "frontend/nginx.conf: в gzip_types обязан остаться application/json — это весь JSON API"
    )


def test_index_html_is_not_cached():
    """Старый index.html после релиза ссылается на удалённые хэшированные JS."""
    text = _text(FRONTEND_CONF)
    block = _location_body(text, r"=\s*/index\.html")
    assert block, "frontend/nginx.conf: не найден `location = /index.html { ... }`"
    assert re.search(r"""add_header[ \t]+Cache-Control[ \t]+["']?[^;"']*no-store""", block), (
        "frontend/nginx.conf: index.html отдаётся без Cache-Control no-store — браузер "
        "кеширует его эвристически, и после релиза пользователь видит белую страницу"
    )
    assert re.search(r"try_files[^;]*index\.html[ \t]*;", text), (
        "frontend/nginx.conf: SPA-маршруты обязаны догружаться через /index.html — "
        "только тогда no-store доходит и до них"
    )


def test_static_assets_are_cached_immutable():
    """Сборки Vite несут хэш содержимого в имени файла — их можно отдавать навечно."""
    text = _text(FRONTEND_CONF)
    assert re.search(r"location[ \t]+~\*[^;{]*\\.\(js\\|css", text), (
        "frontend/nginx.conf: пропал regex-location статики (js|css|svg|woff2?)"
    )
    assert any("max-age=31536000" in c and "immutable" in c for c in _cache_controls(text)), (
        "frontend/nginx.conf: статике нужен Cache-Control "
        "\"public, max-age=31536000, immutable\""
    )


def test_frontend_api_upload_is_not_buffered():
    """Файл отчёта должен уходить в бэкенд потоком, а не ложиться сначала на диск nginx."""
    block = _location_body(_text(FRONTEND_CONF), r"/api/")
    assert block, "frontend/nginx.conf: не найден `location /api/ { ... }`"
    assert re.search(r"proxy_request_buffering[ \t]+off[ \t]*;", block), (
        "frontend/nginx.conf: в location /api/ нужен `proxy_request_buffering off;`"
    )


def test_vhost_gzip_covers_proxied_responses():
    """Кабинет проксируют и внешним nginx без внутреннего — сжатие нужно и там."""
    text = _text(VHOST_CONF)
    assert any(v == "on" for v in _values(text, "gzip")), (
        "statcab.vhost.conf: нужен `gzip on;` — vhost умеет быть единственным nginx"
    )
    assert any("any" in v for v in _values(text, "gzip_proxied")), (
        "statcab.vhost.conf: нужен `gzip_proxied any;` — без него проксируемые ответы "
        "не сжимаются, даже когда gzip включён"
    )
