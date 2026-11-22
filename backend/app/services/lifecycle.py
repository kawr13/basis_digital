"""Остановка процесса по кнопке из админки (п. 28 TASKS2.md).

Кабинет живёт под супервизором: в Docker это `restart: unless-stopped`, в systemd —
`Restart=always`, в systemd-юните из docs/06 — тоже. Значит «перезапустить
сервис» для админа = честно завершить процесс, а поднимет его супервизор.

Задержка перед выходом нужна, чтобы ответ 202 доехал до браузера раньше, чем
умрёт сокет: иначе админ увидит обрыв соединения вместо «сервис перезапускается».
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time

log = logging.getLogger("statcab.lifecycle")

# Сколько секунд даём FastAPI отдать ответ и дозаписать аудит.
DRAIN_SEC = 0.6


def schedule_exit(after_sec: float = DRAIN_SEC) -> None:
    """Завершить процесс после того, как ответ ушёл.

    POSIX: SIGTERM самому себе — штатный путь uvicorn: он дообслуживает текущие
    запросы, останавливает планировщик и закрывает HTTP-клиенты 1С в lifespan.
    Windows: доставить SIGTERM себе нельзя, поэтому `os._exit` — супервизор
    поднимет процесс заново, а SQLite WAL и PostgreSQL переживают разрыв без
    последствий (журнал и MVCC).
    """
    def _stop() -> None:
        time.sleep(after_sec)
        log.warning("Администратор запросил перезапуск: процесс завершается")
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except (AttributeError, OSError, ValueError):
            os._exit(0)                                  # noqa: SLF001 — так задумано

    threading.Thread(target=_stop, name="statcab-restart", daemon=True).start()
