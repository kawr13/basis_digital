"""Демо-генератор «Отчёта диспетчера»: файл обязан проходить настоящий разбор.

Почему тест нужен именно такой. `app/onec/dispatcher_demo.py` пишет лист по
координатам разборщика: строки берёт из констант `dispatcher_xlsm`, колонки
держит своим списком (`COL_*`). Если разборщик переедет, а генератор — нет,
импорт не упадёт: `parse_sheet` просто не распознает блок и вернёт пустоту, а
«Статистика» молча покажет нули. Поэтому проверка — раундтрип: собрали файл →
разобрали его же → сверили, что КАЖДЫЙ блок вернулся непустым и цифры те же,
что мы написали.

Отдельно проверяем «человеческие» значения («-», «129+130», «87+0/3539 тн»):
план захода в витрине — сумма всех чисел строки, и расхождение с `_sum_numbers`
означает, что генератор придумал формат, которого разборщик не знает.

Данные — декабрь 2025, после теста витрину чистим: она на весь прогон одна.
"""
from __future__ import annotations

import base64
import re
from datetime import date, datetime, time as dtime, timedelta

import pytest
from sqlalchemy import delete

from app.db import SessionLocal
from app.models import (
    FactDispatcherShift, FactPlatformBalance, FactRailArrival, FactRailDelivery,
    FactRailDeparture, FactRailOperation, FactTruckOperation,
    FactVesselArrival, FactVesselOperation,
)
from app.onec import dispatcher_demo as demo
from app.onec.dispatcher_xlsm import _sum_numbers, describe, parse_workbook
from app.services import data_cache

END = date(2025, 12, 20)
DAYS = 8
DATE_FROM = END - timedelta(days=DAYS - 1)


@pytest.fixture(scope="module")
def report() -> bytes:
    return demo.build_report(days=DAYS, end=END, seed=42)


@pytest.fixture(scope="module")
def snapshot(report) -> object:
    return parse_workbook(report)


@pytest.fixture
def clean_vitrine():
    """Убрать за собой строки демо: витрина на весь прогон одна."""
    yield
    db = SessionLocal()
    try:
        for model in (FactDispatcherShift, FactVesselOperation, FactRailOperation,
                      FactTruckOperation, FactVesselArrival, FactRailArrival,
                      FactRailDeparture, FactRailDelivery, FactPlatformBalance):
            where = (model.report_date >= DATE_FROM) & (model.report_date <= END)
            db.execute(delete(model).where(where))
        db.commit()
    finally:
        db.close()
    data_cache.invalidate()


# ------------------------------------------------------------------ разбор ----

def test_one_sheet_per_shift(snapshot):
    """Один лист = одна смена: 8 суток → 15 смен (ночной смены 20.12 ещё нет)."""
    shifts = snapshot.shifts
    assert len(shifts) == DAYS * 2 - 1
    assert {s.shift for s in shifts} == {"08-20", "20-08"}
    assert min(s.report_date for s in shifts) == DATE_FROM
    assert max(s.report_date for s in shifts) == END
    assert len({s.sheet for s in shifts}) == len(shifts), "имя листа — ключ смены"
    # смена берётся из имени листа: «13-14.12.2025» — это ночь
    for s in shifts:
        assert (s.shift == "20-08") == ("-" in s.sheet)
        assert all(v.berth.startswith("РП - ") for v in s.vessels)


def test_every_block_survives_the_round_trip(snapshot):
    """Главный сторож координат: ни один блок не должен приезжаться пустым."""
    for s in snapshot.shifts:
        assert s.vessels, f"{s.sheet}: блок обработки судов пуст"
        assert s.rails, f"{s.sheet}: блок вагонов пуст"
        assert s.trucks, f"{s.sheet}: блок автомашин пуст"
        assert s.arrivals, f"{s.sheet}: «Суда на подходе» пусты"
        assert s.rail_arrivals, f"{s.sheet}: «Ж/Д на подходе» пусты"
        assert s.rail_out, f"{s.sheet}: блок «ВЫВОД» пуст"
        assert s.rail_in, f"{s.sheet}: блок «ВВОД» пуст"
        assert len(s.platforms) == 2, f"{s.sheet}: платформ из аренды должен быть 2 владельца"
        assert s.present_total and s.staff_total, f"{s.sheet}: персонал не прочитан"
        assert s.rs_total, f"{s.sheet}: ричстакеры не прочитаны"
        assert s.dispatcher, f"{s.sheet}: диспетчер (B2) пуст"


def test_vessel_rows_are_self_consistent(snapshot):
    """Цифры строки не противоречат друг другу — как в живом листе.

    Машинное время = сменное − потери, темп = факт / машинное, «на борту» +
    «остаток» = план судна (с точностью до округления тоннажа на листе).
    """
    for s in snapshot.shifts:
        for v in s.vessels:
            assert v.machine_hours == pytest.approx(v.shift_hours - v.downtime_hours, abs=0.05)
            assert v.remaining_t >= 0 and v.on_board_t >= 0
            assert v.on_board_t + v.remaining_t == pytest.approx(v.plan_total_t, abs=10)
            if v.machine_hours:
                assert v.productivity_tph == pytest.approx(
                    v.fact_shift_t / v.machine_hours, abs=0.2)
            assert v.vessel and v.cargo_group
            assert v.berthed_at and v.started_at and v.plan_finish_at


def test_arrival_plan_matches_sum_numbers(snapshot):
    """«129+130» и «87+0/3539 тн» должны считаться ровно так же, как в разборе."""
    checked = 0
    for s in snapshot.shifts:
        for a in s.arrivals:
            if not a.plan_raw:                      # диспетчер поставил «-»
                assert a.plan_t == 0.0
                continue
            assert _sum_numbers(a.plan_raw) == pytest.approx(a.plan_t, abs=0.01), a.plan_raw
            checked += 1
    assert checked > 10


def test_plan_cells_are_written_by_hand():
    """Формы плана: «-», «129+130», «87+0/3539 тн», обычное число и дробь.

    Отдельный тест, потому что за 8 суток демо не обязательно успевает выдать
    каждую форму, а разборщик обязан знать их все — это его `_sum_numbers`.
    """
    from random import Random

    rng = Random(1)
    shapes = {"dash": 0, "sum": 0, "tons": 0, "plain": 0, "fraction": 0}
    for _ in range(400):
        raw, total = demo._plan_cell(rng, 12345.0)
        assert _sum_numbers(raw) == pytest.approx(total, abs=0.01), raw
        if raw == "-":
            shapes["dash"] += 1
        elif "/" in raw:
            shapes["tons"] += 1
        elif "+" in raw:
            shapes["sum"] += 1
        elif "," in raw:
            shapes["fraction"] += 1
        else:
            shapes["plain"] += 1
    assert all(shapes.values()), shapes
    # разряды пробелом не делим: `_sum_numbers` прочитает «1 245» как 1 + 245
    assert not any(re.search(r"\d \d", demo._plan_cell(rng, tons)[0])
                   for tons in (1245, 12450, 124500) for _ in range(50))


def test_cargo_spellings_stay_in_one_bucket(snapshot):
    """Написки одного груза не должны разъезжаться по двум строкам витрины.

    `_CargoNames.label` умеет переименовывать метку на лету («кричащее» имя на
    обычное), а `cargo_mix` ключуется уже полученной меткой: из-за этого «УГОЛЬ»
    и «уголь» дали бы два ломтя диаграммы. Демо пишет те варианты, которые
    склеиваются, — тест не даст вернуть кричащие написки обратно.
    """
    from app.services.dispatcher import _CargoNames

    labels: dict[str, set[str]] = {}
    names = _CargoNames()
    for s in snapshot.shifts:
        for v in s.vessels:
            labels.setdefault(names.label(v.cargo_group), set()).add(v.cargo_group)
    for cargo, rule in demo.CARGO_RULES.items():
        merged = {lab for lab, spellings in labels.items() if spellings & set(rule[3])}
        assert len(merged) <= 1, (cargo, sorted(merged))
    present = {c for c, r in demo.CARGO_RULES.items()
               if any(set(r[3]) & spellings for spellings in labels.values())}
    assert len(present) >= 5, "за 8 суток демо обязано успеть сменить номенклатуру"
    assert len(labels) == len(present), sorted(labels)


def test_arrivals_look_into_the_future(snapshot):
    """Карточка плановых заходов живёт только на заходах из будущего."""
    last = snapshot.shifts[-1]
    begin = datetime.combine(last.report_date, dtime(8, 0))
    future = [a for a in last.arrivals if a.eta and a.eta > begin]
    assert len(future) >= 3, "на последней смене должно быть несколько будущих заходов"
    assert max(a.eta for a in future) > begin + timedelta(days=2)
    # заход — это то же судно, что потом встанет к причалу, а не случайный список
    assert len({a.vessel for a in last.arrivals}) == len(last.arrivals)


def test_port_has_a_life_of_its_own(snapshot):
    """Судно стоит не одну смену: иначе «топ судов» = «по одному заходу»."""
    calls: dict[str, int] = {}
    for s in snapshot.shifts:
        for v in s.vessels:
            calls[v.vessel] = calls.get(v.vessel, 0) + 1
    assert max(calls.values()) >= 3, "судно должно стоять у причала несколько смен"
    berths = {v.berth for s in snapshot.shifts for v in s.vessels}
    assert len(berths) >= 3
    names = {v.vessel for s in snapshot.shifts for v in s.vessels}
    cyr = any(any("А" <= ch <= "Я" for ch in n) for n in names)
    lat = any(any("A" <= ch <= "Z" for ch in n) for n in names)
    assert cyr and lat, "в порту должны ходить и «СВЕТЛОГОРСК», и «AURORA»"


def test_report_is_reproducible():
    """Один и тот же seed = один и тот же файл побайтно (иначе скриншоты
    нельзя сравнить между прогонами)."""
    a = demo.build_report(days=3, end=END)
    b = demo.build_report(days=3, end=END)
    assert a == b
    assert a != demo.build_report(days=3, end=END, seed=7)


def test_describe_reports_a_full_port(snapshot):
    line = describe(snapshot)
    assert "смен 15" in line
    for part in ("судов", "вагонов-строк", "машин-строк", "подходов", "выводка", "постановка"):
        assert part in line


# ------------------------------------------------------------------- импорт ----

def test_demo_passes_real_import(client, admin_hdr, report, clean_vitrine):
    """Демо грузится настоящим `POST /api/admin/dispatcher/import` и оживает.

    Отдельно проверяем то, ради чего всё затевалось: страница «Статистика»
    видит грузопоток, подходы и движение вагонов, а не нули.
    """
    r = client.post("/api/admin/dispatcher/import",
                    json={"content_base64": base64.b64encode(report).decode(),
                          "filename": "demo_report.xlsm"}, headers=admin_hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"]["shift"] == DAYS * 2 - 1
    assert body["imported"]["vessel"] > 0
    assert body["date_min"] == DATE_FROM.isoformat()
    assert body["date_max"] == END.isoformat()

    ov = client.get("/api/dispatcher/overview",
                    params={"from": DATE_FROM.isoformat(), "to": END.isoformat()},
                    headers=admin_hdr).json()
    assert ov["kpi"]["throughput_t"] > 0
    assert ov["kpi"]["calls"] > 0
    assert ov["kpi"]["plan_pct"] is not None
    assert ov["arrivals"], "плановые заходы должны быть видны на странице"
    horizon = f"{END.isoformat()}T08:00:00"
    assert any(a["eta"] and a["eta"] > horizon for a in ov["arrivals"]), "заходы из будущего"
    assert ov["rail_arrivals"] and ov["rail_moves"]["totals"]["out_cars"] > 0
    assert ov["platforms"]

    day = client.get("/api/dispatcher/day", params={"date": END.isoformat()},
                     headers=admin_hdr).json()
    assert day["vessels"] and day["arrivals"]
    assert {s["shift"] for s in day["shifts"]} == {"08-20"}
