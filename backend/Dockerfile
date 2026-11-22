FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY app ./app

# Не root: кабинет смотрит в интернет, понижайте привилегии
RUN useradd -m -u 10001 statcab && mkdir -p /app/data && chown -R statcab /app
USER statcab

EXPOSE 8000
# Shell-форма CMD нужна ради переменной ${UVICORN_WORKERS:-1}: в exec-форме docker
# их не подставляет. exec обязателен: shell уступает место uvicorn, тот остаётся
# PID 1 и получает SIGTERM от `docker stop` напрямую — успевает погасить воркеров,
# а не дожидается SIGKILL через 10 секунд. httptools — быстрый парсер вместо h11.
# Один воркер по умолчанию: планировщик и блокировка синхронизации живут внутри
# процесса, второй воркер значит вторую синхронизацию за интервал (п. 8 TASKS2.md).
# Проба готовности (п. 19 TASKS2.md): /readyz отвечает 200, только когда база жива и
# витрина не устарела; /healthz остаётся пробой живости, её ждёт deploy.sh через nginx.
# python + http.client вместо curl: python в базовом образе уже есть, curl контейнеру,
# смотрящему в интернет, больше не нужен — меньше лишней поверхности в образе.
# http.client, а не urlopen: urlopen бросает исключение на любом 4xx/5xx, а 403 —
# это «приложение живо и просит токен», а не неисправность контейнера. Без этого
# админ заводил токен в админке (service.readyz_token) — и контейнер краснел как
# неготовый без объяснений (п. 19, дефект D3).
# READY_TOKEN — токен пробы: /readyz принимает значение из стора настроек ИЛИ из
# этой переменной, дублировать токен из админки в .env не нужно. Токен в URL
# процент-экранируется (quote): токен вводится произвольной строкой, и без
# экранирования пробел или «&» в нём ломали сам запрос (дефект D2).
# Та же команда обязана быть строкой в healthcheck'е docker-compose.yml —
# compose перекрывает образ, и расхождение незаметно (его проверяет
# test_probe_command_is_identical_in_image_and_compose).
# start-period 5 минут: первый запуск с init_db, миграциями колонок и первой загрузкой
# из 1С занимает минуты, преждевременный unhealthy — ложная тревога. timeout 5 с
# меньше interval 30 с: зависшая проба не наслаивается; retries=3 — статус краснеет
# после минуты настоящей проблемы, а не по одному сбою.
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD python -c "import os,urllib.parse as p,http.client as h; t=os.environ.get('READY_TOKEN',''); c=h.HTTPConnection('127.0.0.1',8000,timeout=4); c.request('GET','/readyz'+('?token='+p.quote(t,safe='') if t else '')); raise SystemExit(c.getresponse().status not in (200,403))"
CMD exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-1} --http httptools
