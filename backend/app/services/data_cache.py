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

Почему single-flight и потолок в байтах (п. 32 TASKS2.md). Одну и ту же
величину витрины спрашивают несколько параллельных запросов (виджеты
«Статистики», опрос страницы, двойной клик): без single-flight N потоков
считали бы один и тот же агрегат N раз. Одинаковые ключ и версия теперь
ждут первого считавшего и забирают его результат, причём ждущий не держит
замок кэша. А `MAX_BYTES` ограничивает RAM кэша независимо от того, сколько
накачает очередной `capacity-trend`.

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

# Потолок суммарного размера хранимых ответов (п. 32 TASKS2.md — «лимит по
# байтам»). Цифра из замеров того же файла: годовой `overview` — ~740 КБ,
# `capacity-trend` на дефолтном окне — 344 КБ. 8 МиБ — потолок RAM кэша
# одного воркера: хватает и на восемь коротких периодов, и на годовые
# ответы с запасом, а дальше LRU вытесняет по суммарному размеру, а не
# только по числу записей.
MAX_BYTES = 8 * 1024 * 1024

# На сколько секунд верим посчитанной версии данных. Это и есть цена свежести:
# после импорта `.xlsm` в чужом воркере кабинет отдаёт старые цифры максимум
# столько же. Ставить 0 нельзя — версии будут спрашиваться на каждый запрос.
VERSION_TTL = 2.0

# Сколько секунд ждущие разрешают лидеру single-flight считать ключ
# (п. 32 TASKS2.md). Самый долгий расчёт — годовой `overview` — ~1.2 с по
# замерам TASKS2.md, то есть запас десятикратный. Умри лидер с исключением
# и не позови `put` — ждущие отдают себе ключ по истечении этого срока:
# замок кэша на время вычислений никто не держит, поэтому взаимной
# блокировки нет по построению.
INFLIGHT_TTL = 10.0

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

# ("body"|"value", ключ[, версия]) → _Flight: идёт общее вычисление
# (single-flight, п. 32 TASKS2.md). Два префикса, чтобы ключи байтовых
# ответов и ключей короткоживущих величин не сталкивались между собой.
_inflight: dict[tuple, "_Flight"] = {}

_counters = {"hits": 0, "misses": 0, "inflight_joined": 0}


class _Flight:
    """Одно вычисление на всех, кто спросил один и тот же ключ (single-flight).

    Первый поток создаёт полёт и считает; остальные ждут на `done`, отпустив
    `_lock`, — ожидание под замком отказало бы весь кэш всем остальным.
    `owner` сторожит рекурсивный вызов: поток не ждёт собственный полёт (иначе
    повторный `get` того же ключа из того же потока умер бы в дедлоке).
    `deadline` страхует случай «лидер упал и не позвал `put`»: пережидя его,
    следующий поток становится лидером и считает сам.
    """

    __slots__ = ("done", "result", "owner", "deadline")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: Any = None           # None = результата нет (лидер упал)
        self.owner = threading.get_ident()
        self.deadline = time.monotonic() + INFLIGHT_TTL


def _flight_acquire(fkey: tuple) -> "tuple[_Flight, bool]":
    """Под `_lock`: взять полёт ключа. Возвращает (полёт, мы_ли_лидеры).

    Чужой живой полёт — сигнал ждать; просроченный или свой собственный —
    замещаем новым и считаем сами.
    """
    flight = _inflight.get(fkey)
    if (flight is not None and flight.owner != threading.get_ident()
            and time.monotonic() < flight.deadline):
        return flight, False
    flight = _Flight()
    _inflight[fkey] = flight
    return flight, True


def _flight_wait(fkey: tuple, flight: "_Flight") -> "tuple[str, Any]":
    """Дождаться результата лидера вне `_lock`.

    ("result", значение) — получили (запись могли и вытеснить из кэша —
    байты всё равно в руках). ("leader", новый полёт) — лидер умер или не
    уложился в `INFLIGHT_TTL`, считать теперь нам. ("again", None) — нас
    опередил третий поток, переспрашиваем.
    """
    if (flight.done.wait(max(0.0, flight.deadline - time.monotonic()))
            and flight.result is not None):
        with _lock:
            _counters["inflight_joined"] += 1
        return "result", flight.result
    with _lock:
        if _inflight.get(fkey) is flight:
            fresh = _Flight()
            _inflight[fkey] = fresh
            return "leader", fresh
    return "again", None


def _flight_finish(fkey: tuple, flight: "_Flight", result: Any) -> None:
    """Снять полёт с публикации и разбудить ждущих (или отпустить ни с чем)."""
    with _lock:
        if _inflight.get(fkey) is flight:
            del _inflight[fkey]
    flight.result = result
    flight.done.set()


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

    Параллельные промахи считают значение один раз: первый поток становится
    лидером, остальные ждут его результат (single-flight, п. 32 TASKS2.md).
    Лидер, упавший с исключением, ждущих не вешает — полёт освобождается без
    результата, и каждый считает сам (со своей ошибкой).
    """
    fkey = ("value", key)
    while True:
        with _lock:
            cached = _values.get(key)
            if cached is not None and time.monotonic() - cached[0] < ttl:
                return cached[1]
            flight, leader = _flight_acquire(fkey)
        if not leader:
            state, value = _flight_wait(fkey, flight)
            if state == "result":
                return value
            if state == "leader":
                flight = value          # лидер не отдал результат — считаем сами
            else:
                continue                # полёт перехватил другой — переспрашиваем
        try:
            fresh = build()             # вне блокировки: запрос не должен ждать замок
        except BaseException:
            _flight_finish(fkey, flight, None)
            raise
        with _lock:
            _values[key] = (time.monotonic(), fresh)
        _flight_finish(fkey, flight, fresh)
        return fresh


# ------------------------------------------------------------------ байты ----

def get(key: tuple, version: str) -> bytes | None:
    """Байты ответа, собранные на эту же версию данных, иначе `None`.

    Запись, собранная на ушедшую версию, не просто промах — её выбрасываем,
    иначе LRU набивал бы предыдущими поколениями одного и того же периода.

    Промах регистрирует полёт: первый не попавший поток получает `None` и
    обязан посчитать и вызвать `put`, а пришедшие следом за тот же ключ и ту
    же версию ждут полёт и забирают его байты (single-flight, п. 32
    TASKS2.md). Вызывающему коду об этом знать не нужно — `None` по-прежнему
    означает «считай сам».
    """
    fkey = ("body", key, version)
    while True:
        with _lock:
            entry = _bodies.get(key)
            if entry is not None and entry[0] == version:
                _bodies.move_to_end(key)
                _counters["hits"] += 1
                return entry[1]
            if entry is not None:
                del _bodies[key]
            _counters["misses"] += 1
            flight, leader = _flight_acquire(fkey)
        if leader:
            return None                 # считающий код сам вызовет put()
        state, body = _flight_wait(fkey, flight)
        if state == "result":
            return body
        if state == "leader":
            return None                 # дождались — лидер теперь мы
        # полёт перехватил третий поток — смотрим в кэш заново


def _stored_bytes() -> int:
    """Суммарный размер хранимых ответов. Вызывать только под `_lock`."""
    return sum(len(entry[1]) for entry in _bodies.values())


def _evict_locked() -> None:
    """Вытеснение LRU: по числу записей и по суммарным байтам (п. 32 TASKS2.md).

    Одна запись остаётся живой даже сверх `MAX_BYTES`: иначе единственный
    годовой `overview` (~740 КБ по замерам TASKS2.md) вытеснялся бы в момент
    добавления, и кэш переставал бы работать вместо того, чтобы ограничиваться.
    """
    while len(_bodies) > 1 and (len(_bodies) > MAX_ENTRIES
                                or _stored_bytes() > MAX_BYTES):
        _bodies.popitem(last=False)


def put(key: tuple, version: str, payload: dict) -> bytes:
    """Сериализовать payload и положить в кэш; вернуть байты (их отдадут наружу).

    Вторая половина single-flight: `get` зарегистрировал полёт, `put` отдаёт
    байты всем, кто на него ждал (счётчик `inflight_joined` в `stats()`).
    """
    body = dumps(payload)           # сериализация — вне блокировки, она самая долгая
    fkey = ("body", key, version)
    with _lock:
        _bodies[key] = (version, body)
        _bodies.move_to_end(key)
        _evict_locked()
        flight = _inflight.pop(fkey, None)
    if flight is not None:
        flight.result = body
        flight.done.set()
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
        # _inflight не трогаем: идущие вычисления принадлежат ушедшим версиям,
        # их `put` разбудит ждущих сам, а просроченные полёты и так живут не
        # дольше `INFLIGHT_TTL`.


def stats() -> dict:
    """Диагностика для админки и тестов: записи, байты, попадания, single-flight.

    `inflight_joined` — сколько раз поток получил уже считаемый кем-то ответ,
    не считая его сам (эффект single-flight, п. 32 TASKS2.md); `bytes` —
    текущая занятость кэша относительно потолка `MAX_BYTES`.
    """
    with _lock:
        return {
            "entries": len(_bodies),
            "max_entries": MAX_ENTRIES,
            "bytes": _stored_bytes(),
            "max_bytes": MAX_BYTES,
            "hits": _counters["hits"],
            "misses": _counters["misses"],
            "inflight_joined": _counters["inflight_joined"],
            "inflight": len(_inflight),
            "version_ttl": VERSION_TTL,
            "tables": [m.__tablename__ for m in FACT_TABLES],
        }
