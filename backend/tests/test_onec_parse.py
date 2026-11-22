"""Разбор строк 1С: одна битая строка не роняет срез, мусорный ответ — роняет.

Тестируем поведение парсера как чистую функцию: что вернулось и что попало
в warnings, без БД и HTTP.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.onec.base import Snapshot
from app.onec.parse import (
    _rows,
    parse_capacity,
    parse_cargo,
    parse_dynamics,
    parse_warehouses,
    snapshot_from_payload,
)


# ------------------------------------------------------------------ терпимость --
def test_parse_warehouses_tolerant_mix():
    items = [
        {"code": "WH-1", "name": "Северный"},
        {"name": "без кода"},  # битая: нет кода
        {"warehouse_code": "WH-2"},
        {"code": "   "},       # битая: код-пробелы
        {"code": "WH-3"},
    ]
    warnings: list[str] = []
    out = parse_warehouses(items, warnings)
    assert [r.code for r in out] == ["WH-1", "WH-2", "WH-3"]
    assert len(warnings) == 2
    assert any("warehouses" in w and "строка 2" in w and "code" in w for w in warnings)
    assert any("warehouses" in w and "строка 4" in w for w in warnings)


def test_parse_dynamics_bad_date_and_missing_fields():
    items = [
        {"warehouse_code": "WH-1", "date": "2026-10-12"},
        {"warehouse_code": "WH-1", "date": "12.10.2026"},  # не YYYY-MM-DD
        {"warehouse_code": "WH-2", "date": "2026-10-13"},
        {"date": "2026-10-13"},                            # нет warehouse_code
        {"warehouse_code": "WH-3", "date": "2026-10-14"},
        {"warehouse_code": "WH-4"},                        # нет date
        {"warehouse_code": "WH-5", "date": "2026-10-15"},
    ]
    warnings: list[str] = []
    out = parse_dynamics(items, warnings)
    assert [r.warehouse_code for r in out] == ["WH-1", "WH-2", "WH-3", "WH-5"]
    assert out[0].bucket_date.isoformat() == "2026-10-12"
    assert len(warnings) == 3
    assert any("динамика" in w and "строка 2" in w and "YYYY-MM-DD" in w for w in warnings)
    assert any("динамика" in w and "строка 4" in w and "warehouse_code" in w for w in warnings)
    assert any("динамика" in w and "строка 6" in w and "date" in w for w in warnings)


def test_warnings_optional_and_dropped():
    # Без списка warnings парсер работает так же: предупреждения просто некому отдавать.
    out = parse_warehouses([{"code": "WH-1"}, {"name": "битая"}])
    assert [r.code for r in out] == ["WH-1"]


# ----------------------------------------------------------------------- порог --
def test_parse_cargo_raises_when_majority_broken():
    items = [{"warehouse_code": "WH-1"}] * 4 + [{"cargo_group": "мусор"}] * 6
    with pytest.raises(ValueError) as exc:
        parse_cargo(items)
    assert "пропущено 6 из 10" in str(exc.value)


def test_parse_cargo_exactly_half_broken_is_ok():
    items = [{"warehouse_code": "WH-1"}, {"cargo_group": "без кода"}] * 2
    warnings: list[str] = []
    out = parse_cargo(items, warnings)
    assert len(out) == 2
    assert len(warnings) == 2


# ---------------------------------------------------------------- пустой раздел --
def test_empty_sections_no_error_no_warnings():
    warnings: list[str] = []
    assert parse_warehouses([], warnings) == []
    assert parse_cargo(None, warnings) == []
    assert parse_capacity({"items": []}, warnings) == []
    assert parse_dynamics({}, warnings) == []
    assert warnings == []


# ------------------------------------------------------------------ лимиты --
def test_warnings_capped_per_section():
    items = [{"warehouse_code": "WH-1"}] * 60 + [{"cargo_group": "нет кода"}] * 52
    warnings: list[str] = []
    out = parse_cargo(items, warnings)
    # 52 битых из 112 — ниже порога, но предупреждений не больше 50 + свёртка.
    assert len(out) == 60
    assert len(warnings) == 51
    assert warnings[-1].endswith("и ещё 2 таких же")


def test_warning_cap_is_per_section_in_snapshot():
    balance = [{"warehouse_code": f"WH-{i % 3}"} for i in range(60)] + [{}] * 52
    capacity = [{"warehouse_code": f"WH-{i % 3}"} for i in range(60)] + [{}] * 52
    snap = snapshot_from_payload({"balance": balance, "capacity": capacity})
    bal_w = [w for w in snap.warnings if "balance" in w]
    cap_w = [w for w in snap.warnings if "capacity" in w]
    # Лимит считается на раздел, а не на весь срез: иначе второй раздел молчал бы.
    assert len(bal_w) == 51
    assert len(cap_w) == 51


def test_warning_length_capped():
    bad_date = "x" * 400
    items = [{"warehouse_code": "WH-1", "date": "2026-10-12"}] * 3 + [
        {"warehouse_code": "WH-1", "date": bad_date},
    ]
    warnings: list[str] = []
    parse_dynamics(items, warnings)
    assert len(warnings) == 1
    assert len(warnings[0]) <= 160


# ------------------------------------------------------------- snapshot-сборка --
def test_snapshot_from_payload_tolerant():
    balance = [{"warehouse_code": f"WH-{i}", "weight_t": i} for i in range(1, 9)]
    balance.insert(3, {"weight_t": 1})          # битая: нет warehouse_code
    balance.insert(6, {"cargo_group": "мусор"})  # битая: нет warehouse_code
    payload = {"snapshot_at": "2026-11-20T08:00:00", "balance": balance}
    snap = snapshot_from_payload(payload)
    assert len(snap.cargo) == 8
    assert len(snap.warnings) == 2
    assert all("balance" in w for w in snap.warnings)
    assert snap.snapshot_at == datetime(2026, 11, 20, 8, 0)


def test_snapshot_from_payload_empty_raises():
    with pytest.raises(ValueError):
        snapshot_from_payload({})
    with pytest.raises(ValueError):
        snapshot_from_payload({"warehouses": [], "balance": [], "capacity": [], "dynamics": []})


def test_snapshot_warnings_field_positional():
    # Новое поле — последним и с дефолтом: старые позиционные вызовы живые.
    snap = Snapshot(datetime(2026, 11, 20, 8, 0))
    assert snap.warnings == []
    snap2 = Snapshot(datetime(2026, 11, 20, 8, 0), [], [], [], [], ["w"])
    assert snap2.warnings == ["w"]


# ------------------------------------------------------- строгость формы ответа --
def test_rows_contract_still_strict():
    with pytest.raises(ValueError):
        parse_warehouses({"items": "не список"})
    with pytest.raises(ValueError):
        parse_cargo(["не объект"])
    with pytest.raises(ValueError):
        _rows(42)
