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
    block = _location_body(_text(FRONTEND_CONF), r"\^~ /api/")
    assert block, "frontend/nginx.conf: не найден `location ^~ /api/ { ... }`"
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


def test_backend_address_resolves_at_request_time():
    """nginx резолвил proxy_pass один раз при старте: после пересоздания контейнера
    backend весь /api отвечал 502, а deploy.sh отменял успешный выкат (п. 20).
    Динамический резолв живёт в upstream (`server … resolve` + resolver), а не в
    переменной proxy_pass: только с upstream-формой работает keepalive."""
    text = _text(FRONTEND_CONF)
    assert re.search(r"(?m)^\s*upstream\s+statcab_backend\s*\{", text), \
        "frontend/nginx.conf: нет блока upstream statcab_backend"
    assert re.search(r"(?m)^\s*server\s+backend:8000\s+resolve\s*;", text), \
        "frontend/nginx.conf: в upstream нет `server backend:8000 resolve` — " \
        "после пересоздания контейнера nginx уйдёт в старый IP"
    assert re.search(r"(?m)^\s*resolver\s+127\.0\.0\.11\s+valid=10s", text), \
        "frontend/nginx.conf: нет resolver 127.0.0.11 valid=10s — адрес контейнера не обновится"
    assert re.search(r"(?m)^\s*zone\s+statcab_backend\b", text), \
        "frontend/nginx.conf: в upstream нет zone — с `server … resolve` без общей памяти nginx не стартует"
    assert re.search(r"(?m)^\s*proxy_pass\s+http://statcab_backend", text), \
        "frontend/nginx.conf: proxy_pass не идёт в upstream statcab_backend"
    assert not re.search(r"(?m)^\s*proxy_pass\s+http://backend:8000;", text), \
        "frontend/nginx.conf: proxy_pass литералом — имя разовьётся один раз при старте"
    assert "$statcab_backend" not in text, \
        "frontend/nginx.conf: proxy_pass через переменную не переиспользует соединения"


def test_upstream_keepalive_is_used_by_proxies():
    """keepalive в upstream без proxy_http_version 1.1 + `Connection ""` в location
    — мёртвая настройка: HTTP/1.0 без keepalive-заголовка рвёт соединение на каждом
    запросе, и весь смысл пула соединений теряется (п. 20 TASKS2.md)."""
    text = _text(FRONTEND_CONF)
    assert re.search(r"(?m)^\s*keepalive\s+16\s*;", text), \
        "frontend/nginx.conf: в upstream нет keepalive 16"
    for loc in (r"=\s*/healthz", r"=\s*/api/readyz", r"=\s*/readyz",
                r"=\s*/api/auth/login", r"\^~\s*/api/admin/sync/run",
                r"\^~\s*/api/admin/dispatcher/import", r"\^~\s*/api/"):
        block = _location_body(text, loc)
        assert block, f"frontend/nginx.conf: не найден location {loc}"
        assert re.search(r"proxy_http_version[ \t]+1\.1[ \t]*;", block), \
            f"frontend/nginx.conf: в location {loc} нет proxy_http_version 1.1 — keepalive не работает"
        assert re.search(r'proxy_set_header[ \t]+Connection[ \t]+""[ \t]*;', block), \
            f"frontend/nginx.conf: в location {loc} нет `Connection \"\"` — keepalive не работает"


def test_readyz_token_is_not_logged():
    """Мониторинг ходит в /api/readyz?token=…, а combined-лог пишет $request
    целиком: без access_log off секрет лежал бы в `docker logs` (п. 19, дефект D1)."""
    text = _text(FRONTEND_CONF)
    for loc in (r"=\s*/api/readyz", r"=\s*/readyz"):
        block = _location_body(text, loc)
        assert block, f"frontend/nginx.conf: нет `location {loc}` — проба с токеном попадёт в общий лог"
        assert re.search(r"access_log[ \t]+off[ \t]*;", block), \
            f"frontend/nginx.conf: в location {loc} нет access_log off — токен уйдёт в docker logs"


def test_healthz_probe_has_short_timeouts():
    """Цикл готовности deploy.sh рассчитывает на ~2 с между попытками: location
    живости без proxy_connect_timeout/proxy_read_timeout висит дефолтные 60 с
    на попытку вместо быстрого отказа (дефект D8)."""
    block = _location_body(_text(FRONTEND_CONF), r"=\s*/healthz")
    assert block, "frontend/nginx.conf: не найден `location = /healthz`"
    assert re.search(r"proxy_connect_timeout[ \t]+2s[ \t]*;", block), \
        "frontend/nginx.conf: /healthz без proxy_connect_timeout 2s"
    assert re.search(r"proxy_read_timeout[ \t]+5s[ \t]*;", block), \
        "frontend/nginx.conf: /healthz без proxy_read_timeout 5s"


def test_login_is_rate_limited_at_proxy():
    """Счётчик приложения живёт в памяти процесса и обнуляется рестартом контейнера."""
    text = _text(FRONTEND_CONF)
    assert re.search(r"(?m)^\s*limit_req_zone[ \t]+\$binary_remote_addr[ \t]+zone=\w+:10m", text), \
        "frontend/nginx.conf: нет limit_req_zone для входа"
    login = _location_body(text, r"= /api/auth/login")
    assert login and "limit_req zone=" in login, \
        "frontend/nginx.conf: location входа без limit_req — подбор пароля не ограничен"


def test_long_operations_get_more_time():
    """Ручная синхронизация и импорт отчёта идут прямо в запросе: на 60 с браузер
    получал 504, пока синк продолжался в фоне."""
    text = _text(FRONTEND_CONF)
    for path in ("/api/admin/sync/run", "/api/admin/dispatcher/import"):
        block = _location_body(text, rf"\^~ {re.escape(path)}")
        assert block, f"frontend/nginx.conf: нет location для {path}"
        assert re.search(r"proxy_read_timeout[ \t]+300s[ \t]*;", block), \
            f"frontend/nginx.conf: {path} без увеличенного proxy_read_timeout"
