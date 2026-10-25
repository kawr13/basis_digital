"""Тесты витрины «Отчёт диспетчера»: импорт файла, агрегаты, права.

Файл отчёта собираем прямо в тесте (openpyxl в память): проверяем не «читается
ли вообще что-то», а конкретные цифры — сколько тонн, смен и людей получилось
на известной раскладке. Реальный `Отчет диспетчера.xlsm` в тесты не тянем:
тест не должен зависеть от файла на диске у разработчика.
"""
from __future__ import annotations

import base64
import io
from datetime import datetime

import openpyxl

PERIOD = {"from": "2025-11-01", "to": "2025-11-01"}


def _fill(ws, cells: dict) -> None:
    for ref, value in cells.items():
        ws[ref] = value


def _shift_cells(*, dispatcher: str, crew: float, staff: float, day_off: float,
                 contractors: float, present: float, sick: float,
                 rs_ok_start: float, rs_repair_start: float,
                 rs_ok_end: float, rs_repair_end: float) -> dict:
    """Шапка листа: персонал + ричстакеры (строки 3..13, колонки S/U/W)."""
    return {
        "A3": "ВЫХОД ПЕРСОНАЛА", "C3": "№ бригады", "G3": "КОЛИЧЕСТВО", "P3": "Штат",
        "A4": "ОСНОВНАЯ БРИГАДА", "C4": 2, "G4": crew, "P4": staff, "H4": sick,
        "A5": "ВЫХОДНОГО ДНЯ", "G5": day_off,
        "A7": "ПОДРЯДЧИКИ", "C7": "ПортСервис", "G7": contractors,
        "A13": "Итого", "C13": staff, "G13": present, "H13": sick,
        "S7": "Исправно", "U7": "Ремонт",
        "Q8": "начало смены", "S8": rs_ok_start, "U8": rs_repair_start, "W9": 10,
        "Q10": "конец смены", "S10": rs_ok_end, "U10": rs_repair_end,
        "B2": dispatcher,
    }


def _vessel(ws, row: int, *, berth: str, vessel: str, cargo: str, unload: float, load: float,
            plan_shift: float, fact: float, machine: float, downtime: float, moves: int = 2) -> None:
    _fill(ws, {
        f"A{row}": berth, f"B{row}": vessel, f"C{row}": cargo,
        f"D{row}": unload, f"E{row}": load, f"F{row}": unload + load,
        f"G{row}": datetime(2025, 11, 3, 8, 0),
        f"H{row}": datetime(2025, 11, 1, 4, 0),
        f"I{row}": datetime(2025, 11, 1, 8, 0),
        f"J{row}": plan_shift, f"K{row}": fact, f"L{row}": unload + load,
        f"M{row}": 0, f"N{row}": fact / machine if machine else 0, f"O{row}": moves,
        f"Q{row}": "осадки 1ч",
        f"W{row}": machine + downtime, f"X{row}": downtime, f"Y{row}": machine,
    })


def _rail(ws, row: int, *, cargo: str, station: float, start_l: float, start_e: float,
          processed: float, end_l: float, end_e: float) -> None:
    _fill(ws, {f"B{row}": cargo, f"D{row}": station, f"G{row}": start_l, f"H{row}": start_e,
               f"I{row}": processed, f"K{row}": end_l, f"L{row}": end_e})


def _truck(ws, row: int, *, cargo: str, imp_unload: float, exp_unload: float,
           imp_load: float, exp_load: float, direct: float) -> None:
    _fill(ws, {f"O{row}": cargo, f"Q{row}": imp_unload, f"R{row}": exp_unload,
               f"S{row}": imp_load, f"T{row}": exp_load, f"U{row}": direct,
               f"V{row}": imp_unload + exp_unload + imp_load + exp_load + direct})


def _rail_out(ws, row: int, *, day: datetime, plan: str, semi: float, covered: float,
              flat: float, total: float, fact: float, fact_at: datetime) -> None:
    """Блок «ВЫВОД»: выводка вагонов из порта (план/факт и кто принял)."""
    _fill(ws, {f"A{row}": day, f"B{row}": plan, f"C{row}": semi, f"D{row}": covered,
               f"G{row}": flat, f"H{row}": total, f"I{row}": "ПЧ", f"J{row}": fact,
               f"K{row}": fact_at, f"L{row}": fact_at.strftime("%H:%M")})


def _rail_in(ws, row: int, *, day: datetime, plan: str, settle: str, semi: float,
             flat: float, total: float) -> None:
    """Блок «ВВОД»: постановка вагонов в порт."""
    _fill(ws, {f"A{row}": day, f"B{row}": plan, f"C{row}": settle, f"D{row}": semi,
               f"H{row}": flat, f"L{row}": total})


def _platforms(ws, *, smart: tuple, itk: tuple) -> None:
    """Блок «платформы, выведенные из аренды»: два владельца бок о бок."""
    _fill(ws, {
        "A54": "СМАРТ", "C54": "ИТК-ЛОГИСТИКА 1520",
        "A57": smart[0], "B57": smart[1], "C57": itk[0], "D57": itk[1],
        "A59": smart[2], "B59": smart[3], "C59": itk[2], "D59": itk[3],
    })


def _headers(ws) -> None:
    _fill(ws, {
        "A15": "№ пр", "B15": "Судно", "C15": "Род груза", "J15": "План на смену",
        "K15": "Факт за смену, тн",
        "A24": "Обработка вагонов", "A25": "№", "B25": "Грузы", "O25": "Грузы",
        "N38": "Суда на подходе", "N39": "Дата захода/нахождение у ПБ", "O39": "Судно",
        "A41": "Дата", "J41": "ВЫВЕДЕНО ПО ФАКТУ",
        "A46": "ВВОД", "A47": "Дата", "L47": "Итого",
        "N46": "Ж/Д на подходе", "N47": "Станция дислокации",
        "A53": "ПЛАТФОРМЫ ВЫВЕДЕННЫЕ ИЗ АРЕНДЫ",
    })


def _day_sheet(ws) -> None:
    """Смена 08-20: 60 т из 80 план, 7 вагонов, 8 автомашин."""
    ws.title = "01.11.2025"
    _fill(ws, {"A1": datetime(2025, 11, 1), "A2": "08-20"})
    _fill(ws, _shift_cells(dispatcher="Иванов И.И.", crew=20, staff=25, day_off=1,
                           contractors=5, present=26, sick=2,
                           rs_ok_start=8, rs_repair_start=2, rs_ok_end=7, rs_repair_end=3))
    _headers(ws)
    _vessel(ws, 16, berth="РП - 1/2", vessel="TEST SHIP", cargo="ктк", unload=100, load=0,
            plan_shift=80, fact=60, machine=10, downtime=1)
    _rail(ws, 27, cargo="Калий", station=5, start_l=10, start_e=3, processed=7, end_l=8, end_e=3)
    _truck(ws, 27, cargo="КТК а/м", imp_unload=4, exp_unload=1, imp_load=2, exp_load=1, direct=0)
    _fill(ws, {"N41": datetime(2025, 11, 3, 8, 0), "O41": "NEXT SHIP", "Q41": "1/2",
               "R41": "В", "S41": "ктк", "T41": "100+50", "U41": "Агент"})
    _fill(ws, {"N49": "Шушары", "O49": "калий", "Q49": 57, "R49": "в",
               "S49": "п/в", "T49": "1549-003-0356", "U49": "15 км"})
    _rail_out(ws, 43, day=datetime(2025, 11, 1, 0, 0), plan="23:30", semi=20, covered=4,
              flat=0, total=24, fact=24, fact_at=datetime(2025, 11, 2, 0, 10))
    _rail_in(ws, 49, day=datetime(2025, 11, 1, 0, 0), plan="04:50", settle="05:50",
             semi=57, flat=0, total=57)
    _platforms(ws, smart=(59, 2, 2, 57), itk=(54, 0, 19, 0))


def _night_sheet(ws) -> None:
    """Ночная смена: 40 т из 50 план, имя листа важнее подписи в A2."""
    ws.title = "01-02.11.2025"
    _fill(ws, {"A1": "01-02.11.2025", "A2": "08-20"})   # A2 намеренно неверный
    _fill(ws, _shift_cells(dispatcher="Петров П.П.", crew=15, staff=25, day_off=0,
                           contractors=3, present=18, sick=1,
                           rs_ok_start=7, rs_repair_start=3, rs_ok_end=8, rs_repair_end=2))
    _headers(ws)
    _vessel(ws, 16, berth="РП - 5/6", vessel="NIGHT SHIP", cargo="ген.груз", unload=0, load=200,
            plan_shift=50, fact=40, machine=10, downtime=2)
    _headers(ws)
    _rail_out(ws, 43, day=datetime(2025, 11, 1, 0, 0), plan="21:10", semi=6, covered=0,
              flat=0, total=6, fact=6, fact_at=datetime(2025, 11, 1, 21, 35))
    _rail_in(ws, 49, day=datetime(2025, 11, 1, 0, 0), plan="23:00", settle="00:00",
             semi=60, flat=3, total=63)
    _platforms(ws, smart=(59, 0, 0, 59), itk=(54, 0, 20, 0))


def _workbook() -> bytes:
    wb = openpyxl.Workbook()
    _day_sheet(wb.active)
    _night_sheet(wb.create_sheet())
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _import(client, hdr) -> dict:
    payload = {"content_base64": base64.b64encode(_workbook()).decode(), "filename": "test.xlsm"}
    return client.post("/api/admin/dispatcher/import", json=payload, headers=hdr)


# ---------------------------------------------------------------------- права ----

def test_dispatcher_endpoints_require_auth(client):
    assert client.get("/api/dispatcher/overview").status_code == 401
    assert client.get("/api/dispatcher/meta").status_code == 401
    assert client.get("/api/dispatcher/day", params={"date": "2025-11-01"}).status_code == 401
    assert client.post("/api/admin/dispatcher/import", json={"path": "x.xlsm"}).status_code == 401


def test_import_forbidden_for_manager(client, manager_hdr):
    assert _import(client, manager_hdr).status_code == 403


def test_status_forbidden_for_manager(client, manager_hdr):
    assert client.get("/api/admin/dispatcher/status", headers=manager_hdr).status_code == 403


# --------------------------------------------------------------------- импорт ----

def test_import_rejects_garbage(client, admin_hdr):
    r = client.post("/api/admin/dispatcher/import",
                    json={"content_base64": "not base64 %%%"}, headers=admin_hdr)
    assert r.status_code == 400
    r = client.post("/api/admin/dispatcher/import",
                    json={"content_base64": base64.b64encode(b"zip zip").decode()}, headers=admin_hdr)
    assert r.status_code == 400
    assert client.post("/api/admin/dispatcher/import", json={}, headers=admin_hdr).status_code == 400
    assert client.post("/api/admin/dispatcher/import",
                       json={"path": "Z:\\нет такого файла.xlsm"}, headers=admin_hdr).status_code == 400


def test_import_reads_rail_moves_and_platforms(client, admin_hdr):
    """Блоки «ВЫВОД», «ВВОД» и «платформы из аренды» — тоже данные, а не примечания."""
    r = _import(client, admin_hdr)
    assert r.status_code == 200
    assert r.json()["imported"]["rail_out"] == 2
    assert r.json()["imported"]["rail_in"] == 2
    assert r.json()["imported"]["platform"] == 4      # два владельца × две смены


def test_rail_moves_aggregates(client, admin_hdr):
    mv = client.get("/api/dispatcher/overview", params=PERIOD, headers=admin_hdr).json()["rail_moves"]
    # выводка: 24 + 6 вагонов, постановка: 57 + 63
    assert mv["totals"]["out_cars"] == 30.0
    assert mv["totals"]["in_cars"] == 120.0
    # постановка вся гружёная: 57 + 60 полувагонов и 3 платформы
    assert mv["totals"]["in_loaded"] == 120.0 and mv["totals"]["in_empty"] == 0.0
    assert mv["totals"]["trains_out"] == 2
    # задержка: 23:30 -> 00:10 следующего дня = 40 мин, 21:10 -> 21:35 = 25 мин
    assert mv["totals"]["avg_delay_min"] == 32.5
    assert mv["totals"]["late_trains"] == 2
    mix = {w["wagon_type"]: w for w in mv["by_wagon"]}
    assert mix["полувагон"] == {"wagon_type": "полувагон", "out": 26.0, "in": 117.0}
    assert mix["крытый"]["out"] == 4.0
    assert mix["платформа"]["in"] == 3.0
    # by_day — сутки целиком: день 24 + ночь 6, постановка 57 + 63
    assert mv["by_day"][0]["out_cars"] == 30.0 and mv["by_day"][0]["in_cars"] == 120.0


def test_platform_balance_takes_last_shift(client, admin_hdr):
    """Остаток платформ — нарастающий баланс: показываем конец периода и сдвиг."""
    plats = client.get("/api/dispatcher/overview", params=PERIOD, headers=admin_hdr).json()["platforms"]
    by_owner = {p["owner"]: p for p in plats}
    assert by_owner["СМАРТ"]["total_cars"] == 59.0
    assert by_owner["СМАРТ"]["remaining"] == 0.0          # ночная смена: остаток 0
    assert by_owner["СМАРТ"]["remaining_delta"] == -2.0   # был 2 в дневной
    assert by_owner["ИТК-ЛОГИСТИКА 1520"]["terminal"] == 20.0


def test_rows_endpoint_drills_into_moves(client, admin_hdr):
    """Клик по графику = таблица строк витрины, из которых он собран."""
    r = client.get("/api/dispatcher/rows", params={**PERIOD, "kind": "rail_move"},
                   headers=admin_hdr)
    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 4                     # 2 вывода + 2 ввода
    assert body["totals"]["total"] == 150.0
    assert {row["direction"] for row in body["rows"]} == {"вывод", "ввод"}
    assert any(row["delay_min"] == 40.0 for row in body["rows"])   # через полночь

    r2 = client.get("/api/dispatcher/rows",
                    params={**PERIOD, "kind": "berth", "key": "РП - 1/2"}, headers=admin_hdr)
    assert r2.status_code == 200
    assert len(r2.json()["rows"]) == 1
    # темп и проценты не суммируются — итог по ним бессмысленный
    assert "tph" not in r2.json()["totals"] and "plan_pct" not in r2.json()["totals"]

    assert client.get("/api/dispatcher/rows", params={**PERIOD, "kind": "nope"},
                      headers=admin_hdr).status_code == 422


def test_day_detail_has_rail_moves(client, admin_hdr):
    day = client.get("/api/dispatcher/day", params={"date": "2025-11-01"}, headers=admin_hdr).json()
    assert len(day["rail_out"]) == 2 and len(day["rail_in"]) == 2
    assert len(day["platforms"]) == 4
    assert day["rail_out"][0]["delay_min"] in (40.0, 25.0)


def test_import_reports_counts(client, admin_hdr):
    r = _import(client, admin_hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"]["shift"] == 2
    assert body["imported"]["vessel"] == 2
    assert body["date_min"] == "2025-11-01" and body["date_max"] == "2025-11-01"


def test_import_is_idempotent(client, admin_hdr):
    _import(client, admin_hdr)
    _import(client, admin_hdr)          # повторный импорт того же файла
    ov = client.get("/api/dispatcher/overview", params=PERIOD, headers=admin_hdr).json()
    assert ov["kpi"]["calls"] == 2, "строки смены не должны множиться при повторном импорте"
    assert ov["kpi"]["throughput_t"] == 100.0


# ------------------------------------------------------------------ агрегаты ----

def test_overview_kpi(client, admin_hdr):
    ov = client.get("/api/dispatcher/overview", params=PERIOD, headers=admin_hdr).json()
    kpi = ov["kpi"]
    assert kpi["throughput_t"] == 100.0            # 60 день + 40 ночь
    assert kpi["plan_shift_t"] == 130.0
    assert kpi["plan_pct"] == 76.9                 # 100 / 130
    assert kpi["machine_h"] == 20.0 and kpi["downtime_h"] == 3.0
    assert kpi["avg_tph"] == 5.0                   # 100 т / 20 машинных часов
    assert kpi["rail_cars"] == 7.0
    assert kpi["trucks"] == 8.0
    assert kpi["days"] == 1 and kpi["shifts"] == 2
    # персонал — средняя смена, а не сумма дня и ночи: 20 и 15 при штате 25
    assert kpi["crew_present"] == 17.5
    assert kpi["crew_staff"] == 25.0
    assert kpi["crew_rate"] == 70.0
    # ричстакеры: исправно 15 из 20 человеко-смен = 75 %
    assert kpi["rs_availability"] == 75.0


def test_overview_split_and_details(client, admin_hdr):
    ov = client.get("/api/dispatcher/overview", params=PERIOD, headers=admin_hdr).json()

    assert [s["shift"] for s in ov["shift_split"]] == ["08-20", "20-08"]
    assert ov["shift_split"][0]["throughput_t"] == 60.0
    assert ov["shift_split"][1]["throughput_t"] == 40.0

    mix = {c["cargo_group"]: c for c in ov["cargo_mix"]}
    assert mix["ктк"]["tons"] == 60.0 and mix["ген.груз"]["tons"] == 40.0
    assert mix["ктк"]["share"] == 60.0

    berths = {b["berth"]: b for b in ov["berths"]}
    assert berths["РП - 1/2"]["calls"] == 1 and berths["РП - 5/6"]["throughput_t"] == 40.0

    assert ov["rail"]["totals"]["processed"] == 7.0
    assert ov["rail"]["totals"]["end_empty"] == 3.0
    assert ov["trucks"]["totals"]["imp_unload"] == 4.0
    assert ov["trucks"]["totals"]["total"] == 8.0

    # плановый заход: «100+50» → 150 т
    assert ov["arrivals"][0]["vessel"] == "NEXT SHIP"
    assert ov["arrivals"][0]["plan_t"] == 150.0
    assert ov["rail_arrivals"][0]["station"] == "Шушары"
    assert ov["rail_arrivals"][0]["cars"] == 57.0


def test_shift_filter(client, admin_hdr):
    ov = client.get("/api/dispatcher/overview",
                    params={**PERIOD, "shift": "08-20"}, headers=admin_hdr).json()
    assert ov["kpi"]["throughput_t"] == 60.0
    assert ov["kpi"]["shifts"] == 1
    assert ov["shift_split"] == [] or all(s["shift"] == "08-20" for s in ov["shift_split"])
    bad = client.get("/api/dispatcher/overview", params={**PERIOD, "shift": "06-18"},
                     headers=admin_hdr)
    assert bad.status_code == 422


def test_period_defaults_to_data_bounds(client, admin_hdr):
    meta = client.get("/api/dispatcher/meta", headers=admin_hdr).json()
    assert meta["date_min"] == "2025-11-01" and meta["date_max"] == "2025-11-01"
    ov = client.get("/api/dispatcher/overview", headers=admin_hdr).json()
    assert ov["period"]["from"] == "2025-11-01" and ov["period"]["to"] == "2025-11-01"
    assert ov["kpi"]["shifts"] == 2


def test_reversed_period_rejected(client, admin_hdr):
    r = client.get("/api/dispatcher/overview",
                   params={"from": "2025-11-05", "to": "2025-11-01"}, headers=admin_hdr)
    assert r.status_code == 400


def test_day_detail(client, admin_hdr):
    d = client.get("/api/dispatcher/day", params={"date": "2025-11-01"}, headers=admin_hdr).json()
    assert d["date"] == "2025-11-01"
    assert {s["shift"] for s in d["shifts"]} == {"08-20", "20-08"}
    assert d["shifts"][0]["dispatcher"] in ("Иванов И.И.", "Петров П.П.")
    ships = {v["vessel"]: v for v in d["vessels"]}
    assert ships["TEST SHIP"]["fact_shift_t"] == 60.0
    assert ships["TEST SHIP"]["plan_pct"] == 75.0
    assert d["trucks"][0]["total"] == 8.0
    assert d["rails"][0]["processed"] == 7.0


def test_overview_contract_keys_for_frontend(client, admin_hdr):
    """Страница «Статистика» читает эти поля напрямую — ключ не должен теряться.

    Ключевое слово теста: сериализация, а не математика. Один раз `staff`
    уже выпал из `dynamics`, и фронт упал на пустом значении.
    """
    ov = client.get("/api/dispatcher/overview", params=PERIOD, headers=admin_hdr).json()

    assert set(ov) >= {"period", "meta", "kpi", "dynamics", "cargo_mix", "direction",
                       "shift_split", "berths", "vessels_top", "weekday", "rail",
                       "trucks", "arrivals", "rail_arrivals", "shift_filter",
                       "rail_moves", "platforms"}
    assert set(ov["meta"]) >= {"date_min", "date_max", "updated_at", "sources"}

    for key in ("throughput_t", "plan_shift_t", "plan_pct", "calls", "vessels_unique",
                "avg_tph", "machine_h", "downtime_h", "downtime_pct", "rail_cars",
                "trucks", "crew_present", "crew_staff", "crew_rate", "rs_availability",
                "days", "shifts"):
        assert key in ov["kpi"], f"kpi.{key}"

    for row in ov["dynamics"]:
        for key in ("bucket_date", "throughput_t", "plan_shift_t", "plan_pct", "calls",
                    "machine_h", "downtime_h", "downtime_pct", "rail_cars", "trucks",
                    "present", "crew", "contractors", "staff", "day_off", "sick",
                    "vacation", "other_absent", "crew_rate", "shifts",
                    "rs_ok_start", "rs_repair_start", "rs_ok_end", "rs_repair_end",
                    "rs_total", "rs_availability"):
            assert key in row, f"dynamics.{key}"

    for row in ov["vessels_top"]:
        assert set(row) >= {"vessel", "calls", "tons", "machine_h", "downtime_h", "tph", "cargos"}
    for row in ov["berths"]:
        assert set(row) >= {"berth", "calls", "throughput_t", "vessels", "machine_h", "downtime_h", "downtime_pct"}
    # вагоны по суткам фронт берёт из dynamics (rail_cars), у rails — итог и номенклатура
    assert set(ov["rail"]) >= {"totals", "by_cargo"}
    assert set(ov["trucks"]) >= {"totals", "by_cargo", "by_day"}
    for row in ov["rail_arrivals"]:
        assert set(row) >= {"station", "cars", "trains", "cargos", "work_kinds"}
    # выводка/постановка вагонов и платформы из аренды — блоки «ВЫВОД», «ВВОД», «ПЛАТФОРМЫ»
    assert set(ov["rail_moves"]) >= {"totals", "by_day", "by_wagon"}
    assert set(ov["rail_moves"]["totals"]) >= {"out_cars", "in_cars", "in_loaded", "in_empty"}
    for row in ov["rail_moves"]["by_day"]:
        assert set(row) >= {"bucket_date", "out_cars", "in_cars", "avg_delay_min"}
    for row in ov["platforms"]:
        assert set(row) >= {"owner", "total_cars", "remaining", "terminal", "sent", "remaining_delta"}


def test_drilldown_rows_without_key(client, admin_hdr):
    """Клик по легенде графика (без ключа) обязан показать весь разрез, а не пустоту."""
    assert _import(client, admin_hdr).status_code == 200
    for kind in ("vessel", "berth", "cargo", "rail_cargo", "truck_cargo", "station",
                 "rail_move", "platform"):
        r = client.get("/api/dispatcher/rows", params={"from": "2025-11-01", "to": "2025-11-01",
                                                       "kind": kind}, headers=admin_hdr)
        assert r.status_code == 200, (kind, r.status_code)
        body = r.json()
        assert body["rows"], f"разрез {kind} без ключа вернул пустой список"
        assert body["title"], kind
        assert body["totals"], kind


def test_admin_status_reports_vitrine_bounds(client, admin_hdr):
    """Админка показывает не только путь к файлу, но и что реально в витрине."""
    st = client.get("/api/admin/dispatcher/status", headers=admin_hdr).json()
    assert st["counts"]["shifts"] == 2
    assert st["date_min"] == "2025-11-01" and st["date_max"] == "2025-11-01"
    assert st["updated_at"]
