"""Разбор xlsm в read_only-режиме: значения, агрегаты и закрытие книги.

Числа зафиксированы ЗАМЕРом на коде до перехода на `read_only=True`
(обычный режим vs read_only давали побайтово одинаковый снапшот) — тест
ловит ровно тот риск, который несёт смена режима загрузки: потеря значений
(случайный доступ, формулы, даты) или висящий дескриптор архива.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from app.onec import dispatcher_xlsm as dxl
from app.onec.dispatcher_xlsm import describe, parse_sheet, parse_workbook

DEMO = Path(__file__).resolve().parents[1] / "data" / "demo_report.xlsm"

# --- замеры обычного режима на data/demo_report.xlsm (эталон) -----------------
DEMO_DESCRIBE = ("смен 89; судов 327; вагонов-строк 423; машин-строк 384; "
                "подходов 534; выводка 163; постановка 165")
DEMO_AGGREGATES = {
    "shifts": 89,
    "vessels": 327,
    "rails": 423,
    "trucks": 384,
    "arrivals": 534,
    "rail_arrivals": 412,
    "rail_out": 163,
    "rail_in": 165,
    "platforms": 178,
    "fact_shift_t": 681012.8,
    "plan_shift_t": 857980.0,
    "present_total": 3683.0,
    "machine_hours": 2967.7,
    "rail_processed": 11149.0,
    "truck_total": 5285.0,
    "out_cars": 6396.0,
    "in_cars": 6091.0,
    "plat_total": 10057.0,
}


def _aggregates(snapshot) -> dict:
    s = snapshot.shifts
    return {
        "shifts": len(s),
        "vessels": sum(len(x.vessels) for x in s),
        "rails": sum(len(x.rails) for x in s),
        "trucks": sum(len(x.trucks) for x in s),
        "arrivals": sum(len(x.arrivals) for x in s),
        "rail_arrivals": sum(len(x.rail_arrivals) for x in s),
        "rail_out": sum(len(x.rail_out) for x in s),
        "rail_in": sum(len(x.rail_in) for x in s),
        "platforms": sum(len(x.platforms) for x in s),
        "fact_shift_t": round(sum(v.fact_shift_t for x in s for v in x.vessels), 3),
        "plan_shift_t": round(sum(v.plan_shift_t for x in s for v in x.vessels), 3),
        "present_total": round(sum(x.present_total for x in s), 3),
        "machine_hours": round(sum(v.machine_hours for x in s for v in x.vessels), 3),
        "rail_processed": round(sum(r.processed for x in s for r in x.rails), 3),
        "truck_total": round(sum(t.total for x in s for t in x.trucks), 3),
        "out_cars": round(sum(r.fact_cars for x in s for r in x.rail_out), 3),
        "in_cars": round(sum(r.total for x in s for r in x.rail_in), 3),
        "plat_total": round(sum(pl.total_cars for x in s for pl in x.platforms), 3),
    }


# ------------------------------------------------------------------ значения ----

def test_demo_report_matches_pre_readonly_aggregates():
    """Тот же снапшот, что давал обычный режим: ни одного потерянного значения."""
    snapshot = parse_workbook(DEMO)
    assert snapshot.shifts, "на реальном файле снапшот не должен пустеть"
    assert describe(snapshot) == DEMO_DESCRIBE
    assert _aggregates(snapshot) == DEMO_AGGREGATES
    first = snapshot.shifts[0]
    assert (first.report_date, first.shift, first.sheet) == (
        date(2026, 9, 12), "08-20", "12.09.2026")
    assert first.dispatcher == "Петров П.П."
    assert first.contractors == [{"name": "Ремфлот", "count": 9.0},
                                 {"name": "Чистый порт", "count": 4.0}]


def test_bytes_and_path_give_the_same_snapshot():
    """Способ передачи источника (bytes vs путь) на результат не влияет."""
    by_path = _aggregates(parse_workbook(DEMO))
    by_bytes = _aggregates(parse_workbook(DEMO.read_bytes()))
    assert by_bytes == by_path == DEMO_AGGREGATES


def test_parse_sheet_accepts_regular_worksheet():
    """Внешние вызовы `parse_sheet(ws)` с обычным листом работают как раньше."""
    wb = openpyxl.load_workbook(DEMO, data_only=True, keep_vba=False)
    try:
        rec = parse_sheet(wb.worksheets[0])
    finally:
        wb.close()
    snap = parse_workbook(DEMO)
    same = next(x for x in snap.shifts if x.sheet == rec.sheet.strip())
    assert rec is not None and same is not None
    for field in ("report_date", "shift", "sheet", "dispatcher", "present_total"):
        assert getattr(rec, field) == getattr(same, field)
    assert (len(rec.vessels), len(rec.rails), len(rec.arrivals)) == \
        (len(same.vessels), len(same.rails), len(same.arrivals))


# ----------------------------------------------------------------- дескрипторы ----

def test_workbook_is_closed_after_parse(monkeypatch):
    """read_only держит zip-архив: книга обязана закрываться в finally."""
    real_load = openpyxl.load_workbook
    seen: list[dict] = []

    def spy(*args, **kwargs):
        wb = real_load(*args, **kwargs)
        assert kwargs.get("read_only") is True, "книга грузится не в read_only"
        assert kwargs.get("data_only") is True, "формулы должны браться кэшем"
        flag: list[bool] = []
        original_close = wb.close

        def close():
            flag.append(True)
            original_close()
        monkeypatch.setattr(wb, "close", close)
        seen.append({"closed": flag, "wb": wb})
        return wb

    monkeypatch.setattr(openpyxl, "load_workbook", spy)
    snapshot = parse_workbook(DEMO)
    assert snapshot.shifts
    assert len(seen) == 1 and seen[0]["closed"] == [True]
    # раз close() отработал, zip внутри книги уже закрыт (дескриптор отпущен)
    assert getattr(seen[0]["wb"]._archive, "fp", None) is None


def test_repeated_parse_of_same_file_does_not_leak():
    """Три разбора подряд тем же процессом: файл не «запирается» между ними."""
    for _ in range(3):
        assert _aggregates(parse_workbook(DEMO)) == DEMO_AGGREGATES
    # файл по-прежнему доступен на перезапись — признак отпущенного дескриптора
    data = DEMO.read_bytes()
    assert parse_workbook(io.BytesIO(data)).shifts


# --------------------------------------------------------------------- битые ----

def test_broken_bytes_raise_badzipfile():
    """Битой файл — та же ошибка, что и до read_only: BadZipFile, не тихая пустота."""
    with pytest.raises(zipfile.BadZipFile):
        parse_workbook(b"not an excel at all")
    with pytest.raises(zipfile.BadZipFile):
        parse_workbook(b"PK\x03\x04garbage")


def test_broken_file_with_xlsm_name_raises_badzipfile(tmp_path):
    broken = tmp_path / "broken.xlsm"
    broken.write_bytes(b"definitely not excel")
    with pytest.raises(zipfile.BadZipFile):
        parse_workbook(broken)


# --------------------------------------------------------------- пустые листы ----

def test_empty_and_headerless_sheets_stay_silent():
    """Пустой лист, лист без шапки и лист без даты — 0 смен без исключений."""
    wb = openpyxl.Workbook()
    wb.active.title = "01.11.2025"                 # имя похоже на смену, лист пуст
    ws = wb.create_sheet("02.11.2025")
    ws["A1"] = "02.11.2025"                        # дата есть, шапок блоков нет
    ws["A2"] = "08-20"
    ws2 = wb.create_sheet("ЛистБезДаты")
    ws2["B5"] = "просто текст"
    buf = io.BytesIO()
    wb.save(buf)

    snapshot = parse_workbook(buf.getvalue())
    assert snapshot.shifts == []
    assert describe(snapshot) == ("смен 0; судов 0; вагонов-строк 0; машин-строк 0; "
                                  "подходов 0; выводка 0; постановка 0")


def test_empty_workbook_parses_to_empty_snapshot():
    wb = openpyxl.Workbook()
    buf = io.BytesIO()
    wb.save(buf)
    assert parse_workbook(buf.getvalue()).shifts == []
