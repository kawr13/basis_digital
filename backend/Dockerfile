FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY app ./app

# Не root: кабинет смотрит в интернет, понижайте привилегии
RUN useradd -m -u 10001 statcab && mkdir -p /app/data && chown -R statcab /app
USER statcab

EXPOSE 8000
# Shell-форма CMD нужна ради переменной ${UVICORN_WORKERS:-2}: в exec-форме docker
# их не подставляет. exec обязателен: shell уступает место uvicorn, тот остаётся
# PID 1 и получает SIGTERM от `docker stop` напрямую — успевает погасить воркеров,
# а не дожидается SIGKILL через 10 секунд. httptools — быстрый парсер вместо h11.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2} --http httptools
