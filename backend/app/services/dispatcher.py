"""Агрегаты «Отчёта диспетчера» для страницы статистики.

Данных мало (месяц порта = ~60 смен и несколько сотен строк), поэтому считаем
не SQL-комбинаторикой, а один раз читаем строки периода и складываем в Python:
так группировки (груз × сутки × смена, причалы, направления, теплокарта) остаются
читаемыми, а цифры из разных виджетов физически берутся из одного набора строк —
расойтись они не могут.

Единицы: тонны — грузопоток судов, вагоны/машины — штуки, часы — время работы.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    FactDispatcherShift, FactPlatformBalance, FactRailArrival, FactRailDelivery,
    FactRailDeparture, FactRailOperation, FactTruckOperation,
    FactVesselArrival, FactVesselOperation,
)
from . import app_settings, data_cache

SHIFT_LABELS = {"08-20": "День 08–20", "20-08": "Ночь 20–08"}
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

# Грузы в отчёте пишут «как слышится»: генгруз / ген.груз / ГЕНГРУЗ — одна и та
# же номенклатура. Ключ нормализуем, отображаемое имя выбираем по правилу
# «не капс важнее капса» (см. _CargoNames).
SYNONYMS = {
    "генгруз": "генгруз", "генгруз.": "генгруз", "ген.груз": "генгруз",
    "ктк": "ктк", "ктка/м": "ктк а/м",
    "фрукты": "фрукты", "фруктыбтоф": "фрукты (бтоф)",
    "калий": "калий", "калийнитр": "калий нитрат",
    "карбамид": "карбамид", "карбамидкр/в": "карбамид кр/в",
    "химия": "химия", "химияб/б": "химия б/б",
    "нитрат": "нитрат б/б", "нитратвб/б": "нитрат б/б",
    "оборуде": "оборудование", "оборудование": "оборудование",
    "ктк+генгруз": "ктк + генгруз",
    "удобрения": "удобрения", "удобрение": "удобрения",
    "сталь": "сталь", "металлолом": "металлолом", "трубы": "трубы",
    "чермет": "чермет", "электрод": "электрод", "мк14": "МК-14",
    "сборныегрузы": "сборные грузы",
}


def _delay_min(plan_time: str, fact_time: str, plan_date, fact_date) -> float | None:
    """Задержка вывода состава, минуты. Время в листе — «ЧЧ:ММ» без даты."""
    if not plan_time or not fact_time:
        return None
    try:
        ph, pm = (int(x) for x in plan_time.split(":"))
        fh, fm = (int(x) for x in fact_time.split(":"))
    except ValueError:
        return None
    delta = (fh * 60 + fm) - (ph * 60 + pm)
    if plan_date and fact_date:
        return float(delta + (fact_date - plan_date).days * 1440)
    # даты в листе нет: переход через полночь считаем опозданием, а не «-23 часа»
    return float(delta + 1440 if delta < -720 else delta)


class _CargoNames:
    """Ключ груза → отображаемое имя; варианты написания склеиваются в один."""

    def __init__(self) -> None:
        self._by_key: dict[str, str] = {}

    def label(self, raw: str | None) -> str:
        s = re.sub(r"\s+", " ", (raw or "").strip())
        if not s:
            return "без груза"
        key = re.sub(r"[^0-9a-zа-яё+/.-]", "", s.lower())
        key = SYNONYMS.get(key, key)
        current = self._by_key.get(key)
        if current is None or (current.isupper() and not s.isupper()):
            self._by_key[key] = s
        return self._by_key[key]


def _r(v: float, digits: int = 1) -> float:
    return round(float(v or 0), digits)


def _pct(part: float, whole: float, digits: int = 1) -> float | None:
    return round(part / whole * 100, digits) if whole else None


def _contractors(raw: str | None) -> list[dict]:
    try:
        data = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def period_bounds(db: Session) -> tuple[date | None, date | None]:
    """Реальные границы данных — чтобы фронт не предлагал период «из ниоткуда»."""
    lo = db.scalar(select(func.min(FactDispatcherShift.report_date)))
    hi = db.scalar(select(func.max(FactDispatcherShift.report_date)))
    return lo, hi


# ------------------------------------------------------------------ загрузка ----

class Rows:
    """Все строки периода одним куском — дальше только Python."""

    def __init__(self, shifts, vessels, rails, trucks, arrivals, rail_arrivals,
                 rail_out, rail_in, platforms):
        self.shifts = shifts
        self.vessels = vessels
        self.rails = rails
        self.trucks = trucks
        self.arrivals = arrivals
        self.rail_arrivals = rail_arrivals
        self.rail_out = rail_out          # блок «ВЫВОД» листа
        self.rail_in = rail_in            # блок «ВВОД» листа
        self.platforms = platforms        # платформы, выведенные из аренды

    @property
    def days(self) -> list[date]:
        return sorted({s.report_date for s in self.shifts})


def load_rows(db: Session, date_from: date, date_to: date, shift: str | None) -> Rows:
    def where(model):
        stmt = select(model).where(model.report_date >= date_from, model.report_date <= date_to)
        if shift and shift != "all":
            stmt = stmt.where(model.shift == shift)
        return stmt

    return Rows(
        shifts=list(db.scalars(where(FactDispatcherShift).order_by(
            FactDispatcherShift.report_date, FactDispatcherShift.shift.desc()))),
        vessels=list(db.scalars(where(FactVesselOperation))),
        rails=list(db.scalars(where(FactRailOperation))),
        trucks=list(db.scalars(where(FactTruckOperation))),
        arrivals=list(db.scalars(where(FactVesselArrival))),
        rail_arrivals=list(db.scalars(where(FactRailArrival))),
        rail_out=list(db.scalars(where(FactRailDeparture).order_by(
            FactRailDeparture.report_date, FactRailDeparture.shift, FactRailDeparture.row_no))),
        rail_in=list(db.scalars(where(FactRailDelivery).order_by(
            FactRailDelivery.report_date, FactRailDelivery.shift, FactRailDelivery.row_no))),
        platforms=list(db.scalars(where(FactPlatformBalance))),
    )


# ------------------------------------------------------------------- сборка ----

def overview(db: Session, date_from: date, date_to: date, shift: str | None = None) -> dict:
    rows = load_rows(db, date_from, date_to, shift)
    names = _CargoNames()

    days = rows.days
    prev_from = date_from - timedelta(days=(date_to - date_from).days + 1)
    prev_to = date_from - timedelta(days=1)
    prev = load_rows(db, prev_from, prev_to, shift) if prev_to >= date_from else None

    # ---- сутки -------------------------------------------------------------
    per_day: dict[date, dict] = defaultdict(lambda: {
        "throughput_t": 0.0, "plan_shift_t": 0.0, "import_t": 0.0, "export_t": 0.0,
        "calls": 0, "machine_h": 0.0, "downtime_h": 0.0,
        "rail_cars": 0.0, "trucks": 0.0,
        "present": 0.0, "crew": 0.0, "contractors": 0.0, "day_off": 0.0,
        "sick": 0.0, "vacation": 0.0, "other_absent": 0.0, "staff": 0.0,
        "rs_ok_start": 0.0, "rs_repair_start": 0.0, "rs_ok_end": 0.0, "rs_repair_end": 0.0,
        "rs_total": 0.0, "shifts": 0,
    })

    for s in rows.shifts:
        d = per_day[s.report_date]
        d["present"] += s.present_total
        d["crew"] += s.crew_present
        d["day_off"] += s.crew_day_off
        d["contractors"] += sum(c.get("count", 0) for c in _contractors(s.contractors_json))
        d["sick"] += s.sick
        d["vacation"] += s.vacation
        d["other_absent"] += s.other_absent + s.truant + s.mob + s.training + s.injury
        d["staff"] += s.staff_total
        # Ричстакеры по сменам суммировать нельзя (одна и та же техника): берём
        # максимум по сменам суток — честнее, чем складывать 10 + 10 = 20.
        d["rs_ok_start"] = max(d["rs_ok_start"], s.rs_ok_start)
        d["rs_repair_start"] = max(d["rs_repair_start"], s.rs_repair_start)
        d["rs_ok_end"] = max(d["rs_ok_end"], s.rs_ok_end)
        d["rs_repair_end"] = max(d["rs_repair_end"], s.rs_repair_end)
        d["rs_total"] = max(d["rs_total"], s.rs_total or (s.rs_ok_start + s.rs_repair_start))
        d["shifts"] += 1

    for v in rows.vessels:
        d = per_day[v.report_date]
        d["throughput_t"] += v.fact_shift_t
        d["plan_shift_t"] += v.plan_shift_t
        d["calls"] += 1
        d["machine_h"] += v.machine_hours
        d["downtime_h"] += v.downtime_hours

    for r in rows.rails:
        per_day[r.report_date]["rail_cars"] += r.processed
    for t in rows.trucks:
        per_day[t.report_date]["trucks"] += t.total

    # Персонал за сутки — это ДВЕ смены (день и ночь) с разными людьми, а «штат»
    # в обоих сменах считается по одному и тому же списку. Складывать людей
    # нельзя, поэтому по суткам показываем среднюю смену: сравнение «вышел /
    # штат» остаётся честным.
    dynamics = []
    for day in days:
        d = per_day[day]
        n = d["shifts"] or 1
        avg = lambda key: d[key] / n
        dynamics.append({
            "bucket_date": day,
            "throughput_t": _r(d["throughput_t"]),
            "plan_shift_t": _r(d["plan_shift_t"]),
            "plan_pct": _pct(d["throughput_t"], d["plan_shift_t"]),
            "calls": d["calls"],
            "machine_h": _r(d["machine_h"]),
            "downtime_h": _r(d["downtime_h"]),
            "downtime_pct": _pct(d["downtime_h"], d["machine_h"] + d["downtime_h"]),
            "rail_cars": _r(d["rail_cars"], 0),
            "trucks": _r(d["trucks"], 0),
            "present": _r(avg("present")),
            "crew": _r(avg("crew")),
            "contractors": _r(avg("contractors")),
            "day_off": _r(avg("day_off")),
            "sick": _r(avg("sick")),
            "vacation": _r(avg("vacation")),
            "other_absent": _r(avg("other_absent")),
            "staff": _r(avg("staff")),
            "crew_rate": _pct(d["crew"], d["staff"]),
            "shifts": d["shifts"],
            "rs_ok_start": _r(d["rs_ok_start"], 0),
            "rs_repair_start": _r(d["rs_repair_start"], 0),
            "rs_ok_end": _r(d["rs_ok_end"], 0),
            "rs_repair_end": _r(d["rs_repair_end"], 0),
            "rs_total": _r(d["rs_total"], 0),
            "rs_availability": _pct((d["rs_ok_start"] + d["rs_ok_end"]) / 2, d["rs_total"]),
        })

    # ---- грузопоток: план по направлениям (импорт = выгрузка с судна) -------
    direction = {"import_t": 0.0, "export_t": 0.0}
    cargo_mix: dict[str, dict] = defaultdict(lambda: {"tons": 0.0, "calls": 0, "plan_t": 0.0,
                                                      "machine_h": 0.0, "downtime_h": 0.0})
    berths: dict[str, dict] = defaultdict(lambda: {"calls": 0, "tons": 0.0, "machine_h": 0.0,
                                                   "downtime_h": 0.0, "vessels": set()})
    vessels_top: dict[str, dict] = defaultdict(lambda: {"calls": 0, "tons": 0.0,
                                                        "machine_h": 0.0, "downtime_h": 0.0,
                                                        "cargos": set()})
    by_shift: dict[str, dict] = defaultdict(lambda: {"tons": 0.0, "plan_t": 0.0, "calls": 0,
                                                     "machine_h": 0.0, "downtime_h": 0.0})
    by_weekday: dict[int, dict] = defaultdict(lambda: {"tons": 0.0, "days": set()})

    for v in rows.vessels:
        plan_unload, plan_load = v.plan_unload_t, v.plan_load_t
        # Факт смены делим по направлениям пропорционально плану: отдельного
        # «факт выгрузки / факт погрузки» в отчёте нет, только общий факт.
        plan_sum = plan_unload + plan_load
        fact = v.fact_shift_t
        if plan_sum > 0 and fact:
            direction["import_t"] += fact * plan_unload / plan_sum
            direction["export_t"] += fact * plan_load / plan_sum

        label = names.label(v.cargo_group)
        c = cargo_mix[label]
        c["tons"] += fact
        c["plan_t"] += plan_sum
        c["calls"] += 1
        c["machine_h"] += v.machine_hours
        c["downtime_h"] += v.downtime_hours

        b = berths[(v.berth or "—").strip()]
        b["calls"] += 1
        b["tons"] += fact
        b["machine_h"] += v.machine_hours
        b["downtime_h"] += v.downtime_hours
        b["vessels"].add(v.vessel)

        key = (v.vessel or "—").strip().upper()
        vt = vessels_top[key]
        vt["calls"] += 1
        vt["tons"] += fact
        vt["machine_h"] += v.machine_hours
        vt["downtime_h"] += v.downtime_hours
        vt["cargos"].add(label)

        bs = by_shift[v.shift]
        bs["tons"] += fact
        bs["plan_t"] += v.plan_shift_t
        bs["calls"] += 1
        bs["machine_h"] += v.machine_hours
        bs["downtime_h"] += v.downtime_hours

    for day, d in per_day.items():
        wd = day.weekday()
        by_weekday[wd]["tons"] += d["throughput_t"]
        by_weekday[wd]["days"].add(day)

    total_tons = sum(p["throughput_t"] for p in dynamics)

    # ---- вагоны -------------------------------------------------------------
    rail_by_cargo: dict[str, dict] = defaultdict(lambda: {"processed": 0.0, "start": 0.0,
                                                          "end_loaded": 0.0, "end_empty": 0.0,
                                                          "start_loaded": 0.0, "start_empty": 0.0})
    rail_totals = {"processed": 0.0, "start_loaded": 0.0, "start_empty": 0.0,
                   "end_loaded": 0.0, "end_empty": 0.0, "station_start": 0.0}
    for r in rows.rails:
        label = names.label(r.cargo_group)
        c = rail_by_cargo[label]
        c["processed"] += r.processed
        c["start_loaded"] += r.port_start_loaded
        c["start_empty"] += r.port_start_empty
        c["end_loaded"] += r.port_end_loaded
        c["end_empty"] += r.port_end_empty
        rail_totals["processed"] += r.processed
        rail_totals["start_loaded"] += r.port_start_loaded
        rail_totals["start_empty"] += r.port_start_empty
        rail_totals["end_loaded"] += r.port_end_loaded
        rail_totals["end_empty"] += r.port_end_empty
        rail_totals["station_start"] += r.station_start

    # ---- выводка и постановка вагонов (блоки «ВЫВОД» / «ВВОД») --------------
    move_day: dict[date, dict] = defaultdict(lambda: {
        "out_cars": 0.0, "in_cars": 0.0, "in_loaded": 0.0, "in_empty": 0.0,
        "out_semi": 0.0, "out_covered": 0.0, "out_flat": 0.0,
        "in_semi": 0.0, "in_covered": 0.0, "in_flat": 0.0,
        "delays": [], "late": 0,
    })
    move_totals = {"out_cars": 0.0, "in_cars": 0.0, "in_loaded": 0.0, "in_empty": 0.0,
                   "out_semi": 0.0, "out_covered": 0.0, "out_flat": 0.0,
                   "in_semi": 0.0, "in_covered": 0.0, "in_flat": 0.0,
                   "trains_out": 0.0, "delays": [], "late": 0}
    for r in rows.rail_out:
        d = move_day[r.report_date]
        d["out_cars"] += r.total
        d["out_semi"] += r.semi_wagons
        d["out_covered"] += r.covered
        d["out_flat"] += r.flatcars
        move_totals["out_cars"] += r.total
        move_totals["out_semi"] += r.semi_wagons
        move_totals["out_covered"] += r.covered
        move_totals["out_flat"] += r.flatcars
        if r.total:
            move_totals["trains_out"] += 1
        gap = _delay_min(r.plan_time, r.fact_time, r.move_date, r.fact_date)
        if gap is not None:
            d["delays"].append(gap)
            move_totals["delays"].append(gap)
            if gap > 0:
                d["late"] += 1
                move_totals["late"] += 1
    for r in rows.rail_in:
        d = move_day[r.report_date]
        loaded = r.load_semi + r.load_covered + r.load_flat
        empty = r.empty_semi + r.empty_covered + r.empty_flat
        d["in_cars"] += r.total
        d["in_loaded"] += loaded
        d["in_empty"] += empty
        d["in_semi"] += r.load_semi + r.empty_semi
        d["in_covered"] += r.load_covered + r.empty_covered
        d["in_flat"] += r.load_flat + r.empty_flat
        move_totals["in_cars"] += r.total
        move_totals["in_loaded"] += loaded
        move_totals["in_empty"] += empty
        move_totals["in_semi"] += r.load_semi + r.empty_semi
        move_totals["in_covered"] += r.load_covered + r.empty_covered
        move_totals["in_flat"] += r.load_flat + r.empty_flat

    def _avg_delay(values: list[float]) -> float | None:
        # десятая доля минуты лишняя, но округлять до целой нельзя: 32.5 → 32
        return _r(sum(values) / len(values), 1) if values else None

    # платформы, выведенные из аренды: остаток на конец периода и сдвиг за период
    plat_by_owner: dict[str, dict] = {}
    for pl in sorted(rows.platforms, key=lambda x: (x.report_date, x.shift)):
        cur = plat_by_owner.setdefault(pl.owner, {"first": pl, "last": pl})
        cur["last"] = pl

    # ---- автомашины ---------------------------------------------------------
    truck_ops = ("imp_unload", "exp_unload", "imp_load", "exp_load", "direct_vessel")
    truck_by_cargo: dict[str, dict] = defaultdict(lambda: {k: 0.0 for k in truck_ops} | {"total": 0.0})
    truck_totals = {k: 0.0 for k in truck_ops} | {"total": 0.0}
    for t in rows.trucks:
        label = names.label(t.cargo_group)
        c = truck_by_cargo[label]
        for op in truck_ops:
            c[op] += getattr(t, op)
            truck_totals[op] += getattr(t, op)
        c["total"] += t.total
        truck_totals["total"] += t.total

    truck_by_day: dict[date, dict] = defaultdict(lambda: {k: 0.0 for k in truck_ops} | {"total": 0.0})
    for t in rows.trucks:
        d = truck_by_day[t.report_date]
        for op in truck_ops:
            d[op] += getattr(t, op)
        d["total"] += t.total

    # ---- KPI ----------------------------------------------------------------
    machine_h = sum(v.machine_hours for v in rows.vessels)
    downtime_h = sum(v.downtime_hours for v in rows.vessels)
    plan_shift = sum(v.plan_shift_t for v in rows.vessels)
    shifts_n = len(rows.shifts) or 1
    # средние на смену: «сколько человек выходит» и «сколько их в штате»
    crew_present = sum(s.crew_present for s in rows.shifts) / shifts_n
    crew_staff = sum(s.staff_total for s in rows.shifts) / shifts_n
    # Исправность считаем по середине смены: (исправно в начале + в конце) / 2 —
    # иначе одна и та же техника, уехавшая в ремонт к концу, «исчезает» из средней.
    rs_ok = sum((s.rs_ok_start + s.rs_ok_end) / 2 for s in rows.shifts)
    rs_all = sum(max(s.rs_total, s.rs_ok_start + s.rs_repair_start,
                     s.rs_ok_end + s.rs_repair_end) for s in rows.shifts)

    prev_total = sum(v.fact_shift_t for v in prev.vessels) if prev else 0.0

    kpi = {
        "throughput_t": _r(total_tons),
        "plan_shift_t": _r(plan_shift),
        "plan_pct": _pct(total_tons, plan_shift),
        "delta_pct": _pct(total_tons - prev_total, prev_total) if prev_total else None,
        "prev_throughput_t": _r(prev_total),
        "calls": len(rows.vessels),
        "vessels_unique": len({(v.vessel or "").strip().upper() for v in rows.vessels if v.vessel}),
        "avg_tph": _r(total_tons / machine_h, 1) if machine_h else 0.0,
        "machine_h": _r(machine_h),
        "downtime_h": _r(downtime_h),
        "downtime_pct": _pct(downtime_h, machine_h + downtime_h),
        "rail_cars": _r(rail_totals["processed"], 0),
        "trucks": _r(truck_totals["total"], 0),
        "crew_present": _r(crew_present),
        "crew_staff": _r(crew_staff),
        "crew_present_total": _r(sum(s.present_total for s in rows.shifts), 0),
        "crew_rate": _pct(crew_present, crew_staff),
        "rs_availability": _pct(rs_ok, rs_all),
        "days": len(days),
        "shifts": len(rows.shifts),
    }

    # ---- подходы ------------------------------------------------------------
    arrivals = sorted(
        ({
            "eta": a.eta, "vessel": a.vessel, "berth": a.berth, "work_kind": a.work_kind,
            "cargo_group": names.label(a.cargo_group), "plan_t": _r(a.plan_t, 0),
            "plan_raw": a.plan_raw, "agent": a.agent, "report_date": a.report_date,
        } for a in rows.arrivals),
        key=lambda a: (a["eta"] is None, a["eta"] or datetime(2100, 1, 1), a["vessel"]),
    )
    # Один и тот же плановый заход диспетчер повторяет в нескольких сменах —
    # на графике показываем уникальную пару (судно, время).
    seen: set = set()
    arrivals_unique = []
    for a in arrivals:
        key = (a["vessel"].upper(), a["eta"])
        if key in seen:
            continue
        seen.add(key)
        arrivals_unique.append(a)

    rail_arrivals: dict[str, dict] = defaultdict(lambda: {"cars": 0.0, "trains": 0,
                                                          "cargos": set(), "work_kinds": set()})
    for r in rows.rail_arrivals:
        key = (r.station or "—").strip()
        item = rail_arrivals[key]
        item["cars"] += r.cars
        item["trains"] += 1
        item["cargos"].add(names.label(r.cargo_group))
        item["work_kinds"].add(r.work_kind)
    rail_arrivals_out = sorted(
        ({"station": k, "cars": _r(v["cars"], 0), "trains": v["trains"],
          "cargos": sorted(v["cargos"]), "work_kinds": sorted(x for x in v["work_kinds"] if x)}
         for k, v in rail_arrivals.items()),
        key=lambda x: x["cars"], reverse=True,
    )

    total_cargo_tons = sum(c["tons"] for c in cargo_mix.values())

    return {
        "period": {"from": date_from, "to": date_to, "days": len(days)},
        "shift_filter": shift or "all",
        "meta": {
            "date_min": period_bounds(db)[0],
            "date_max": period_bounds(db)[1],
            "updated_at": max((s.updated_at for s in rows.shifts if s.updated_at), default=None),
            "sources": sorted({s.source for s in rows.shifts}),
        },
        "kpi": kpi,
        "dynamics": dynamics,
        "cargo_mix": sorted(
            ({"cargo_group": k, "tons": _r(v["tons"]), "calls": v["calls"],
              "plan_t": _r(v["plan_t"]), "machine_h": _r(v["machine_h"]),
              "downtime_h": _r(v["downtime_h"]),
              "tph": _r(v["tons"] / v["machine_h"], 1) if v["machine_h"] else 0.0,
              "share": _pct(v["tons"], total_cargo_tons)}
             for k, v in cargo_mix.items()),
            key=lambda x: x["tons"], reverse=True,
        ),
        "direction": {"import_t": _r(direction["import_t"]), "export_t": _r(direction["export_t"])},
        "shift_split": [
            {"shift": key, "label": SHIFT_LABELS.get(key, key), "throughput_t": _r(v["tons"]),
             "plan_pct": _pct(v["tons"], v["plan_t"]), "calls": v["calls"],
             "avg_tph": _r(v["tons"] / v["machine_h"], 1) if v["machine_h"] else 0.0,
             "downtime_pct": _pct(v["downtime_h"], v["machine_h"] + v["downtime_h"])}
            for key, v in sorted(by_shift.items(), key=lambda kv: kv[0])
        ],
        "berths": sorted(
            ({"berth": k, "calls": v["calls"], "throughput_t": _r(v["tons"]),
              "vessels": len(v["vessels"]), "machine_h": _r(v["machine_h"]),
              "downtime_h": _r(v["downtime_h"]),
              "downtime_pct": _pct(v["downtime_h"], v["machine_h"] + v["downtime_h"])}
             for k, v in berths.items()),
            key=lambda x: x["throughput_t"], reverse=True,
        ),
        "vessels_top": sorted(
            ({"vessel": k, "calls": v["calls"], "tons": _r(v["tons"]),
              "machine_h": _r(v["machine_h"]), "downtime_h": _r(v["downtime_h"]),
              "tph": _r(v["tons"] / v["machine_h"], 1) if v["machine_h"] else 0.0,
              "cargos": sorted(v["cargos"])}
             for k, v in vessels_top.items() if k != "—"),
            key=lambda x: x["tons"], reverse=True,
        ),
        "weekday": [
            {"weekday": wd, "label": WEEKDAYS[wd], "avg_throughput_t": _r(v["tons"] / len(v["days"]))
             if v["days"] else 0.0, "days": len(v["days"])}
            for wd, v in sorted(by_weekday.items())
        ],
        "rail": {
            "totals": {k: _r(v, 0) for k, v in rail_totals.items()},
            "by_cargo": sorted(
                ({"cargo_group": k, "processed": _r(v["processed"], 0),
                  "start_loaded": _r(v["start_loaded"], 0), "start_empty": _r(v["start_empty"], 0),
                  "end_loaded": _r(v["end_loaded"], 0), "end_empty": _r(v["end_empty"], 0)}
                 for k, v in rail_by_cargo.items()),
                key=lambda x: x["processed"], reverse=True,
            ),
        },
        "trucks": {
            "totals": {k: _r(v, 0) for k, v in truck_totals.items()},
            "by_cargo": sorted(
                ({"cargo_group": k, **{op: _r(v[op], 0) for op in truck_ops},
                  "total": _r(v["total"], 0)}
                 for k, v in truck_by_cargo.items()),
                key=lambda x: x["total"], reverse=True,
            ),
            "by_day": sorted(
                ({"bucket_date": d, **{op: _r(v[op], 0) for op in truck_ops},
                  "total": _r(v["total"], 0)}
                 for d, v in truck_by_day.items()),
                key=lambda x: x["bucket_date"],
            ),
        },
        "arrivals": arrivals_unique,
        "rail_arrivals": rail_arrivals_out,
        "rail_moves": {
            "totals": {
                "out_cars": _r(move_totals["out_cars"], 0),
                "in_cars": _r(move_totals["in_cars"], 0),
                "in_loaded": _r(move_totals["in_loaded"], 0),
                "in_empty": _r(move_totals["in_empty"], 0),
                "trains_out": _r(move_totals["trains_out"], 0),
                "avg_delay_min": _avg_delay(move_totals["delays"]),
                "late_trains": move_totals["late"],
            },
            "by_day": sorted(
                ({"bucket_date": d, "out_cars": _r(v["out_cars"], 0),
                  "in_cars": _r(v["in_cars"], 0), "in_loaded": _r(v["in_loaded"], 0),
                  "in_empty": _r(v["in_empty"], 0), "late": v["late"],
                  "avg_delay_min": _avg_delay(v["delays"])}
                 for d, v in move_day.items()),
                key=lambda x: x["bucket_date"],
            ),
            "by_wagon": [
                {"wagon_type": "полувагон", "out": _r(move_totals["out_semi"], 0),
                 "in": _r(move_totals["in_semi"], 0)},
                {"wagon_type": "крытый", "out": _r(move_totals["out_covered"], 0),
                 "in": _r(move_totals["in_covered"], 0)},
                {"wagon_type": "платформа", "out": _r(move_totals["out_flat"], 0),
                 "in": _r(move_totals["in_flat"], 0)},
            ],
        },
        "platforms": [
            {"owner": owner,
             "total_cars": _r(v["last"].total_cars, 0), "remaining": _r(v["last"].remaining, 0),
             "terminal": _r(v["last"].terminal, 0), "sent": _r(v["last"].sent, 0),
             "arrived_loaded": _r(v["last"].arrived_loaded, 0),
             "remaining_delta": _r(v["last"].remaining - v["first"].remaining, 0)}
            for owner, v in sorted(plat_by_owner.items())
        ],
    }


def day_detail(db: Session, day: date, shift: str | None = None) -> dict:
    """Один день для панели детализации: смены, суда, вагоны, машины, подходы."""
    names = _CargoNames()
    rows = load_rows(db, day, day, shift)
    return {
        "date": day,
        "shifts": [{
            "shift": s.shift, "label": SHIFT_LABELS.get(s.shift, s.shift),
            "dispatcher": s.dispatcher, "crew_no": s.crew_no,
            "present": _r(s.present_total, 0), "crew": _r(s.crew_present, 0),
            "contractors": _contractors(s.contractors_json), "staff": _r(s.staff_total, 0),
            "sick": _r(s.sick, 0), "vacation": _r(s.vacation, 0),
            "rs_ok_start": _r(s.rs_ok_start, 0), "rs_repair_start": _r(s.rs_repair_start, 0),
            "rs_ok_end": _r(s.rs_ok_end, 0), "rs_repair_end": _r(s.rs_repair_end, 0),
        } for s in rows.shifts],
        "vessels": [{
            "shift": v.shift, "berth": v.berth, "vessel": v.vessel,
            "cargo_group": names.label(v.cargo_group),
            "plan_shift_t": _r(v.plan_shift_t, 0), "fact_shift_t": _r(v.fact_shift_t, 0),
            "plan_pct": _pct(v.fact_shift_t, v.plan_shift_t),
            "plan_total_t": _r(v.plan_total_t, 0), "remaining_t": _r(v.remaining_t, 0),
            "tph": _r(v.productivity_tph, 1), "moves": v.moves,
            "machine_h": _r(v.machine_hours), "downtime_h": _r(v.downtime_hours),
            "note": v.note,
        } for v in rows.vessels],
        "rails": [{
            "shift": r.shift, "cargo_group": names.label(r.cargo_group),
            "processed": _r(r.processed, 0),
            "port_start": _r(r.port_start_loaded + r.port_start_empty, 0),
            "port_end": _r(r.port_end_loaded + r.port_end_empty, 0),
        } for r in rows.rails if r.processed or r.port_start_loaded or r.port_start_empty],
        "trucks": [{
            "shift": t.shift, "cargo_group": names.label(t.cargo_group),
            "total": _r(t.total, 0), "imp_unload": _r(t.imp_unload, 0),
            "exp_unload": _r(t.exp_unload, 0), "imp_load": _r(t.imp_load, 0),
            "exp_load": _r(t.exp_load, 0), "direct_vessel": _r(t.direct_vessel, 0),
        } for t in rows.trucks if t.total],
        "arrivals": [{
            "eta": a.eta, "vessel": a.vessel, "berth": a.berth, "work_kind": a.work_kind,
            "cargo_group": names.label(a.cargo_group), "plan_raw": a.plan_raw, "agent": a.agent,
        } for a in rows.arrivals],
        "rail_out": [{
            "shift": r.shift, "move_date": r.move_date, "plan_time": r.plan_time,
            "semi_wagons": _r(r.semi_wagons, 0), "covered": _r(r.covered, 0),
            "flatcars": _r(r.flatcars, 0), "total": _r(r.total, 0),
            "accepted_by": r.accepted_by, "fact_cars": _r(r.fact_cars, 0),
            "fact_date": r.fact_date, "fact_time": r.fact_time,
            "delay_min": _delay_min(r.plan_time, r.fact_time, r.move_date, r.fact_date),
        } for r in rows.rail_out],
        "rail_in": [{
            "shift": r.shift, "move_date": r.move_date, "plan_time": r.plan_time,
            "settle_time": r.settle_time, "load_semi": _r(r.load_semi, 0),
            "load_covered": _r(r.load_covered, 0), "load_flat": _r(r.load_flat, 0),
            "empty_semi": _r(r.empty_semi, 0), "empty_covered": _r(r.empty_covered, 0),
            "empty_flat": _r(r.empty_flat, 0), "total": _r(r.total, 0),
        } for r in rows.rail_in],
        "platforms": [{
            "shift": p.shift, "owner": p.owner, "total_cars": _r(p.total_cars, 0),
            "remaining": _r(p.remaining, 0), "terminal": _r(p.terminal, 0),
            "sent": _r(p.sent, 0), "arrived_loaded": _r(p.arrived_loaded, 0),
        } for p in rows.platforms],
    }


# ------------------------------------------------- таблица-детализация ----
# Любой график страницы кликабелен: клик отдаёт те строки витрины, из которых
# график собран. Колонки описаны на бэкенде — фронт рисует таблицу и CSV как есть.

ROW_KINDS = ("vessel", "berth", "cargo", "rail_cargo", "truck_cargo",
             "station", "rail_move", "platform")

VESSEL_COLS = [
    {"key": "report_date", "label": "дата"}, {"key": "shift", "label": "смена"},
    {"key": "berth", "label": "причал"}, {"key": "cargo_group", "label": "груз"},
    {"key": "plan_shift_t", "label": "план, т", "right": True},
    {"key": "fact_shift_t", "label": "факт, т", "right": True},
    # проценты и темп не суммируются: среднее по строкам — не итог
    {"key": "plan_pct", "label": "план, %", "right": True, "sum": False},
    {"key": "tph", "label": "т/ч", "right": True, "sum": False},
    {"key": "machine_h", "label": "маш. ч", "right": True},
    {"key": "downtime_h", "label": "потери, ч", "right": True},
    {"key": "note", "label": "примечание"},
]
RAIL_COLS = [
    {"key": "report_date", "label": "дата"}, {"key": "shift", "label": "смена"},
    {"key": "cargo_group", "label": "груз"},
    {"key": "processed", "label": "обработано", "right": True},
    {"key": "start_loaded", "label": "начало груж.", "right": True},
    {"key": "start_empty", "label": "начало пор.", "right": True},
    {"key": "end_loaded", "label": "конец груж.", "right": True},
    {"key": "end_empty", "label": "конец пор.", "right": True},
]
TRUCK_COLS = [
    {"key": "report_date", "label": "дата"}, {"key": "shift", "label": "смена"},
    {"key": "cargo_group", "label": "груз"},
    {"key": "imp_unload", "label": "импорт выгр.", "right": True},
    {"key": "exp_unload", "label": "экспорт выгр.", "right": True},
    {"key": "imp_load", "label": "импорт погр.", "right": True},
    {"key": "exp_load", "label": "экспорт погр.", "right": True},
    {"key": "direct_vessel", "label": "с т/х в а/м", "right": True},
    {"key": "total", "label": "всего", "right": True},
]
MOVE_COLS = [
    {"key": "report_date", "label": "дата"}, {"key": "shift", "label": "смена"},
    {"key": "direction", "label": "операция"},
    {"key": "semi_wagons", "label": "полувагоны", "right": True},
    {"key": "covered", "label": "крытые", "right": True},
    {"key": "flatcars", "label": "платформы", "right": True},
    {"key": "total", "label": "итого", "right": True},
    {"key": "plan_time", "label": "план"}, {"key": "fact_time", "label": "факт"},
    {"key": "delay_min", "label": "задержка, мин", "right": True},
    {"key": "accepted_by", "label": "кто принял"},
]
PLATFORM_COLS = [
    {"key": "report_date", "label": "дата"}, {"key": "shift", "label": "смена"},
    {"key": "owner", "label": "владелец"},
    {"key": "total_cars", "label": "всего", "right": True},
    {"key": "remaining", "label": "остаток", "right": True},
    {"key": "terminal", "label": "на терминале", "right": True},
    {"key": "sent", "label": "отправлено", "right": True},
    {"key": "arrived_loaded", "label": "прибыло груж.", "right": True},
]
ARRIVAL_COLS = [
    {"key": "report_date", "label": "дата"}, {"key": "shift", "label": "смена"},
    {"key": "station", "label": "станция"},
    {"key": "cargo_group", "label": "груз"},
    {"key": "cars", "label": "вагонов", "right": True},
    {"key": "wagon_type", "label": "тип вагона"},
    {"key": "train_index", "label": "поезд"}, {"key": "note", "label": "примечание"},
]


def detail_rows(db: Session, date_from: date, date_to: date, shift: str | None,
                kind: str, key: str | None = None, *,
                limit: int | None = None, offset: int = 0) -> dict:
    """Строки витрины за период по измерению графика (для клика по бару).

    `limit`/`offset` — страница экрана: до неё урезается `rows`, а `total` и
    `totals` считаются по всей выборке, чтобы «итого» под таблицей оставалось
    итогом графика, а не тем, что влезло в окно. `limit=None` — вся выборка;
    так отдаёт файл `GET /api/dispatcher/rows.csv`, который обязан быть полнее
    того, что пользователь видит на экране.
    """
    if kind not in ROW_KINDS:
        raise ValueError(f"неизвестный разрез: {kind}")
    rows = load_rows(db, date_from, date_to, shift)
    names = _CargoNames()
    needle = (key or "").strip().lower()

    def want(value: str | None) -> bool:
        """без ключа берём все строки разреза, с ключом — только совпадение"""
        return not needle or (value or "").strip().lower() == needle

    def vessel_row(v) -> dict:
        return {
            "report_date": v.report_date, "shift": v.shift, "berth": v.berth,
            "cargo_group": names.label(v.cargo_group),
            "plan_shift_t": _r(v.plan_shift_t, 0), "fact_shift_t": _r(v.fact_shift_t, 0),
            "plan_pct": _pct(v.fact_shift_t, v.plan_shift_t), "tph": _r(v.productivity_tph, 1),
            "machine_h": _r(v.machine_hours), "downtime_h": _r(v.downtime_hours),
            "note": v.note,
        }

    out_rows: list[dict] = []
    cols = VESSEL_COLS
    title = kind
    if kind == "vessel":
        out_rows = [vessel_row(v) for v in rows.vessels if want(v.vessel)]
        title = f"Судно · {key}" if needle else "Все суда периода"
    elif kind == "berth":
        out_rows = [vessel_row(v) for v in rows.vessels if want(v.berth)]
        title = f"Причал · {key}" if needle else "Все причалы периода"
    elif kind == "cargo":
        out_rows = [vessel_row(v) for v in rows.vessels if want(names.label(v.cargo_group))]
        title = f"Груз · {key}" if needle else "Все грузы периода"
    elif kind == "rail_cargo":
        cols = RAIL_COLS
        out_rows = [{
            "report_date": r.report_date, "shift": r.shift,
            "cargo_group": names.label(r.cargo_group), "processed": _r(r.processed, 0),
            "start_loaded": _r(r.port_start_loaded, 0), "start_empty": _r(r.port_start_empty, 0),
            "end_loaded": _r(r.port_end_loaded, 0), "end_empty": _r(r.port_end_empty, 0),
        } for r in rows.rails if want(names.label(r.cargo_group))]
        title = f"Вагоны · {key}" if needle else "Вагоны по грузам за период"
    elif kind == "truck_cargo":
        cols = TRUCK_COLS
        out_rows = [{
            "report_date": t.report_date, "shift": t.shift,
            "cargo_group": names.label(t.cargo_group),
            **{op: _r(getattr(t, op), 0) for op in
               ("imp_unload", "exp_unload", "imp_load", "exp_load", "direct_vessel")},
            "total": _r(t.total, 0),
        } for t in rows.trucks if want(names.label(t.cargo_group))]
        title = f"Автомашины · {key}" if needle else "Автомашины по грузам за период"
    elif kind == "station":
        cols = ARRIVAL_COLS
        out_rows = [{
            "report_date": a.report_date, "shift": a.shift, "station": a.station,
            "cargo_group": names.label(a.cargo_group), "cars": _r(a.cars, 0),
            "wagon_type": a.wagon_type, "work_kind": a.work_kind,
            "train_index": a.train_index, "note": a.note,
        } for a in rows.rail_arrivals if want(a.station)]
        title = f"Станция · {key}" if needle else "Ж/Д на подходе по станциям"
    elif kind == "rail_move":
        cols = MOVE_COLS
        out_rows = [{
            "report_date": r.report_date, "shift": r.shift, "direction": "вывод",
            "semi_wagons": _r(r.semi_wagons, 0), "covered": _r(r.covered, 0),
            "flatcars": _r(r.flatcars, 0), "total": _r(r.total, 0),
            "plan_time": r.plan_time, "fact_time": r.fact_time,
            "delay_min": _delay_min(r.plan_time, r.fact_time, r.move_date, r.fact_date),
            "accepted_by": r.accepted_by,
        } for r in rows.rail_out] + [{
            "report_date": r.report_date, "shift": r.shift, "direction": "ввод",
            "semi_wagons": _r(r.load_semi + r.empty_semi, 0),
            "covered": _r(r.load_covered + r.empty_covered, 0),
            "flatcars": _r(r.load_flat + r.empty_flat, 0), "total": _r(r.total, 0),
            "plan_time": r.plan_time, "fact_time": r.settle_time, "delay_min": None,
            "accepted_by": "",
        } for r in rows.rail_in]
        out_rows.sort(key=lambda x: (x["report_date"], x["shift"], x["direction"]))
        title = "Выводка и постановка вагонов"
    elif kind == "platform":
        cols = PLATFORM_COLS
        out_rows = [{
            "report_date": p.report_date, "shift": p.shift, "owner": p.owner,
            "total_cars": _r(p.total_cars, 0), "remaining": _r(p.remaining, 0),
            "terminal": _r(p.terminal, 0), "sent": _r(p.sent, 0),
            "arrived_loaded": _r(p.arrived_loaded, 0),
        } for p in rows.platforms if not needle or p.owner.lower() == needle]
        title = f"Платформы аренды{'' if not needle else ' · ' + key}"

    # итог по числовым колонкам — таблица без него нечитаемая; считаем по всей
    # выборке, до лимита: панель — детализация графика, её итог обязан равняться
    # числу на баре, а не сумме видимых строк
    totals: dict[str, float | None] = {}
    for c in cols:
        if not c.get("right") or c.get("sum") is False:
            continue
        vals = [r[c["key"]] for r in out_rows if isinstance(r.get(c["key"]), (int, float))]
        totals[c["key"]] = _r(sum(vals), 0) if vals else None

    # Лимит — в самом конце, на готовом списке, а не в SQL. Строки разреза
    # приходят из того же `load_rows`, что и графики (иначе цифры панели и
    # баров разъехались бы), а фильтр ключа живёт в Python: груз подписывается
    # через `names.label`, вагоны «ВЫВОД» и «ВВОД» склеиваются из двух таблиц и
    # сортируются уже после склейки. `COUNT(*)`, положенный в SQL, считался бы
    # по другому условию, чем экран, и `total` врал бы о числе строк таблицы.
    # Порядок строк при этом не меняется — срез берёт готовый список.
    total = len(out_rows)
    start = max(0, offset)
    page = out_rows[start:] if limit is None else out_rows[start:start + limit]
    return {
        "kind": kind, "key": key, "title": title, "columns": cols,
        "rows": page, "total": total,
        # впереди ещё строки: по этому флагу фронт пишет «показаны первые N из M»
        "truncated": start + len(page) < total,
        "totals": totals,
        "period": {"from": date_from, "to": date_to, "shift": shift},
    }


# ------------------------------------------------------------- автоимпорт ----

def file_mtime_utc(path: Path) -> datetime:
    """mtime файла в наивном UTC — в тех же единицах, что и models.utcnow()."""
    stamp = path.stat().st_mtime
    return datetime.fromtimestamp(stamp, tz=timezone.utc).replace(tzinfo=None)


def auto_import(db: Session) -> str:
    """Загрузить файл отчёта диспетчера при старте сервиса.

    Трогаем файл только если он новее последней записи витрины: иначе каждый
    рестарт сервиса перечитывал бы месяц порта впустую. Ошибку не поднимаем —
    автоимпорт не должен ронять запуск кабинета, всё видно в логе и в админке.
    """
    from ..onec.dispatcher import apply_snapshot
    from ..onec.dispatcher_xlsm import describe, parse_workbook

    if not app_settings.get(db, "dispatcher.auto_import"):
        return "автоимпорт выключен настройкой"
    raw = str(app_settings.get(db, "dispatcher.source_path") or "")
    if not raw:
        return "файл отчёта диспетчера не указан (dispatcher.source_path)"
    path = Path(raw)
    if not path.exists():
        return f"файл не найден: {path}"

    last = db.scalar(select(func.max(FactDispatcherShift.updated_at)))
    mtime = file_mtime_utc(path)
    if last and mtime <= last:
        return f"файл не менялся с последней загрузки ({path.name})"

    snapshot = parse_workbook(path)
    if not snapshot.shifts:
        return f"в файле {path.name} не найдено листов отчёта"
    apply_snapshot(db, snapshot)
    # Причина та же, что в `api/dispatcher.import_file`: витрина изменилась,
    # собранные ответы больше не действительны.
    data_cache.invalidate()
    return f"импортировано из {path.name}: {describe(snapshot)}"
