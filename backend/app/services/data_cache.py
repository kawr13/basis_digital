"""Кэш собранных ответов витрины «Отчёт диспетчера» в памяти процесса.

Почему здесь байты, а не dict. `services/dispatcher.overview()` за 31 сутки
считает ~110 мс и отдаёт ~70 КБ JSON, за 365 суток — ~1200 мс и ~740 КБ. Если
бы кэш держал готовый `dict`, каждый повторный запрос всё равно гонял бы его
через `jsonable_encoder` + `json.dumps`: на годовом периоде это десятки
миллисекунд чистого CPU под GIL — ровно то, с чем и боролись. Кэш хранит те же
байты, что FastAPI кладёт в тело ответа: повторный запрос стоит один хеш по ним
и одну запись в сокет, сериализация — ноль раз.

Почему ключ обязан включать версию. Данные витрины меняет импорт `.xlsm` или
синк 1С (раз в ~15 минут). Версия — `max(updated_at)`/`max(created_at)` по
каждой фактовой таблице витрины одним запросом `UNION ALL`; запись смены сдвигает
её, и собранный ответ сам становится промахом. Версию переспрашиваем не чаще,
чем раз в `VERSION_TTL` секунд: запрос к девяти таблицам дешёвый, но не бесплатный.

Когда переезжать в Redis. Кэш живёт в процессе, поэтому при `--workers N`
каждая копия держит свои `MAX_ENTRIES` записей и свой счётчик попаданий: после
инвалидации первый запрос в каждый воркер считается заново. Пока витрина
обновляется раз в 15 минут, а пользователей десятки, это дешевле общего
хранилища и не требует сетевого round-trip. Переезд нужен, когда (а) воркеров стало
заметно больше двух и «холодный первый запрос в воркер» стал виден людям,
(б) захотим кэшировать тяжёлые отчёты, которые нельзя считать в каждом процессе,
или (в) появится несколько инстансов кабинета за балансировщиком. Ключи подобраны
так, чтобы лечь в `SET <ключ> <байты> EX <ttl>`: `version(db)` становится
отдельным ключом-версией, LRU — политикой `maxmemory`, а `invalidate()` —
`FLUSH` по префиксу.
"""
from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from datetime import date, datetime
from hashlib import blake2b
from typing import Any, Callable

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.orm import Session

from ..models import (
    FactDispatcherShift, FactPlatformBalance, FactRailArrival, FactRailDelivery,
    FactRailDeparture, FactRailOperation, FactTruckOperation,
    FactVesselArrival, FactVesselOperation,
)

# Больше восьми периодов на странице «Статистика» не живут: период, смена,
# пресеты 7/14/30/90 и дриллдауны. Записи держим в байтах, но пусть их будет
# немного — иначе кэш начинает вытеснять сам себя между переключениями пресета.
MAX_ENTRIES = 8

# На сколько секунд верим посчитанной версии данных. Это и есть цена свежести:
# после импорта `.xlsm` в чужом воркере кабинет отдаёт старые цифры максимум
# столько же. Ставить 0 нельзя — версии будут спрашиваться на каждый запрос.
VERSION_TTL = 2.0

# Ключ служебных значений внутри кэша короткоживущих величин (`cached_value`).
VERSION_VALUE = ("__version__",)

# Фактовые таблицы витрины диспетчера: одна сводная + восемь детальных.
# У детальных своей колонки `updated_at` нет — строки смены при повторном
# импорте удаляются и вставляются заново (см. `onec.dispatcher.apply_snapshot`),
# поэтому для них годится и `created_at`.
FACT_TABLES = (
    FactDispatcherShift, FactVesselOperation, FactRailOperation, FactTruckOperation,
    FactVesselArrival, FactRailArrival, FactRailDeparture, FactRailDelivery,
    FactPlatformBalance,
)

_lock = threading.Lock()

# ключ → (версия данных, байты ответа). Порядок словаря = порядок
# обращения: вытесняем то, к чему не обращались дольше всех.
_bodies: "OrderedDict[tuple, tuple[str, bytes]]" = OrderedDict()

# ключ → (monotonic, значение) для `cached_value`: считаем не чаще ttl.
_values: dict[tuple, tuple[float, Any]] = {}

_counters = {"hits": 0, "misses": 0}


# --------------------------------------------------------------- сериализация ----

def _json_default(value: Any) -> Any:
    """В payload витрины живут `date`/`datetime` — FastAPI пишет их isoformat."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"в кэш витрины нельзя сериализовать {type(value).__name__}")


def dumps(payload: dict) -> bytes:
    """Тот же JSON, что отдал бы FastAPI: юникод как есть, без пробелов."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                      default=_json_default).encode("utf-8")


def etag(body: bytes) -> str:
    """Крепкий ETag по байтам ответа.

    Считаем на каждый запрос, а не храним в записи: blake2b по 700 КБ — это
    десятки микросекунд, зато в кэше не заводится поле, обязанное совпадать с байтами.
    """
    return f'"{blake2b(body, digest_size=16).hexdigest()}"'


# ------------------------------------------------------------------ версия ----

def _stamp(model):
    """По какой колонке считать версию таблицы: `updated_at` есть не у всех."""
    return getattr(model, "updated_at", None) or model.created_at


def _query_version(db: Session) -> str:
    """Метки времени всех фактовых таблиц витрины — одним запросом.

    `UNION ALL`, а не девять отдельных: девять кругов к БД дороже самой
    агрегации. Метка таблицы кладём в результат строкой — порядок строк
    `UNION ALL` стандартом не гарантирован, а версия обязана быть стабильной.
    """
    parts = [
        select(literal(model.__tablename__).label("t"),
               func.max(_stamp(model)).label("v"))
        for model in FACT_TABLES
    ]
    stamps = {row[0]: row[1] for row in db.execute(union_all(*parts))}
    # Пустая таблица даёт None. Терять такую строку нельзя: удаление последней
    # смены обязано сдвинуть версию, поэтому None — отдельное значение, а не ноль.
    return "|".join(f"{model.__tablename__}={stamps.get(model.__tablename__) or '-'}"
                    for model in FACT_TABLES)


def version(db: Session) -> str:
    """Версия данных витрины; пересчитывается не чаще, чем раз в `VERSION_TTL`."""
    return cached_value(VERSION_VALUE, VERSION_TTL, lambda: _query_version(db))


def cached_value(key: tuple, ttl: float, build: Callable[[], Any]) -> Any:
    """Значение, которое пересчитывают не чаще, чем раз в `ttl` секунд.

    Нужно для величин, без которых не обойтись до обращения к кэшу ответов
    (версия данных, границы витрины): запрос идёт к БД, но не на каждый хит.
    Сессию в залоченной секции не держим — под замком только чтение и запись
    словаря, сам запрос работает параллельно с другими потоками.
    """
    with _lock:
        cached = _values.get(key)
        if cached is not None and time.monotonic() - cached[0] < ttl:
            return cached[1]
    fresh = build()               # вне блокировки: запрос не должен ждать замок
    with _lock:
        _values[key] = (time.monotonic(), fresh)
    return fresh


# ------------------------------------------------------------------ байты ----

def get(key: tuple, version: str) -> bytes | None:
    """Байты ответа, собранные на эту же версию данных, иначе `None`.

    Запись, собранная на ушедшую версию, не просто промах — её выбрасываем,
    иначе LRU набивал бы предыдущими поколениями одного и того же периода.
    """
    with _lock:
        entry = _bodies.get(key)
        if entry is not None and entry[0] == version:
            _bodies.move_to_end(key)
            _counters["hits"] += 1
            return entry[1]
        if entry is not None:
            del _bodies[key]
        _counters["misses"] += 1
    return None


def put(key: tuple, version: str, payload: dict) -> bytes:
    """Сериализовать payload и положить в кэш; вернуть байты (их отдадут наружу)."""
    body = dumps(payload)           # сериализация — вне блокировки, она самая долгая
    with _lock:
        _bodies[key] = (version, body)
        _bodies.move_to_end(key)
        while len(_bodies) > MAX_ENTRIES:
            _bodies.popitem(last=False)
    return body


def invalidate() -> None:
    """Забыть всё: байты ответов, версию и короткоживущие значения.

    Импорт `.xlsm` и загрузка из 1С вызывают этот явно: версия данных и так
    сдвинулась бы, но у строк-деталей своей `updated_at` нет, а `VERSION_TTL`
    добавил бы до двух секунд «чужих» цифр после нажатия «Загрузить».
    """
    with _lock:
        _bodies.clear()
        _values.clear()


def info() -> dict:
    """Диагностика для админки и тестов: сколько записей и сколько попаданий."""
    with _lock:
        return {
            "entries": len(_bodies),
            "max_entries": MAX_ENTRIES,
            "hits": _counters["hits"],
            "misses": _counters["misses"],
            "version_ttl": VERSION_TTL,
            "tables": [m.__tablename__ for m in FACT_TABLES],
        }
