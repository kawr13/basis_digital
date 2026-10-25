"""Разбор «Отчет диспетчера.xlsm» — суточного отчёта диспетчера порта.

Лист = одна смена (сутки × 08-20 / 20-08). Раскладка листа многолетняя и в
файле заказчика одинаковая, поэтому разбор идёт по фиксированным ячейкам, НО
каждый блок дополнительно проверяется по заголовку: если диспетчер вставит
строку, мы не начнём молча читать мусор — блок просто не распознаётся и
останется пустым, а в `warnings` попадёт имя листа.

Что читается (строки листа):
  1..13  шапка: дата, смена, диспетчер, выход персонала, ричстакеры
  15..22 обработка судов (план/факт, производительность, потери времени)
  25..37 обработка вагонов (B..L) и автомашин (N..V) — два блока на одних строках
  39..45 «Суда на подходе»
  47..55 «Ж/Д на подходе»

Значения в листе — «как у человека»: «-» вместо нуля, «129+130» вместо числа,
пробелы и переносы в подписях. Всё это чинит `_num` / `_text`.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Callable

import openpyxl
from openpyxl.utils import get_column_letter as col

from .dispatcher import (
    PlatformRec, RailArrivalRec, RailDeliveryRec, RailDepartureRec, RailOpRec,
    ShiftRec, DispatcherSnapshot, TruckOpRec, VesselArrivalRec, VesselOpRec,
)

# --- координаты блоков (1-based, как в Excel) --------------------------------
ROW_DATE = 1
ROW_SHIFT = 2
ROW_PERSONNEL_HEAD = 3
ROW_BRIGADE = 4
ROW_DAY_OFF_FIRST = 5
ROW_DAY_OFF_LAST = 6
ROW_CONTRACT_FIRST = 7
ROW_CONTRACT_LAST = 12
ROW_PERSONNEL_TOTAL = 13

CELL_RS_OK = "S7"          # заголовок «Исправно»
CELL_RS_REPAIR = "U7"      # заголовок «Ремонт»
ROW_RS_START = 8           # «начало смены»
ROW_RS_END = 10            # «конец смены»
CELL_RS_TOTAL = "W9"

ROW_VESSEL_HEAD = 15
ROW_VESSEL_FIRST = 16
ROW_VESSEL_LAST = 22

ROW_RAIL_HEAD = 25
ROW_RAIL_FIRST = 27
ROW_RAIL_LAST = 36
ROW_RAIL_TOTAL = 37

ROW_ARRIVAL_HEAD = 39
ROW_ARRIVAL_FIRST = 40
ROW_ARRIVAL_LAST = 45

ROW_RAIL_ARR_HEAD = 47
ROW_RAIL_ARR_FIRST = 49
ROW_RAIL_ARR_LAST = 55

# «ВЫВОД» (выводка вагонов из порта): заголовки 41/42, строки 43..45
ROW_R_OUT_HEAD = 41
ROW_R_OUT_FIRST = 43
ROW_R_OUT_LAST = 45
# «ВВОД» (постановка вагонов в порт): заголовки 47/48, строки 49..51
ROW_R_IN_HEAD = 47
ROW_R_IN_FIRST = 49
ROW_R_IN_LAST = 51
# «ПЛАТФОРМЫ, выведенные из аренды»: заголовок 53, владельцы 54,
# всего/остаток 57, терминал/отправлено 59, прибыло гружёными 60
ROW_PLAT_HEAD = 53
ROW_PLAT_OWNER = 54
ROW_PLAT_TOTALS = 57
ROW_PLAT_TERMINAL = 59
ROW_PLAT_ARRIVED = 60

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


# ------------------------------------------------------------------ утилиты ----

def _text(v: Any) -> str:
    """Ячейка → строка без «-» и лишних пробелов."""
    if v is None:
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(int(v)) if float(v).is_integer() else str(v)
    s = str(v).replace("\n", " ").strip()
    return "" if s in {"-", "—", "–", "-"} else s


def _num(v: Any) -> float | None:
    """Ячейка → число. «-»/пусто → None; «129+130» → 259; «1 234,5 тн» → 1234.5."""
    if v is None or isinstance(v, (datetime, date)):
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s in {"-", "—", "–"}:
        return None
    s = s.replace("\u00a0", " ").replace(" ", "")
    if "+" in s:
        parts = [_num(p) for p in s.split("+")]
        parts = [p for p in parts if p is not None]
        return sum(parts) if parts else None
    s = re.sub(r"[^0-9.,-]", "", s).replace(",", ".")
    if s.count(".") > 1:                 # «1.234.567» — разделители разрядов
        head, *tail = s.split(".")
        s = head + "".join(tail)
    try:
        return float(s)
    except ValueError:
        return None


def _sum_numbers(v: Any) -> float:
    """План вида «87+0/3539 тн» → сумма всех чисел (87 + 3539)."""
    total = 0.0
    for m in NUM_RE.finditer(str(v or "")):
        total += float(m.group(0).replace(",", "."))
    return total


def _dt(v: Any) -> datetime | None:
    return v if isinstance(v, datetime) else None


def _hhmm(v: Any) -> str:
    """Время листа в «ЧЧ:ММ». Excel отдаёт и time, и datetime, и текст «23:30»."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return f"{v.hour:02d}:{v.minute:02d}"
    if isinstance(v, time):
        return f"{v.hour:02d}:{v.minute:02d}"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        minutes = round(float(v) * 24 * 60) % (24 * 60)   # доля суток
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    m = re.search(r"(\d{1,2}):(\d{2})", str(v))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def _day(v: Any) -> date | None:
    """Дата ячейки: datetime → date, текст «01.11.2025» → date."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", str(v or ""))
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(round(n)) if n is not None else None


def _sheet_date(ws, name: str) -> date | None:
    """Дата смены. Лист дня: A1 — дата. Ночной лист: A1 — текст «01-02.11.2025»."""
    a1 = ws.cell(ROW_DATE, 1).value
    if isinstance(a1, datetime):
        return a1.date()
    if isinstance(a1, date):
        return a1
    for raw in (str(a1 or ""), name.strip()):
        s = raw.strip()
        if not s:
            continue
        m = re.match(r"^(\d{1,2})\.(\d{1,2})-(.+)$", s)      # 31.10-01.11.2025
        if m:
            day, month, rest = int(m.group(1)), int(m.group(2)), m.group(3)
        else:
            m = re.match(r"^(\d{1,2})-(.+)$", s)              # 30-01.11-12.2025
            if not m:
                continue
            day, rest = int(m.group(1)), m.group(2)
            nums = [int(x) for x in re.findall(r"\d+", rest)]
            if len(nums) < 2:
                continue
            month = nums[1] if len(nums) >= 3 else nums[0]    # «02.11.2025» → 11
        year = [int(x) for x in re.findall(r"\d{4}", rest)]
        if not year:
            continue
        try:
            return date(year[-1], month, day)
        except ValueError:
            continue
    return None


def _shift_of(ws, sheet_date: date | None, name: str) -> str:
    """Смена. Главный признак — имя листа: «01.11.2025» = день, «01-02.11.2025» = ночь.

    Подпись в A2 диспетчеры иногда забывают поменять (в файле 2025-11 таких
    листа два: «16-17.11.2025» с A2=08-20). Если бы мы верили A2, ночная смена
    перезаписала бы дневную того же дня — имя листа надёжнее.
    """
    stripped = name.strip()
    if re.search(r"\d\s*[-–]\s*\d", stripped):
        return "20-08"
    raw = _text(ws.cell(ROW_SHIFT, 1).value)
    m = re.match(r"^(\d{1,2})\s*[-–]\s*(\d{1,2})$", raw)
    if m:
        return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return "08-20"


def _is(ws, row: int, column: str, startswith: str) -> bool:
    """Проверка якоря блока: заголовок на месте → блок читаем."""
    return _text(ws.cell(row, openpyxl.utils.column_index_from_string(column)).value) \
        .lower().startswith(startswith.lower())


# -------------------------------------------------------------------- блоки ----

def _personnel(ws) -> dict:
    """Выход персонала + ричстакеры (строки 3..13 и S/U)."""
    if not _is(ws, ROW_PERSONNEL_HEAD, "A", "ВЫХОД ПЕРСОНАЛА"):
        return {}
    contractors = []
    for r in range(ROW_CONTRACT_FIRST, ROW_CONTRACT_LAST + 1):
        name = _text(ws.cell(r, 3).value)          # C — имя подрядчика
        if not name or name.upper() == "ПОДРЯДЧИКИ":
            continue
        count = _num(ws.cell(r, 7).value) or 0     # G — «КОЛИЧЕСТВО»
        contractors.append({"name": name, "count": count})

    total_row = ROW_PERSONNEL_TOTAL
    rs_total = _num(ws[CELL_RS_TOTAL].value) or 0
    return {
        "crew_no": _int(ws.cell(ROW_BRIGADE, 3).value),
        "crew_present": _num(ws.cell(ROW_BRIGADE, 7).value) or 0,
        "crew_staff": _num(ws.cell(ROW_BRIGADE, 16).value) or 0,      # P — «Штат»
        "crew_day_off": sum(
            _num(ws.cell(r, 7).value) or 0
            for r in range(ROW_DAY_OFF_FIRST, ROW_DAY_OFF_LAST + 1)
            if _text(ws.cell(r, 1).value).upper().startswith("ВЫХОДНОГО")
        ),
        "contractors": contractors,
        "present_total": _num(ws.cell(total_row, 7).value) or 0,
        "staff_total": _num(ws.cell(total_row, 3).value) or 0,
        "sick": _num(ws.cell(total_row, 8).value) or 0,
        "injury": _num(ws.cell(total_row, 9).value) or 0,
        "vacation": _num(ws.cell(total_row, 10).value) or 0,
        "other_absent": _num(ws.cell(total_row, 11).value) or 0,
        "truant": _num(ws.cell(total_row, 12).value) or 0,
        "day_off": _num(ws.cell(total_row, 13).value) or 0,
        "mob": _num(ws.cell(total_row, 14).value) or 0,
        "training": _num(ws.cell(total_row, 15).value) or 0,
        "rs_ok_start": _num(ws[f"S{ROW_RS_START}"].value) or 0,
        "rs_repair_start": _num(ws[f"U{ROW_RS_START}"].value) or 0,
        "rs_ok_end": _num(ws[f"S{ROW_RS_END}"].value) or 0,
        "rs_repair_end": _num(ws[f"U{ROW_RS_END}"].value) or 0,
        "rs_total": rs_total,
    }


def _vessels(ws) -> list[VesselOpRec]:
    if not _is(ws, ROW_VESSEL_HEAD, "B", "Судно"):
        return []
    out: list[VesselOpRec] = []
    for r in range(ROW_VESSEL_FIRST, ROW_VESSEL_LAST + 1):
        vessel = _text(ws.cell(r, 2).value)                       # B — Судно
        if not vessel:
            continue
        plan_unload = _num(ws.cell(r, 4).value) or 0.0            # D — план выгрузки
        plan_load = _num(ws.cell(r, 5).value) or 0.0              # E — план погрузки
        plan_total = _num(ws.cell(r, 6).value)                     # F — ∑ (формула)
        on_board = _num(ws.cell(r, 12).value) or 0.0              # L — на борту/выгружено
        remaining = _num(ws.cell(r, 13).value)                     # M — остаток (формула)
        shift_hours = _num(ws.cell(r, 23).value) or 0.0           # W — сменное время
        downtime = _num(ws.cell(r, 24).value) or 0.0              # X — потери
        machine = _num(ws.cell(r, 25).value)                       # Y = W - X (формула)
        fact = _num(ws.cell(r, 11).value) or 0.0                   # K — факт за смену
        if machine is None:
            machine = max(shift_hours - downtime, 0.0)
        tph = _num(ws.cell(r, 14).value)                           # N — производительность
        if tph is None:
            tph = fact / machine if machine > 0 else 0.0
        out.append(VesselOpRec(
            berth=_text(ws.cell(r, 1).value),                      # A — № пр
            vessel=vessel,
            cargo_group=_text(ws.cell(r, 3).value),                # C — Род груза
            plan_unload_t=plan_unload,
            plan_load_t=plan_load,
            plan_total_t=plan_total if plan_total is not None else plan_unload + plan_load,
            plan_shift_t=_num(ws.cell(r, 10).value) or 0.0,        # J — план на смену
            fact_shift_t=fact,
            on_board_t=on_board,
            remaining_t=remaining if remaining is not None
            else max((plan_unload + plan_load) - on_board, 0.0),
            productivity_tph=tph,
            moves=_int(ws.cell(r, 15).value) or 0,                 # O — кол-во ходов
            shift_hours=shift_hours,
            downtime_hours=downtime,
            machine_hours=machine,
            plan_finish_at=_dt(ws.cell(r, 7).value),
            berthed_at=_dt(ws.cell(r, 8).value),
            started_at=_dt(ws.cell(r, 9).value),
            note=_text(ws.cell(r, 17).value),                      # Q — примечания
        ))
    return out


def _rails(ws) -> list[RailOpRec]:
    if not _is(ws, ROW_RAIL_HEAD, "B", "Грузы"):
        return []
    out: list[RailOpRec] = []
    for r in range(ROW_RAIL_FIRST, ROW_RAIL_LAST + 1):
        cargo = _text(ws.cell(r, 2).value)                         # B — Грузы
        if not cargo:
            continue
        out.append(RailOpRec(
            cargo_group=cargo,
            station_start=_num(ws.cell(r, 4).value) or 0.0,       # D — на станции начало
            port_start_loaded=_num(ws.cell(r, 7).value) or 0.0,   # G
            port_start_empty=_num(ws.cell(r, 8).value) or 0.0,    # H
            processed=_num(ws.cell(r, 9).value) or 0.0,           # I — обработано
            port_end_loaded=_num(ws.cell(r, 11).value) or 0.0,    # K
            port_end_empty=_num(ws.cell(r, 12).value) or 0.0,     # L
        ))
    return out


def _trucks(ws) -> list[TruckOpRec]:
    if not _is(ws, ROW_RAIL_HEAD, "O", "Грузы"):
        return []
    out: list[TruckOpRec] = []
    for r in range(ROW_RAIL_FIRST, ROW_RAIL_LAST + 1):
        cargo = _text(ws.cell(r, 15).value)                        # O — Грузы
        if not cargo:
            continue
        imp_unload = _num(ws.cell(r, 17).value) or 0.0            # Q — принял/выгрузка импорт
        exp_unload = _num(ws.cell(r, 18).value) or 0.0            # R — растарка/выгрузка экспорт
        imp_load = _num(ws.cell(r, 19).value) or 0.0              # S — затарка/погрузка импорт
        exp_load = _num(ws.cell(r, 20).value) or 0.0              # T — затарка/погрузка экспорт
        direct = _num(ws.cell(r, 21).value) or 0.0                 # U — с т/х в а/м
        total = _num(ws.cell(r, 22).value)                         # V — ВСЕГО (формула)
        out.append(TruckOpRec(
            cargo_group=cargo, imp_unload=imp_unload, exp_unload=exp_unload,
            imp_load=imp_load, exp_load=exp_load, direct_vessel=direct,
            total=total if total is not None
            else imp_unload + exp_unload + imp_load + exp_load + direct,
        ))
    return out


def _arrivals(ws) -> list[VesselArrivalRec]:
    if not _is(ws, ROW_ARRIVAL_HEAD, "N", "Дата захода"):
        return []
    out: list[VesselArrivalRec] = []
    for r in range(ROW_ARRIVAL_FIRST, ROW_ARRIVAL_LAST + 1):
        vessel = _text(ws.cell(r, 15).value)                       # O — Судно
        if not vessel:
            continue
        plan_raw = ws.cell(r, 20).value                            # T — План
        out.append(VesselArrivalRec(
            eta=_dt(ws.cell(r, 14).value),                         # N — дата захода
            vessel=vessel,
            berth=_text(ws.cell(r, 17).value),                     # Q — Причал
            work_kind=_text(ws.cell(r, 18).value),                 # R — Вид работ
            cargo_group=_text(ws.cell(r, 19).value),               # S — Вид груза
            plan_t=_sum_numbers(plan_raw),
            plan_raw=_text(plan_raw),
            agent=_text(ws.cell(r, 21).value),                     # U — Агент
        ))
    return out


def _rail_arrivals(ws) -> list[RailArrivalRec]:
    if not _is(ws, ROW_RAIL_ARR_HEAD, "N", "Станция"):
        return []
    out: list[RailArrivalRec] = []
    for r in range(ROW_RAIL_ARR_FIRST, ROW_RAIL_ARR_LAST + 1):
        station = _text(ws.cell(r, 14).value)                      # N — Станция
        cargo = _text(ws.cell(r, 15).value)                        # O — Груз
        cars = _num(ws.cell(r, 17).value)                          # Q — Кол-во вагонов
        if not station and not cargo:
            continue
        out.append(RailArrivalRec(
            station=station, cargo_group=cargo, cars=cars or 0.0,
            work_kind=_text(ws.cell(r, 18).value),                 # R — Вид работ
            wagon_type=_text(ws.cell(r, 19).value),                # S — Тип вагона
            train_index=_text(ws.cell(r, 20).value),               # T — Индекс поезда
            note=_text(ws.cell(r, 21).value),                      # U — Примечание
        ))
    return out




def _rail_out(ws) -> list[RailDepartureRec]:
    """Блок «ВЫВОД»: дата, время план, вагоны по типам, кто принял, факт."""
    if not _is(ws, ROW_R_OUT_HEAD, "A", "Дата") or not _is(ws, ROW_R_OUT_HEAD, "J", "ВЫВЕДЕНО"):
        return []
    out: list[RailDepartureRec] = []
    for r in range(ROW_R_OUT_FIRST, ROW_R_OUT_LAST + 1):
        total = _num(ws.cell(r, 8).value) or 0.0                    # H — итого
        fact = _num(ws.cell(r, 10).value) or 0.0                    # J — выведено по факту
        semi = _num(ws.cell(r, 3).value) or 0.0                     # C — полувагоны
        covered = _num(ws.cell(r, 4).value) or 0.0                  # D — крытые
        flat = _num(ws.cell(r, 7).value) or 0.0                     # G — к/платформа
        accepted = _text(ws.cell(r, 9).value)                       # I — кто принял
        when = _day(ws.cell(r, 1).value)                            # A — дата
        if not (total or fact or semi or covered or flat or accepted or when):
            continue
        out.append(RailDepartureRec(
            row_no=r, move_date=when, plan_time=_hhmm(ws.cell(r, 2).value),
            semi_wagons=semi, covered=covered, flatcars=flat, total=total or fact,
            accepted_by=accepted, fact_cars=fact or total,
            fact_date=_day(ws.cell(r, 11).value),                  # K — дата факта
            fact_time=_hhmm(ws.cell(r, 12).value),                  # L — время факта
        ))
    return out


def _rail_in(ws) -> list[RailDeliveryRec]:
    """Блок «ВВОД»: постановка вагонов, гружёные/порожние по трём типам."""
    if not _is(ws, ROW_R_IN_HEAD, "A", "Дата") or not _is(ws, ROW_R_IN_HEAD, "L", "Итого"):
        return []
    out: list[RailDeliveryRec] = []
    for r in range(ROW_R_IN_FIRST, ROW_R_IN_LAST + 1):
        rec = RailDeliveryRec(
            row_no=r,
            move_date=_day(ws.cell(r, 1).value),                    # A — Дата
            plan_time=_hhmm(ws.cell(r, 2).value),                   # B — время план/факт
            settle_time=_hhmm(ws.cell(r, 3).value),                 # C — время расстановки
            load_semi=_num(ws.cell(r, 4).value) or 0.0,             # D — гружёные п/в
            load_covered=_num(ws.cell(r, 7).value) or 0.0,          # G — крытые
            load_flat=_num(ws.cell(r, 8).value) or 0.0,             # H — платформы
            empty_semi=_num(ws.cell(r, 9).value) or 0.0,            # I — порожние п/в
            empty_covered=_num(ws.cell(r, 10).value) or 0.0,        # J — крытые
            empty_flat=_num(ws.cell(r, 11).value) or 0.0,           # K — платформы
            total=_num(ws.cell(r, 12).value) or 0.0,                # L — итого
        )
        if not (rec.total or rec.load_semi or rec.load_covered or rec.load_flat
                or rec.empty_semi or rec.empty_covered or rec.empty_flat or rec.move_date):
            continue
        if not rec.total:
            rec.total = (rec.load_semi + rec.load_covered + rec.load_flat
                         + rec.empty_semi + rec.empty_covered + rec.empty_flat)
        out.append(rec)
    return out


def _platforms(ws) -> list[PlatformRec]:
    """Блок «платформы, выведенные из аренды»: два владельца бок о бок (A-B и C-D)."""
    if not _is(ws, ROW_PLAT_HEAD, "A", "ПЛАТФОРМЫ"):
        return []
    out: list[PlatformRec] = []
    for base in (1, 3):                                             # A..B и C..D
        owner = _text(ws.cell(ROW_PLAT_OWNER, base).value)
        if not owner:
            continue
        out.append(PlatformRec(
            owner=owner,
            total_cars=_num(ws.cell(ROW_PLAT_TOTALS, base).value) or 0.0,
            remaining=_num(ws.cell(ROW_PLAT_TOTALS, base + 1).value) or 0.0,
            terminal=_num(ws.cell(ROW_PLAT_TERMINAL, base).value) or 0.0,
            sent=_num(ws.cell(ROW_PLAT_TERMINAL, base + 1).value) or 0.0,
            arrived_loaded=_num(ws.cell(ROW_PLAT_ARRIVED, base + 1).value) or 0.0,
        ))
    return out

# ------------------------------------------------------------------ лист ----

def parse_sheet(ws, name: str | None = None) -> ShiftRec | None:
    """Один лист → одна смена. Пустой/нераспознанный лист → None."""
    name = name or ws.title
    sheet_date = _sheet_date(ws, name)
    if sheet_date is None:
        return None
    rec = ShiftRec(
        report_date=sheet_date,
        shift=_shift_of(ws, sheet_date, name),
        sheet=name.strip(),
        dispatcher=_text(ws.cell(ROW_SHIFT, 2).value),              # B2 — ФИО диспетчера
        **_personnel(ws),
    )
    rec.vessels = _vessels(ws)
    rec.rails = _rails(ws)
    rec.trucks = _trucks(ws)
    rec.arrivals = _arrivals(ws)
    rec.rail_arrivals = _rail_arrivals(ws)
    rec.rail_out = _rail_out(ws)
    rec.rail_in = _rail_in(ws)
    rec.platforms = _platforms(ws)
    if not (rec.vessels or rec.rails or rec.trucks or rec.arrivals or rec.present_total):
        return None
    return rec


def parse_workbook(source: str | Path | bytes | io.IOBase) -> DispatcherSnapshot:
    """Файл (путь или bytes) → снимок витрины. Скрытые листы не читаем."""
    data = io.BytesIO(source) if isinstance(source, bytes) else source
    wb = openpyxl.load_workbook(data, data_only=True, keep_vba=False)
    shifts: dict[tuple[date, str], ShiftRec] = {}
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        try:
            rec = parse_sheet(ws)
        except Exception:                       # лист-исключение не роняет импорт
            continue
        if rec:
            shifts[(rec.report_date, rec.shift)] = rec
    return DispatcherSnapshot(shifts=[shifts[k] for k in sorted(shifts)], source="xlsm")


def describe(snapshot: DispatcherSnapshot) -> str:
    """Однострочник для лога/админки: сколько смен и строк."""
    s = snapshot.shifts
    return (
        f"смен {len(s)}; судов {sum(len(x.vessels) for x in s)}; "
        f"вагонов-строк {sum(len(x.rails) for x in s)}; "
        f"машин-строк {sum(len(x.trucks) for x in s)}; "
        f"подходов {sum(len(x.arrivals) for x in s)}; "
        f"выводка {sum(len(x.rail_out) for x in s)}; "
        f"постановка {sum(len(x.rail_in) for x in s)}"
    )
