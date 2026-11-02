"""Генератор правдоподобного «Отчёта диспетчера» (.xlsm) — демо-данные витрины.

Зачем генератор файла, а не генератор строк. Витрину «Статистики» наполняет
только один путь: `POST /api/admin/dispatcher/import` → `dispatcher_xlsm
.parse_workbook` → `dispatcher.apply_snapshot`. Если насыпать `fact_*`
напрямую, демо разойдётся с продом во всём, что делает импорт:
`dispatcher.source_path` останется пустым (админка — «путь не задан»,
автоимпорт по mtime не срабатывает, браузерная проверка «Статистики» висит на
трёх красных проверках именно там), а `describe()`, границы витрины, `source`
строк и инвалидация кеша отвечают за импортированный файл. Поэтому демо —
настоящий .xlsm, который проходит разбор как есть.

Координаты — из `dispatcher_xlsm`, а не свои. Строки блоков разборщик берёт из
констант этого модуля (`ROW_VESSEL_FIRST`, `ROW_ARRIVAL_HEAD`,
`ROW_PLAT_TOTALS`, …). Переписать эти числа в генераторе означало бы тихий
распад: сдвинет диспетчер строку (или поправит разборщик) — генератор
продолжит писать по старой координате, и импорт не заругается, а молча вернёт
пустой блок: `parse_sheet` умеет «блок не распознан», но не «файл
подозрительный». Колонки разборщик читает инлайновыми номерами
(`ws.cell(r, 2)`), поэтому буквы блоков держим здесь одним списком (`COL_*`), а
расхождение «генератор пишет в D, разборщик читает с E» ловит
тест-раундтрип `tests/test_dispatcher_demo.py`: собранный файл обязан читаться
ровно так, как написан, включая тонны и «129+130» в плане захода.

Правдоподобие важнее объёма: порт в генераторе живёт, а не пересобирается с
нуля на каждом листе. Судно приходит по плановому заходу, стоит на причале
несколько смен, снижает остаток и уходит; «Суда на подходе» — это те же самые
суда до того, как встали, в том числе через N суток. Значения пишутся «как у
человека»: `-` вместо нуля, `129+130` вместо числа, `87+0/3539 тн` в плане,
то кириллицей, то латиницей.

seed фиксирован: один и тот же seed = один и тот же файл, иначе браузерные
проверки и скриншоты нельзя сравнить между прогонами.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from random import Random

import openpyxl

from . import dispatcher_xlsm as dxl

# --------------------------------------------------------------------- пулы ----

# Причалы — ровно так, как пишет разборщик: «РП - 1/2», с пробелом вокруг тире.
BERTHS = ("РП - 1/2", "РП - 2/3", "РП - 3/4", "РП - 4/5", "РП - 5/6")

# Имена в духе порта: латиница и кириллица вперемешку, бортовые с дефисом.
VESSEL_NAMES = (
    "AURORA", "СВЕТЛОГОРСК", "SEVER-2", "ВОЛНА-9", "ATLANTIC BREEZE", "ОКТЯБРЬСКИЙ",
    "МАРИНА", "NORD WIND", "БАЛТИКА", "ГРИФОН", "SEVER-3", "ПОЛНОЧНЫЙ",
    "RIGA MARINER", "ИРБЕНЕ", "СЕВЕРНАЯ ДВИНА", "KRONOS", "АМГАР", "ЛАДОГА-2",
    "POLARIS", "ЧЕЛЯБИНЕЦ", "ВЕТЕК-1", "МЕРКУРИЙ", "ATLAS", "ТАЙМЫР",
    "FESCO GLORY", "СТРЕЛЕЦКИЙ", "БАЛТИЙСКИЙ ПУТЬ", "NORDIC STAR",
)

# Род груза: (коридор т/ч, вероятность выгрузки, тоннаж захода, варианты написки).
# Варианты написки — не пёстрая мелочь: `_CargoNames` в агрегатах склеивает
# «ген.груз» / «генгруз» в одну номенклатуру, и демо обязано это вести, иначе
# «структура груза» на графике покажет не порт, а «три груза по 33 %».
# Кричащих вариантов («УГОЛЬ», «КТК») тут намеренно нет: правило «заменить
# кричащее имя на обычное» в `_CargoNames.label` срабатывает на середине
# агрегации, метка меняется, и один груз разъезжается по двум строкам доната
# (проверено на сервисе). Разборщик такие написки читает, а вот витрина за
# ними тянется криво — это её баг, а не демо, и чинить его здесь нельзя.
CARGO_RULES: dict[str, tuple[tuple[int, int], float, tuple[int, int], tuple[str, ...]]] = {
    "ктк": ((420, 690), 0.80, (12000, 42000), ("ктк", "Ктк")),
    "уголь": ((500, 900), 0.75, (15000, 48000), ("уголь", "Уголь")),
    "щебень": ((280, 520), 0.90, (7000, 26000), ("щебень", "Щебень")),
    "металлолом": ((180, 340), 0.15, (5000, 20000), ("металлолом", "Металлолом")),
    "контейнеры": ((120, 260), 0.50, (3000, 16000), ("контейнеры", "Контейнеры")),
    "лес": ((140, 300), 0.20, (5000, 18000), ("лес", "Лес")),
    "ген.груз": ((200, 430), 0.40, (6000, 24000), ("ген.груз", "генгруз")),
}
CARGOS = tuple(CARGO_RULES)

AGENTS = ("ТРАНСШИП", "Морской агент", "СФЕРА-ШИППИНГ", "Порт-Агент",
          "Дальтранссервис", "АГЕНТСТВО РЕКА", "БалтТрансСервис")
DISPATCHERS = ("Иванов И.И.", "Петров П.П.", "Смирнова А.В.", "Кузнецов Д.М.", "Морозова Е.С.")
CONTRACTORS = ("ПортСервис", "СтройМонтаж", "Погрузка-7", "Ремфлот", "Чистый порт")
STATIONS = ("Шушары", "Мга", "Тосно", "Обухово", "Сортировочная-Лесная",
            "Ирбилово", "Кахолка", "Узловая-2")
WAGON_TYPES = ("п/в", "крытый", "платф", "цистерна")
TRAIN_PREFIXES = ("1549-003", "2900-014", "3300-102", "4400-217", "1812-007", "2107-055")
ACCEPTORS = ("ПЧ-4", "ПЧ-7", "СТЦ", "Экспедитор", "Логистик-Транс")
NOTES = ("", "", "", "осадки 1ч", "нет судовых", "2 крана из 4", "дождевание",
          "ветер 15 м/с", "простой по грузу", "ожидание баржи", "затяжка скважины")
PLATE_OWNERS = ("СМАРТ", "ИТК-ЛОГИСТИКА 1520")

# На сколько суток вперёд диспетчер ведёт «Суда на подходе» — настолько же
# горизонт плана уезжает за конец файла. Без этого карточка плановых заходов
# остаётся пустой: в витрину попадают только отстоявшие суда.
AHEAD_DAYS = 10

# ------------------------------------------------------------------ колонки ----
# Строки блоков — константы `dispatcher_xlsm` (см. шапку модуля). Колонки
# разборщик читает инлайновыми номерами, поэтому буквы живут здесь и больше
# нигде; расхождение ловит тест-раундтрип.

# Ключи = имена полей словаря смены (см. `_Port._vessel`), значения = буквы
# колонок листа: расхождение «ключ не совпал» молча превращает блок в пустоту.
COL_VESSEL = {"berth": "A", "vessel": "B", "cargo": "C", "plan_unload": "D",
              "plan_load": "E", "plan_total": "F", "plan_finish_at": "G",
              "berthed_at": "H", "started_at": "I", "plan_shift": "J", "fact": "K",
              "on_board": "L", "remaining": "M", "tph": "N", "moves": "O", "note": "Q",
              "shift_hours": "W", "downtime": "X", "machine": "Y"}

COL_RAIL = {"cargo": "B", "station": "D", "start_loaded": "G", "start_empty": "H",
            "processed": "I", "end_loaded": "K", "end_empty": "L"}

COL_TRUCK = {"cargo": "O", "imp_unload": "Q", "exp_unload": "R", "imp_load": "S",
             "exp_load": "T", "direct": "U", "total": "V"}

COL_ARRIVAL = {"eta": "N", "vessel": "O", "berth": "Q", "work_kind": "R",
               "cargo": "S", "plan": "T", "agent": "U"}

COL_RAIL_ARRIVAL = {"station": "N", "cargo": "O", "cars": "Q", "work_kind": "R",
                    "wagon_type": "S", "train_index": "T", "note": "U"}

COL_R_OUT = {"date": "A", "plan_time": "B", "semi": "C", "covered": "D", "flat": "G",
             "total": "H", "accepted": "I", "fact": "J", "fact_date": "K", "fact_time": "L"}

COL_R_IN = {"date": "A", "plan_time": "B", "settle_time": "C", "load_semi": "D",
            "load_covered": "G", "load_flat": "H", "empty_semi": "I",
            "empty_covered": "J", "empty_flat": "K", "total": "L"}

# Заголовки: разборщик по началу заголовка решает, читать ли блок (`_is`),
# поэтому подписи пишем в те же строки, что и живой отчёт.
HEAD_VESSEL = {"A": "№ пр", "B": "Судно", "C": "Род груза", "D": "план выгрузки",
               "E": "план погрузки", "F": "∑", "J": "План на смену",
               "K": "Факт за смену, тн", "L": "на борту", "M": "остаток",
               "N": "т/ч", "O": "ходы", "Q": "Примечание", "W": "сменное время",
               "X": "потери", "Y": "маш. время"}
HEAD_RAIL = {"B": "Грузы", "D": "на станции", "G": "в порту гружёные",
             "H": "в порту порожние", "I": "обработано", "K": "конец гружёные",
             "L": "конец порожние"}
HEAD_TRUCK = {"O": "Грузы", "Q": "принял/выгрузка импорт", "R": "растарка/выгрузка экспорт",
              "S": "затарка/погрузка импорт", "T": "затарка/погрузка экспорт",
              "U": "с т/х в а/м", "V": "ВСЕГО"}
HEAD_ARRIVAL = {"N": "Дата захода/нахождение у ПБ", "O": "Судно", "Q": "Причал",
                "R": "Вид работ", "S": "Вид груза", "T": "План", "U": "Агент"}
HEAD_RAIL_ARRIVAL = {"N": "Станция дислокации", "O": "Груз", "Q": "Кол-во вагонов",
                     "R": "Вид работ", "S": "Тип вагона", "T": "Индекс поезда",
                     "U": "Примечание"}
HEAD_R_OUT = {"A": "Дата", "B": "Время план", "C": "полувагоны", "D": "крытые",
              "G": "к/платформа", "H": "итого", "I": "кто принял",
              "J": "ВЫВЕДЕНО ПО ФАКТУ", "K": "дата факт", "L": "время факт"}
HEAD_R_IN = {"A": "Дата", "B": "время план/факт", "C": "время расстановки",
             "D": "гружёные п/в", "G": "крытые", "H": "платформы",
             "I": "порожние п/в", "J": "крытые", "K": "платформы", "L": "Итого"}

DT_FMT = "dd.mm.yyyy hh:mm"       # как в живом отчёте: «03.11.2025 14:20»


# ------------------------------------------------------------------- утилиты ----

def _round_dt(value: datetime) -> datetime:
    """Время листа — с точностью до минуты: секунд в отчёте диспетчера не бывает."""
    return value.replace(second=0, microsecond=0)


def _cargo_key(spelling: str) -> str:
    """Написку груза («ГЕНГРУЗ», «ген.груз») → ключ правил: темп берём по грузу."""
    return spelling.lower().replace(".", "").replace(" ", "").replace("-", "")


_CANON = {_cargo_key(s): name for name, rules in CARGO_RULES.items() for s in rules[3]}


def _rule(cargo: str) -> tuple[tuple[int, int], float, tuple[int, int], tuple[str, ...]]:
    return CARGO_RULES[_CANON.get(_cargo_key(cargo), "ген.груз")]


def _spell(rng: Random, spellings: tuple[str, ...]) -> str:
    """Одна и та же номенклатура в листе пишется по-разному — так честнее."""
    return rng.choice(spellings)


def _plan_cell(rng: Random, tons: float) -> tuple[str, float]:
    """План захода «как у человека» и его же сумма — ровно так считает `_sum_numbers`.

    Разборщик складывает ВСЕ числа строки («87+0/3539 тн» → 3626), поэтому
    ожидаемое значение считаем из тех же слагаемых, что положили в текст: иначе
    план в витрине разойдётся с планом на листе. Разряды пробелом не делим —
    «1 245» `_sum_numbers` прочитает как 1 + 245.
    """
    base = float(round(tons, -1))
    roll = rng.random()
    if roll < 0.08:                                   # плана ещё нет, судно «ждёт»
        return "-", 0.0
    if roll < 0.30:                                   # две строки одной номенклатуры
        a, b = round(base * 0.6, -1), round(base * 0.4, -1)
        return f"{int(a)}+{int(b)}", a + b
    if roll < 0.40:                                   # «87+0/3539 тн» — остаток и дали
        a, b = round(base * 0.02, -1), base
        return f"{int(a)}+0/{int(b)} тн", a + b
    if roll < 0.48:                                   # дробное, как пишет диспетчер
        value = round(base + 0.5, 1)
        return f"{value}".replace(".", ","), value
    return str(int(base)), base


# --------------------------------------------------------------------- план ----

@dataclass
class _Call:
    """Плановый заход: строка «на подходе», а как придёт — строка обработки."""

    eta: datetime
    vessel: str
    berth: str
    work_kind: str
    cargo: str
    tons: float           # реальный тоннаж судна — им живёт симуляция порта
    plan_raw: str         # как написал диспетчер: «129+130», «-», «87+0/3539 тн»
    plan_t: float         # то, что посчитает `_sum_numbers` от этой строки
    agent: str
    arrived: bool = False  # судно встало к причалу — в списке «на подходе» оно не нужно


@dataclass
class _Job:
    """Судно у причала: остаток копится между сменами, а не рождается заново."""

    call: _Call
    plan_total: float
    berthed_at: datetime
    started_at: datetime
    plan_finish_at: datetime
    worked: float = 0.0

    @property
    def remaining(self) -> float:
        return max(self.plan_total - self.worked, 0.0)


class _Port:
    """Состояние порта между сменами: кто стоит у причала и что уже обработал.

    Смена — не «случайные цифры на строку», а шаг модели: судно приходит по
    ETA, работает несколько смен, уходит, причал занимает следующее. Иначе
    «топ судов» даёт «по одному заходу на судно», а `remaining` и `on_board`
    не сходятся с планом — ровно то, что диспетчер видит в живом отчёте первым
    же взглядом.

    Обычный класс, не dataclass: состояние собирает `__init__` (пул причалов,
    черёда, парки платформ), а не вызов.
    """

    def __init__(self, rng: Random, calls: list[_Call]) -> None:
        self.rng = rng
        self.calls = calls
        self.berths = {b: None for b in BERTHS}
        self.waiting = []
        self.cursor = 0
        # парки платформ — баланс, который переходит из смены в смену
        self.platform_state = {
            PLATE_OWNERS[0]: {"total": 59.0, "remaining": float(rng.randint(0, 9))},
            PLATE_OWNERS[1]: {"total": 54.0, "remaining": float(rng.randint(0, 14))},
        }

    # ---- служебное ----
    def _due(self, until: datetime) -> list[_Call]:
        """Плановые заходы, которые должны были прийти до момента `until`."""
        out = []
        while self.cursor < len(self.calls) and self.calls[self.cursor].eta < until:
            out.append(self.calls[self.cursor])
            self.cursor += 1
        return out

    def _job(self, call: _Call, begin: datetime) -> _Job:
        rng = self.rng
        berthed = _round_dt(max(call.eta, begin - timedelta(hours=2)))
        started = berthed + timedelta(hours=rng.uniform(0.5, 2.0))
        # плановое окончание: тоннаж / средний темп, но не меньше смены
        tph = sum(_rule(call.cargo)[0]) / 2
        hours = max(12.0, call.tons / max(tph, 1.0))
        return _Job(call=call, plan_total=call.tons, berthed_at=berthed,
                    started_at=_round_dt(started),
                    plan_finish_at=_round_dt(begin + timedelta(hours=hours)))

    # ---- блоки смены ----
    def step(self, day: date, shift: str) -> dict:
        begin = datetime.combine(day, time(8 if shift == "08-20" else 20, 0))
        finish = begin + timedelta(hours=12)
        night = shift == "20-08"
        rng = self.rng

        # 1) ушедшие суда освобождают причалы
        for berth, job in self.berths.items():
            if job is not None and job.remaining <= 0:
                self.berths[berth] = None
        # 2) пришедшие заходят, кому хватило причала
        self.waiting.extend(self._due(finish))
        for call in list(self.waiting):
            target = call.berth if self.berths.get(call.berth) is None else None
            if target is None:
                target = next((b for b, j in self.berths.items() if j is None), None)
            if target is None:
                continue                    # ждёт черёды — остаётся в «на подходе»
            self.waiting.remove(call)
            call.arrived = True
            self.berths[target] = self._job(call, begin)

        vessels = [self._vessel(berth, job, begin, night)
                   for berth, job in self.berths.items() if job is not None]
        # «Судно обрабатывают два причала» — в живом листе такое бывает и ломает
        # любую попытку придумать строке уникальный ключ. Тоннаж при этом
        # ДЕЛИТСЯ между строками, а не удваивается: иначе грузопоток оказался
        # бы больше, чем было на борту.
        if vessels and rng.random() < 0.12:
            free = [b for b, j in self.berths.items() if j is None]
            if free:
                row = vessels[rng.randrange(len(vessels))]
                part = round(row["fact"] * 0.3, 1)
                machines = round(row["machine"] * 0.3, 1)
                downtime = round(row["downtime"] * 0.3, 1)
                if part > 0 and machines > 0:
                    extra = dict(row, berth=free[0], fact=part, machine=machines,
                                 downtime=downtime,
                                 shift_hours=round(machines + downtime, 1),
                                 moves=max(1, int(machines * 5)), note="второй причал")
                    row["fact"] = round(row["fact"] - part, 1)
                    row["machine"] = round(row["machine"] - machines, 1)
                    row["downtime"] = round(row["downtime"] - downtime, 1)
                    row["shift_hours"] = round(row["machine"] + row["downtime"], 1)
                    # темп — факт / машинное время: разборщик считает его так же,
                    # если колонки N на листе нет
                    row["tph"] = round(row["fact"] / row["machine"], 1) if row["machine"] else 0.0
                    extra["tph"] = round(part / machines, 1)
                    vessels.append(extra)

        return {
            "day": day, "shift": shift,
            "personnel": self._personnel(night),
            "vessels": vessels,
            "rails": self._rails(),
            "trucks": self._trucks(),
            "arrivals": self._arrivals(begin),
            "rail_arrivals": self._rail_arrivals(),
            "rail_out": self._rail_out(day),
            "rail_in": self._rail_in(day),
            "platforms": self._platforms(),
        }

    def _vessel(self, berth: str, job: _Job, begin: datetime, night: bool) -> dict:
        rng = self.rng
        tph_lo, tph_hi = _rule(job.call.cargo)[0]
        shift_hours = round(rng.uniform(10.0, 12.0) * 2) / 2
        downtime = round(rng.uniform(0.2, 2.4 if not night else 3.4), 1)
        machine = round(shift_hours - downtime, 1)
        tph = rng.uniform(tph_lo, tph_hi)
        # Сменное задание и факт считают от одного и того же номинала кранов, но
        # с загрузкой 50–75 %: судно обрабатывают не все краны, бывают осадки,
        # погода и «нет судовых». Отсюда и стоянка в 3–6 смен, и `plan_pct`
        # около 90 %, а не «план выполнен на 300 %».
        plan_shift = round(11 * tph * rng.uniform(0.52, 0.74), -1)
        fact = min(job.remaining, machine * tph * rng.uniform(0.50, 0.78))
        if job.remaining and fact < 1:
            fact = job.remaining            # последние остатки дорабатываем
        job.worked += fact
        done = job.remaining <= 0
        unload = load = 0.0
        kind = job.call.work_kind
        if "выгр" in kind and "погр" in kind:
            unload, load = round(job.plan_total * 0.55, -1), round(job.plan_total * 0.45, -1)
        elif kind == "выгрузка":
            unload = round(job.plan_total, -1)
        else:
            load = round(job.plan_total, -1)
        return {
            "berth": berth,
            "vessel": job.call.vessel,
            "cargo": job.call.cargo,
            "plan_unload": unload,
            "plan_load": load,
            "plan_total": round(job.plan_total, -1),
            "plan_shift": plan_shift,
            "fact": round(fact, 1),
            "on_board": round(job.worked, 1),
            "remaining": round(job.remaining, 1),
            "tph": round(fact / machine, 1) if machine else 0.0,
            "moves": int(machine * rng.uniform(3.5, 8.0)),
            "note": "окончание работ" if done else rng.choice(NOTES),
            "shift_hours": shift_hours,
            "downtime": downtime,
            "machine": machine,
            "berthed_at": job.berthed_at,
            "started_at": job.started_at,
            "plan_finish_at": job.plan_finish_at,
        }

    def _personnel(self, night: bool) -> dict:
        rng = self.rng
        crew_staff = rng.choice((33, 34, 35, 36))
        crew_present = round(crew_staff * rng.uniform(0.78, 1.0))
        day_off = [rng.choice((0, 0, 1, 1, 2, 3)), rng.choice((0, 0, 0, 1))]
        contractors = [{"name": n, "count": rng.randint(2, 9)}
                       for n in rng.sample(CONTRACTORS, rng.randint(1, 3))]
        rs_total = 10
        repair_start = rng.choice((0, 1, 1, 2, 2, 3))
        repair_end = max(0, min(rs_total, repair_start + rng.choice((-1, 0, 0, 1))))
        return {
            "crew_no": rng.choice((1, 2, 3, 4)),
            "dispatcher": rng.choice(DISPATCHERS),
            "crew_present": int(crew_present),
            "crew_staff": crew_staff,
            "day_off_rows": day_off,
            "contractors": contractors,
            "present_total": int(crew_present) + sum(c["count"] for c in contractors),
            # «Итого штат» — штат порта: подрядчики в отчёте стоят отдельной
            # строкой «вышло» и в штат не входят, иначе «явка» падала бы до 60 %.
            "staff_total": crew_staff + rng.choice((0, 1, 2)),
            "sick": rng.choice((0, 0, 1, 1, 2, 3)),
            "injury": 0,
            "vacation": rng.choice((0, 1, 1, 2, 3, 4)),
            "other_absent": rng.choice((0, 0, 0, 1, 2)),
            "truant": rng.choice((0, 0, 0, 0, 1)),
            "day_off": sum(day_off),
            "mob": rng.choice((0, 0, 1, 2)),
            "training": rng.choice((0, 0, 0, 1)),
            "rs_total": rs_total,
            "rs_ok_start": rs_total - repair_start,
            "rs_repair_start": repair_start,
            "rs_ok_end": rs_total - repair_end,
            "rs_repair_end": repair_end,
        }

    def _rails(self) -> list[dict]:
        rng = self.rng
        out = []
        for cargo in rng.sample(CARGOS, rng.randint(3, 6)):
            start_l, start_e = rng.randint(6, 62), rng.randint(0, 26)
            processed = rng.randint(5, 48)
            out.append({
                "cargo": _spell(rng, CARGO_RULES[cargo][3]),
                "station": rng.randint(0, 42),
                "start_loaded": start_l,
                "start_empty": start_e,
                "processed": processed,
                "end_loaded": max(0, start_l - processed // 2 + rng.randint(0, 22)),
                "end_empty": max(0, start_e - processed // 2 + rng.randint(0, 14)),
            })
        return out

    def _trucks(self) -> list[dict]:
        """Автотранспорт — строки в вагонах-эквивалентах, а не в тоннах.

        Витрина считает эти колонки как «автомашин обработано» (так и подписано
        на плитке «Статистики»), поэтому порядок цифр — десятки за смену, а не
        тысячи: 6–8 тыс. автомашин за месяц рядом с 7 тыс. вагонов выглядят
        портом, а 240 тыс. — уже ошибкой разбора. Долька автовывоза у нас
        маленькая сознательно: груз летит вагоном и судном.
        """
        rng = self.rng
        out = []
        for cargo in rng.sample(CARGOS, rng.randint(3, 6)):
            row = {
                "cargo": _spell(rng, CARGO_RULES[cargo][3]) + " а/м",
                "imp_unload": rng.choice((0, 0, rng.randint(1, 12))),
                "exp_unload": rng.choice((0, rng.randint(1, 12), rng.randint(1, 12))),
                "imp_load": rng.choice((0, 0, rng.randint(1, 7))),
                "exp_load": rng.choice((0, rng.randint(1, 12), rng.randint(1, 12))),
                "direct": rng.choice((0, 0, 0, rng.randint(1, 9))),
            }
            row["total"] = sum(row[k] for k in ("imp_unload", "exp_unload",
                                                "imp_load", "exp_load", "direct"))
            out.append(row)
        return out

    def _arrivals(self, begin: datetime) -> list[dict]:
        """«Суда на подходе»: очередь заходов, которые ещё не встали к причалу.

        Список смотрит вперёд (до `AHEAD_DAYS` суток за концом файла): карточка
        плановых заходов обязана что-то показывать, а диспетчер в живом отчёте
        держит очередь всегда — и на сегодня, и на неделю вперёд. Сюда же
        попадают суда, которым не хватило причала (ждут черёды).
        """
        slots = dxl.ROW_ARRIVAL_LAST - dxl.ROW_ARRIVAL_FIRST + 1
        pending = [c for c in self.calls if not c.arrived and c.eta >= begin]
        upcoming = (self.waiting + pending)
        seen: set[str] = set()
        rows = []
        for call in sorted(upcoming, key=lambda c: c.eta):
            if call.vessel in seen:
                continue
            seen.add(call.vessel)
            rows.append({
                "eta": _round_dt(call.eta),
                "vessel": call.vessel,
                "berth": call.berth,
                "work_kind": call.work_kind,
                "cargo": call.cargo,
                "plan": call.plan_raw,
                "agent": call.agent,
            })
            if len(rows) >= slots:
                break
        return rows

    def _rail_arrivals(self) -> list[dict]:
        rng = self.rng
        out = []
        for _ in range(rng.randint(3, 6)):
            out.append({
                "station": rng.choice(STATIONS),
                "cargo": _spell(rng, CARGO_RULES[rng.choice(CARGOS)][3]),
                "cars": rng.choice((rng.randint(15, 72), rng.randint(15, 72),
                                    rng.randint(15, 72), 0)),
                "work_kind": rng.choice(("в", "в", "п")),
                "wagon_type": rng.choice(WAGON_TYPES),
                "train_index": f"{rng.choice(TRAIN_PREFIXES)}-{rng.randint(1, 9):03d}",
                "note": rng.choice(("", "15 км", "под станцию", "с рассортировкой", "сдвоенный")),
            })
        return out

    def _rail_out(self, day: date) -> list[dict]:
        """Блок «ВЫВОД»: состав вывели, кто принял и насколько опоздал к плану."""
        rng = self.rng
        out = []
        for _ in range(rng.randint(1, 3)):
            semi, covered, flat = rng.randint(0, 58), rng.randint(0, 14), rng.randint(0, 10)
            total = semi + covered + flat
            if not total:
                continue
            plan_at = datetime.combine(day, time(rng.randint(0, 23),
                                                 rng.choice((0, 10, 20, 30, 45))))
            fact_at = plan_at + timedelta(minutes=rng.choice((-10, 0, 5, 12, 20, 35, 55, 80)))
            fact = max(0, total - rng.choice((0, 0, 0, 2, 4)))
            out.append({
                "date": plan_at,
                "plan_time": plan_at.strftime("%H:%M"),
                "semi": semi, "covered": covered, "flat": flat, "total": total,
                "accepted": rng.choice(ACCEPTORS),
                "fact": fact or total,
                "fact_date": fact_at,                 # вывод за полночь — обычный случай
                "fact_time": fact_at.strftime("%H:%M"),
            })
        return out

    def _rail_in(self, day: date) -> list[dict]:
        """Блок «ВВОД»: постановка в порт, гружёные и порожние по трём типам."""
        rng = self.rng
        keys = ("load_semi", "load_covered", "load_flat", "empty_semi", "empty_covered",
                "empty_flat")
        out = []
        for _ in range(rng.randint(1, 3)):
            plan_at = datetime.combine(day, time(rng.randint(0, 23),
                                                 rng.choice((0, 15, 30, 50))))
            settle = plan_at + timedelta(minutes=rng.randint(30, 110))
            # Состав обычно приходит одним родом вагонов, остальные — добавка
            # 1..3 единицами: «по 5 полувагонов и 5 крытых» диспетчер пишет редко.
            row = {"load_semi": rng.choice((0, rng.randint(12, 62))),
                   "load_covered": rng.choice((0, 0, rng.randint(2, 14))),
                   "load_flat": rng.choice((0, 0, rng.randint(1, 10))),
                   "empty_semi": rng.choice((0, rng.randint(6, 34))),
                   "empty_covered": rng.choice((0, 0, rng.randint(1, 12))),
                   "empty_flat": rng.choice((0, 0, rng.randint(1, 8)))}
            if not any(row.values()):
                row["load_semi"] = rng.randint(20, 60)
            row.update({
                "date": plan_at,
                "plan_time": plan_at.strftime("%H:%M"),
                "settle_time": settle.strftime("%H:%M"),
            })
            row["total"] = sum(row[k] for k in keys)
            if row["total"]:
                out.append(row)
        return out

    def _platforms(self) -> list[dict]:
        rng = self.rng
        out = []
        for owner in PLATE_OWNERS:
            state = self.platform_state[owner]
            # Парк платформ — штуки: дробных вагонов в отчёте не бывает, а баланс
            # переходит из смены в смену, поэтому остаток копим целым шагом.
            state["remaining"] = float(max(0, min(int(state["total"]),
                                                 int(state["remaining"])
                                                 + rng.choice((-4, -2, 0, 2, 4)))))
            out.append({
                "owner": owner,
                "total": state["total"],
                "remaining": state["remaining"],
                "terminal": rng.randint(0, 9),
                "sent": rng.randint(0, 12),
                "arrived_loaded": rng.choice((0, 0, rng.randint(1, 8))),
            })
        return out


def _planned_calls(rng: Random, start: date, end: date, ahead: int) -> list[_Call]:
    """Поток плановых заходов на весь горизонт: ~1 судно в 1–2 суток.

    Имена берём по кругу из перемешанного списка и не даём пересечься двум
    одноимённым судам: `vessels_top` склеивает строки по имени, и одинаковые
    имена у разных судов выглядели бы как «одно судно обработало 40 тысяч».
    """
    calls: list[_Call] = []
    names = list(VESSEL_NAMES)
    rng.shuffle(names)
    cursor = 0
    busy: set[str] = set()

    def take_name() -> str:
        nonlocal cursor
        for _ in range(len(names)):
            name = names[cursor % len(names)]
            cursor += 1
            if name not in busy:
                busy.add(name)
                return name
        return f"ОЖИДАНИЕ {len(calls) + 1}"

    moment = datetime.combine(start - timedelta(days=3), time(6, 0))
    horizon = datetime.combine(end + timedelta(days=ahead), time(23, 0))
    while moment <= horizon:
        cargo = rng.choice(CARGOS)
        _, unload_p, tonnage, spellings = CARGO_RULES[cargo]
        tons = round(rng.uniform(*tonnage), -1)
        raw, plan_t = _plan_cell(rng, tons)
        kind = "выгрузка" if rng.random() < unload_p else "погрузка"
        if rng.random() < 0.18:
            kind = "выгрузка+погрузка"
        calls.append(_Call(
            eta=_round_dt(moment.replace(minute=rng.choice((0, 10, 20, 30, 40, 50)))),
            vessel=take_name(),
            berth=rng.choice(BERTHS),
            work_kind=kind,
            cargo=_spell(rng, spellings),
            tons=tons,
            plan_raw=raw,
            plan_t=plan_t,
            agent=rng.choice(AGENTS),
        ))
        if len(busy) >= len(names) - 6:
            busy.clear()          # имена освобождаем «с запасом»: судно уходит за 2–6 смен
        # ~1 заход в 16–44 часа: при 5 причалах и стоянке в 4–6 смен у стенки
        # держатся 3–5 судов, очередь иногда дорастает до «ждут черёды», а на
        # период выходит столько заходов, что все они влезают в карточку
        # плановых заходов вместе с будущими.
        moment += timedelta(hours=rng.uniform(16, 44))
    return calls


# ------------------------------------------------------------------- запись ----

def _put(ws, column: str, row: int, value) -> None:
    """Положить значение, если оно есть: пустую ячейку диспетчер не заполняет."""
    if value is None or value == "":
        return
    if isinstance(value, datetime):
        ws[f"{column}{row}"] = value
        ws[f"{column}{row}"].number_format = DT_FMT
    else:
        ws[f"{column}{row}"] = value


def _write_block(ws, columns: dict, first: int, last: int, rows: list[dict]) -> None:
    """Строки блока — строго в `first..last`.

    Больше слотов блок не читает: за краем разборщик теряет строку молча, а
    диспетчер в живом файле пишет ровно в сетку листа. Поэтому список
    обрезаем, а не дописываем за край.
    """
    for offset, row in enumerate(rows[: last - first + 1]):
        target = first + offset
        for key, column in columns.items():
            _put(ws, column, target, row.get(key))


def _write_headers(ws) -> None:
    """Подписи блоков. Без них разборщик блоки не распознаёт (`_is`) — и лист
    молча приедет пустым, поэтому пишем заголовки в те же строки, что и живая
    раскладка (в т.ч. «Ж/Д на подходе» и «ВВОД» делят строку 47, но разные
    колонки)."""
    for column, text in HEAD_VESSEL.items():
        ws[f"{column}{dxl.ROW_VESSEL_HEAD}"] = text
    ws[f"A{dxl.ROW_RAIL_HEAD - 1}"] = "Обработка вагонов"
    ws[f"A{dxl.ROW_RAIL_HEAD}"] = "№"
    for column, text in HEAD_RAIL.items():
        ws[f"{column}{dxl.ROW_RAIL_HEAD}"] = text
    for column, text in HEAD_TRUCK.items():
        ws[f"{column}{dxl.ROW_RAIL_HEAD}"] = text
    ws[f"N{dxl.ROW_ARRIVAL_HEAD - 1}"] = "Суда на подходе"
    for column, text in HEAD_ARRIVAL.items():
        ws[f"{column}{dxl.ROW_ARRIVAL_HEAD}"] = text
    ws[f"A{dxl.ROW_R_OUT_HEAD - 1}"] = "ВЫВОД"
    for column, text in HEAD_R_OUT.items():
        ws[f"{column}{dxl.ROW_R_OUT_HEAD}"] = text
    ws[f"A{dxl.ROW_R_IN_HEAD - 1}"] = "ВВОД"
    for column, text in HEAD_R_IN.items():
        ws[f"{column}{dxl.ROW_R_IN_HEAD}"] = text
    ws[f"N{dxl.ROW_RAIL_ARR_HEAD - 1}"] = "Ж/Д на подходе"
    for column, text in HEAD_RAIL_ARRIVAL.items():
        ws[f"{column}{dxl.ROW_RAIL_ARR_HEAD}"] = text
    ws[f"A{dxl.ROW_PLAT_HEAD}"] = "ПЛАТФОРМЫ ВЫВЕДЕННЫЕ ИЗ АРЕНДЫ"


def _write_personnel(ws, p: dict) -> None:
    head, brigade, total = dxl.ROW_PERSONNEL_HEAD, dxl.ROW_BRIGADE, dxl.ROW_PERSONNEL_TOTAL
    ws[f"A{head}"] = "ВЫХОД ПЕРСОНАЛА"
    ws[f"C{head}"] = "№ бригады"
    ws[f"G{head}"] = "КОЛИЧЕСТВО"
    ws[f"P{head}"] = "Штат"

    ws[f"A{brigade}"] = "ОСНОВНАЯ БРИГАДА"
    for column, key in (("C", "crew_no"), ("G", "crew_present"),
                        ("P", "crew_staff"), ("H", "sick")):
        ws[f"{column}{brigade}"] = p[key]

    for row, value in zip(range(dxl.ROW_DAY_OFF_FIRST, dxl.ROW_DAY_OFF_LAST + 1),
                          p["day_off_rows"]):
        ws[f"A{row}"] = "ВЫХОДНОГО ДНЯ"
        ws[f"G{row}"] = value

    ws[f"A{dxl.ROW_CONTRACT_FIRST}"] = "ПОДРЯДЧИКИ"
    for row, contractor in zip(range(dxl.ROW_CONTRACT_FIRST, dxl.ROW_CONTRACT_LAST + 1),
                               p["contractors"]):
        ws[f"C{row}"] = contractor["name"]
        ws[f"G{row}"] = contractor["count"]

    ws[f"A{total}"] = "Итого"
    for column, key in (("C", "staff_total"), ("G", "present_total"), ("H", "sick"),
                        ("I", "injury"), ("J", "vacation"), ("K", "other_absent"),
                        ("L", "truant"), ("M", "day_off"), ("N", "mob"), ("O", "training")):
        ws[f"{column}{total}"] = p[key]

    # Ричстакеры: заголовки и «всего» у разборщика — именованные ячейки
    # (`CELL_RS_*`), берём их как есть, а не заводим вторую копию координат.
    ws[dxl.CELL_RS_OK] = "Исправно"
    ws[dxl.CELL_RS_REPAIR] = "Ремонт"
    ws[f"Q{dxl.ROW_RS_START}"] = "начало смены"
    ws[f"S{dxl.ROW_RS_START}"] = p["rs_ok_start"]
    ws[f"U{dxl.ROW_RS_START}"] = p["rs_repair_start"]
    ws[f"Q{dxl.ROW_RS_END}"] = "конец смены"
    ws[f"S{dxl.ROW_RS_END}"] = p["rs_ok_end"]
    ws[f"U{dxl.ROW_RS_END}"] = p["rs_repair_end"]
    ws[dxl.CELL_RS_TOTAL] = p["rs_total"]


def _write_platforms(ws, platforms: list[dict]) -> None:
    """Блок платформ: два владельца бок о бок (A–B и C–D), а не двумя строками."""
    for index, pl in enumerate(platforms[:2]):
        base = index * 2                                  # 0 → A/B, 1 → C/D
        owner_col = chr(ord("A") + base)
        value_col = chr(ord("A") + base + 1)
        ws[f"{owner_col}{dxl.ROW_PLAT_OWNER}"] = pl["owner"]
        ws[f"{owner_col}{dxl.ROW_PLAT_TOTALS}"] = pl["total"]
        ws[f"{value_col}{dxl.ROW_PLAT_TOTALS}"] = pl["remaining"]
        ws[f"{owner_col}{dxl.ROW_PLAT_TERMINAL}"] = pl["terminal"]
        ws[f"{value_col}{dxl.ROW_PLAT_TERMINAL}"] = pl["sent"]
        ws[f"{value_col}{dxl.ROW_PLAT_ARRIVED}"] = pl["arrived_loaded"]


def _shift_title(day: date, shift: str) -> str:
    """Имя листа. Оно задаёт и смену: `dispatcher_xlsm._shift_of` ищет в нём «dd-dd»."""
    if shift == "20-08":
        return f"{day.day:02d}-{(day + timedelta(days=1)).day:02d}.{day.month:02d}.{day.year}"
    return f"{day.day:02d}.{day.month:02d}.{day.year}"


def _write_sheet(wb, sheet: dict) -> None:
    day, shift = sheet["day"], sheet["shift"]
    ws = wb.create_sheet(title=_shift_title(day, shift))

    # A1 — дата смены. В живом файле на ночном листе там текст «01-02.11.2025»,
    # но `_sheet_date` берёт из такой подписи месяц из ВТОРОЙ части и на стыке
    # месяцев получает несуществующее 31.11 — лист просто выпадает из импорта.
    # Дата в A1 читается раньше подписи (`isinstance(a1, datetime)`), а смену
    # по-прежнему задаёт имя листа, так что дата из A1 — осознанный выбор в
    # пользу того, чтобы демо проходило разбор как есть.
    ws.cell(dxl.ROW_DATE, 1).value = datetime(day.year, day.month, day.day)
    ws.cell(dxl.ROW_SHIFT, 1).value = shift                 # A2 — подпись смены
    ws.cell(dxl.ROW_SHIFT, 2).value = sheet["personnel"]["dispatcher"]   # B2 — диспетчер

    _write_personnel(ws, sheet["personnel"])
    _write_headers(ws)
    _write_block(ws, COL_VESSEL, dxl.ROW_VESSEL_FIRST, dxl.ROW_VESSEL_LAST, sheet["vessels"])
    _write_block(ws, COL_RAIL, dxl.ROW_RAIL_FIRST, dxl.ROW_RAIL_LAST, sheet["rails"])
    _write_block(ws, COL_TRUCK, dxl.ROW_RAIL_FIRST, dxl.ROW_RAIL_LAST, sheet["trucks"])
    _write_block(ws, COL_ARRIVAL, dxl.ROW_ARRIVAL_FIRST, dxl.ROW_ARRIVAL_LAST,
                 sheet["arrivals"])
    _write_block(ws, COL_RAIL_ARRIVAL, dxl.ROW_RAIL_ARR_FIRST, dxl.ROW_RAIL_ARR_LAST,
                 sheet["rail_arrivals"])
    _write_block(ws, COL_R_OUT, dxl.ROW_R_OUT_FIRST, dxl.ROW_R_OUT_LAST, sheet["rail_out"])
    _write_block(ws, COL_R_IN, dxl.ROW_R_IN_FIRST, dxl.ROW_R_IN_LAST, sheet["rail_in"])
    _write_platforms(ws, sheet["platforms"])


# --------------------------------------------------------------------- вход ----

_EPOCH = datetime(2020, 1, 1)        # фиксированная метка сборки — см. `build_report`
_MODIFIED_RE = re.compile(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)")
_MODIFIED_TO = rb"\g<1>2020-01-01T00:00:00Z\g<2>"


def _stable_zip(data: bytes) -> bytes:
    """Пересобрать архив с неизменными метками записей (см. `build_report`).

    Мешают быть воспроизводимым двум вещам: zip кладёт в каждую запись время
    сборки, а openpyxl в `docProps/core.xml` — `dcterms:modified` от `now()`
    (это делает `save_workbook`, обойти его свойством `wb.properties.modified`
    нельзя). Имя файла от этого не портится: Excel смотрит на содержимое, а
    разборщик — только на листы.
    """
    stamp = _EPOCH.timetuple()[:6]
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            payload = src.read(item.filename)
            if item.filename == "docProps/core.xml":
                payload = _MODIFIED_RE.sub(_MODIFIED_TO, payload)
            info = zipfile.ZipInfo(item.filename, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = item.external_attr
            dst.writestr(info, payload)
    return out.getvalue()


def build_report(*, days: int, end: date | None = None, seed: int = 42) -> bytes:
    """Собрать «Отчет диспетчера.xlsm» на `days` суток, заканчивая `end`.

    Один лист = одна смена (`08-20` и `20-08`). Ночную смену последнего дня не
    пишем: её в живом отчёте ещё не существует — диспетчер сдаст её завтра
    утром. «Суда на подходе» при этом уезжают на `AHEAD_DAYS` суток вперёд,
    иначе карточка плановых заходов остаётся пустой.
    """
    end_day = end or date.today()
    total = max(int(days), 1)
    start_day = end_day - timedelta(days=total - 1)
    rng = Random(seed)
    calls = _planned_calls(rng, start_day, end_day, AHEAD_DAYS)

    port = _Port(rng, calls)
    # 4 суток «до начала отчёта» порт уже жил: иначе первый лист отчёта
    # застал бы пустые причалы, а грузопоток — с нуля.
    warm_start = start_day - timedelta(days=4)
    for warm in range(8):
        port.step(warm_start + timedelta(days=warm // 2),
                  "08-20" if warm % 2 == 0 else "20-08")

    wb = openpyxl.Workbook()
    wb.remove(wb.active)                    # дефолтный «Sheet» в отчёте не нужен
    # Файл обязан быть воспроизводимым: openpyxl кладёт в свойства дату сборки,
    # а zip — время модификации каждого элемента. Без этого один и тот же seed
    # давал бы разные байты, и «проверь, что ничего не поменялось» сводилось бы
    # к побайтовому сравнению двух заведомо разных файлов.
    wb.properties.created = _EPOCH
    wb.properties.modified = _EPOCH
    for offset in range(total):
        day = start_day + timedelta(days=offset)
        for shift in ("08-20", "20-08"):
            if day == end_day and shift == "20-08":
                continue                    # ночной смены сегодня ещё нет
            _write_sheet(wb, port.step(day, shift))

    buf = io.BytesIO()
    wb.save(buf)
    return _stable_zip(buf.getvalue())
