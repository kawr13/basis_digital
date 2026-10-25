"""Тесты документа «Постановка судов»: справочник причалов, ручные строки плана,
просмотр за любой период, пересечения по времени и по длине причала и права."""
from __future__ import annotations

from datetime import date, datetime, timedelta

PERIOD = {"from": "2026-05-01", "to": "2026-05-31"}


def _berth_id(client, hdr, code="РП-1") -> int:
    rows = client.get("/api/vessels/berths", headers=hdr).json()
    return next(b["id"] for b in rows if b["code"] == code)


def _mk(client, hdr, *, code="РП-1", lane=1, vessel="TEST SHIP", start="2026-05-03T06:00:00",
        end="2026-05-05T12:00:00", **kw):
    body = {
        "berth_id": _berth_id(client, hdr, code), "lane": lane, "vessel": vessel,
        "starts_at": start, "ends_at": end, **kw,
    }
    return client.post("/api/vessels/placements", json=body, headers=hdr)


# ------------------------------------------------------------------ справочник ----


def test_berths_seeded(client, admin_hdr):
    rows = client.get("/api/vessels/berths", headers=admin_hdr).json()
    assert len(rows) >= 6, rows
    assert rows[0]["code"] == "РП-1"
    assert rows[0]["length_m"] == 100 and rows[0]["depth"] == 8.3


def test_berth_create_and_duplicate(client, admin_hdr):
    r = client.post("/api/vessels/berths", json={"code": "Тest-99", "length_m": 90, "lanes": 1},
                    headers=admin_hdr)
    assert r.status_code == 201, r.text
    again = client.post("/api/vessels/berths", json={"code": "Тest-99"}, headers=admin_hdr)
    assert again.status_code == 409


def test_berth_code_must_be_readable(client, admin_hdr):
    """Код причала печатают в плане — пустой или из знаков не пропустим."""
    for bad in ("", " ", "-", "  ", "A"):
        r = client.post("/api/vessels/berths", json={"code": bad}, headers=admin_hdr)
        assert r.status_code == 422, (bad, r.text)


def test_berth_deactivate_hides_from_plan(client, admin_hdr, manager_hdr):
    """Снят с линии: в справочнике есть, в сетке плана его нет."""
    bid = _berth_id(client, admin_hdr)
    berth = next(b for b in client.get("/api/vessels/berths", headers=admin_hdr).json()
                 if b["id"] == bid)
    berth["is_active"] = False
    r = client.patch(f"/api/vessels/berths/{bid}", json=berth, headers=admin_hdr)
    assert r.status_code == 200, r.text
    plan = client.get("/api/vessels/placements", params=PERIOD, headers=manager_hdr).json()
    assert bid not in {b["id"] for b in plan["berths"]}
    assert bid in {b["id"] for b in client.get("/api/vessels/berths", headers=admin_hdr).json()}


def test_berth_delete_only_if_empty(client, admin_hdr):
    """Причал с историей удалить нельзя — только снять с линии."""
    created = client.post("/api/vessels/berths",
                          json={"code": "ПУСТОЙ-77", "length_m": 120}, headers=admin_hdr)
    assert created.status_code == 201, created.text
    empty_id = created.json()["id"]

    # свой причал под историю: чужие тесты не должны зависеть от этого судна
    busy = client.post("/api/vessels/berths", json={"code": "ЗАНЯТОЙ-78", "length_m": 150},
                       headers=admin_hdr).json()["id"]
    body = {"berth_id": busy, "vessel": "НОСИТЕЛЬ ИСТОРИИ", "length_m": 60, "offset_m": 0,
            "starts_at": "2027-01-03T06:00:00", "ends_at": "2027-01-05T12:00:00"}
    assert client.post("/api/vessels/placements", json=body, headers=admin_hdr).status_code == 201
    blocked = client.delete(f"/api/vessels/berths/{busy}", headers=admin_hdr)
    assert blocked.status_code == 409
    assert "снимите его с линии" in blocked.json()["detail"]

    ok = client.delete(f"/api/vessels/berths/{empty_id}", headers=admin_hdr)
    assert ok.status_code == 204
    assert empty_id not in {b["id"] for b in client.get("/api/vessels/berths", headers=admin_hdr).json()}


def test_admin_sees_berth_book_even_if_plan_closed(client, admin_hdr):
    """Личный запрет на план не должен ломать админскую вкладку «Причалы»."""
    users = client.get("/api/admin/users", headers=admin_hdr).json()
    aid = next(u["id"] for u in users if u["login"] == "admin")
    assert client.patch(f"/api/admin/users/{aid}", json={"vessel_right": "none"},
                        headers=admin_hdr).status_code == 200
    try:
        assert client.get("/api/vessels/berths", headers=admin_hdr).status_code == 200
    finally:
        client.patch(f"/api/admin/users/{aid}", json={"vessel_right": None}, headers=admin_hdr)


def test_plan_pdf_is_a_real_pdf_with_cyrillic(client, admin_hdr, manager_hdr):
    """PDF строит бэкенд: файл должен открыться, а русские названия — читаться.

    Свой причал и своё время: тесты делят одну базу, и судно на общем РП-1
    ломало бы чужие проверки на конфликте.
    """
    own = client.post("/api/vessels/berths", json={"code": "PDF-1", "length_m": 120},
                      headers=admin_hdr).json()["id"]
    body = {"berth_id": own, "vessel": "СВЕТЛОГОРСК", "volume": "4 000", "cargo": "СБОРНИК",
            "length_m": 60, "offset_m": 0,
            "starts_at": "2026-05-03T06:00:00", "ends_at": "2026-05-05T12:00:00"}
    assert client.post("/api/vessels/placements", json=body, headers=admin_hdr).status_code == 201
    r = client.get("/api/vessels/placements", params=PERIOD, headers=manager_hdr).json()
    ids = ",".join(str(b["id"]) for b in r["berths"][:3])
    pdf = client.get("/api/vessels/plan.pdf",
                     params={**PERIOD, "day_from": 2, "day_to": 9, "berth_ids": ids},
                     headers=manager_hdr)
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"] == "application/pdf"
    assert "attachment" in pdf.headers["content-disposition"]
    body = pdf.content
    assert body[:5] == b"%PDF-" and body.rstrip().endswith(b"%%EOF")
    assert len(body) > 2000, "PDF подозрительно маленький — похоже, нечего рисовать"

    from pypdf import PdfReader
    import io
    text = "".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(body)).pages)
    assert "СВЕТЛОГОРСК" in text, f"кириллица потерялась: {text[:200]}"
    assert "План постановки судов" in text


def test_plan_pdf_window_and_rights(client, admin_hdr, manager_hdr):
    """В файл попадают только сутки и причалы из кадра; без права view — 403."""
    own = client.post("/api/vessels/berths", json={"code": "PDF-2", "length_m": 100},
                      headers=admin_hdr).json()["id"]
    body = {"berth_id": own, "vessel": "PDF SHIP", "length_m": 50, "offset_m": 0,
            "starts_at": "2026-05-20T06:00:00", "ends_at": "2026-05-21T12:00:00"}
    assert client.post("/api/vessels/placements", json=body, headers=admin_hdr).status_code == 201
    # сосед по периоду: его причал и его сутки за кадром — в листе их быть не должно
    other = client.post("/api/vessels/berths", json={"code": "PDF-2B", "length_m": 100},
                        headers=admin_hdr).json()["id"]
    for berth, vessel, start, end in (
        (other, "ВНЕ ПРИЧАЛА", "2026-05-20T06:00:00", "2026-05-21T12:00:00"),
        (own, "ВНЕ СУТОК", "2026-05-27T06:00:00", "2026-05-28T12:00:00"),
    ):
        assert client.post("/api/vessels/placements", json={
            "berth_id": berth, "vessel": vessel, "length_m": 40, "offset_m": 0,
            "starts_at": start, "ends_at": end}, headers=admin_hdr).status_code == 201
    ids = str(own)
    narrow = client.get("/api/vessels/plan.pdf",
                        params={**PERIOD, "day_from": 0, "day_to": 2, "berth_ids": ids},
                        headers=manager_hdr)
    assert narrow.status_code == 200

    from pypdf import PdfReader
    import io
    text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(narrow.content)).pages)
    assert "ВНЕ СУТОК" not in text, "судно за границей кадра по суткам уехало в лист"
    assert "ВНЕ ПРИЧАЛА" not in text, "причал за границей кадра уехал в лист"
    assert client.get("/api/vessels/plan.pdf", params=PERIOD,
                      headers={"Authorization": "Bearer nope"}).status_code == 401
    # закрытому праву файл не отдаём
    uid = _user_id(client, admin_hdr, "sidorova")
    client.patch(f"/api/admin/users/{uid}", json={"vessel_right": "none"}, headers=admin_hdr)
    tok = client.post("/api/auth/login", json={"login": "sidorova", "password": "demo1234"}).json()["access_token"]
    denied = client.get("/api/vessels/plan.pdf", params=PERIOD,
                        headers={"Authorization": f"Bearer {tok}"})
    assert denied.status_code == 403
    client.patch(f"/api/admin/users/{uid}", json={"vessel_right": None}, headers=admin_hdr)


def test_plan_pdf_header_on_every_sheet(client, admin_hdr, manager_hdr):
    """Листов несколько — шапка и сноска обязаны быть на каждом.

    Листы распечатывают и по одному: на листе без заголовка и без дат окна
    непонятно, какой это кусок плана. Раньше второй лист уходил без шапки:
    при переносе причала на новую страницу флаг «первый лист» ещё не сбросили.
    """
    made = []
    for i in range(6):
        berth = client.post("/api/vessels/berths", json={"code": f"MUL-{i}", "length_m": 130},
                            headers=admin_hdr).json()["id"]
        pid = client.post("/api/vessels/placements", json={
            "berth_id": berth, "vessel": f"ЛИСТ {i}", "length_m": 60, "offset_m": 0,
            "starts_at": "2026-07-02T06:00:00", "ends_at": "2026-07-03T12:00:00"},
            headers=admin_hdr).json()["id"]
        made.append((berth, pid))

    pdf = client.get("/api/vessels/plan.pdf",
                     params={"from": "2026-07-01", "to": "2026-07-07",
                             "day_from": 0, "day_to": 6,
                             "berth_ids": ",".join(str(b) for b, _ in made)},
                     headers=manager_hdr)
    assert pdf.status_code == 200, pdf.text

    from pypdf import PdfReader
    import io
    pages = PdfReader(io.BytesIO(pdf.content)).pages
    assert len(pages) > 1, f"шесть причалов по 130 м должны не на один лист, а их {len(pages)}"
    for n, page in enumerate(pages, start=1):
        text = page.extract_text() or ""
        assert "План постановки судов" in text, f"лист {n} без шапки"
        assert "Пунктир" in text, f"лист {n} без сноски"
        assert "причал / метры" in text, f"лист {n} без шапки суток"

    for berth, pid in made:   # прочий план не засоряем
        client.delete(f"/api/vessels/placements/{pid}", headers=admin_hdr)
        assert client.delete(f"/api/vessels/berths/{berth}", headers=admin_hdr).status_code == 204


def test_berth_delete_needs_admin(client, admin_hdr):
    """Справочник причалов — админская зона: топ-менеджмент править справочник не может."""
    top = client.post("/api/auth/login", json={"login": "ivanova", "password": "demo1234"})
    assert top.status_code == 200, top.text
    hdr = {"Authorization": f"Bearer {top.json()['access_token']}"}
    bid = _berth_id(client, admin_hdr)
    assert client.delete(f"/api/vessels/berths/{bid}", headers=hdr).status_code == 403
    assert client.post("/api/vessels/berths", json={"code": "НОВЫЙ-ТОП"}, headers=hdr).status_code == 403


# ------------------------------------------------------------------- создание ----


def test_create_and_read(client, admin_hdr):
    r = _mk(client, admin_hdr, vessel="CROWN SAPPHIRE", volume="5 500", cargo="БШ",
            note="сборник")
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["vessel"] == "CROWN SAPPHIRE" and row["cargo"] == "БШ"
    assert row["starts_at"] == "2026-05-03T06:00:00"

    data = client.get("/api/vessels/placements", params=PERIOD, headers=admin_hdr).json()
    got = [p for p in data["placements"] if p["id"] == row["id"]]
    assert got and got[0]["lane"] == 1
    assert data["period"] == {"from": "2026-05-01", "to": "2026-05-31"}
    assert data["slot_hours"] == [0, 6, 12, 18]


def test_period_covers_running_placement(client, admin_hdr):
    """План «непрерывный»: судно, начатое в апреле, видно и в мае."""
    r = _mk(client, admin_hdr, code="РП-2", vessel="APRIL SHIP",
            start="2026-04-25T18:00:00", end="2026-05-02T00:00:00")
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    may = client.get("/api/vessels/placements", params=PERIOD, headers=admin_hdr).json()
    assert pid in [p["id"] for p in may["placements"]]

    # период целиком после окончания — уже нет
    june = client.get("/api/vessels/placements",
                      params={"from": "2026-06-01", "to": "2026-06-30"}, headers=admin_hdr).json()
    assert pid not in [p["id"] for p in june["placements"]]

    # период целиком до начала — тоже нет
    march = client.get("/api/vessels/placements",
                       params={"from": "2026-03-01", "to": "2026-03-31"}, headers=admin_hdr).json()
    assert pid not in [p["id"] for p in march["placements"]]


def test_period_default_is_forward(client, admin_hdr):
    data = client.get("/api/vessels/placements", headers=admin_hdr).json()
    d_from, d_to = date.fromisoformat(data["period"]["from"]), date.fromisoformat(data["period"]["to"])
    assert d_from == date.today() and d_to > d_from


def test_period_reversed_is_normalized(client, admin_hdr):
    data = client.get("/api/vessels/placements",
                      params={"from": "2026-05-31", "to": "2026-05-01"}, headers=admin_hdr).json()
    assert data["period"] == {"from": "2026-05-01", "to": "2026-05-31"}


# ------------------------------------------------------------------- правила ----


def test_length_overlap_conflicts(client, admin_hdr):
    """Два судна в одно время у одного причала мешают друг другу только по длине.

    Шкала внутри причала — метровая: 100 м = 10 ячеек по 10 м. РП-3 — 130 м,
    значит у него могут стоять два судна одновременно, если они не наезжают
    друг на друга по длине.
    """
    a = _mk(client, admin_hdr, code="РП-3", vessel="FIRST", length_m=60, offset_m=0,
            start="2026-05-10T00:00:00", end="2026-05-12T00:00:00")
    assert a.status_code == 201, a.text
    assert a.json()["length_m"] == 60 and a.json()["offset_m"] == 0

    # 40–100 м наезжает на 0–60 м
    b = _mk(client, admin_hdr, code="РП-3", vessel="SECOND", length_m=60, offset_m=40,
            start="2026-05-11T12:00:00", end="2026-05-13T00:00:00")
    assert b.status_code == 409, b.text
    detail = b.json()["detail"]
    assert "FIRST" in detail and "РП-3" in detail and "40–60 м" in detail, detail

    # 70–130 м — вдоль причала места хватило
    c = _mk(client, admin_hdr, code="РП-3", vessel="THIRD", length_m=60, offset_m=70,
            start="2026-05-11T12:00:00", end="2026-05-13T00:00:00")
    assert c.status_code == 201, c.text


def test_vessel_must_fit_the_berth(client, admin_hdr):
    """Длина судна сверяется с метражом причала: РП-1 — 100 м."""
    too_long = _mk(client, admin_hdr, code="РП-1", vessel="TOO LONG", length_m=140,
                   start="2026-07-01T00:00:00", end="2026-07-02T00:00:00")
    assert too_long.status_code == 400
    assert "длина причала 100 м" in too_long.json()["detail"], too_long.text

    over_edge = _mk(client, admin_hdr, code="РП-1", vessel="OVER THE EDGE", length_m=60, offset_m=60,
                    start="2026-07-01T00:00:00", end="2026-07-02T00:00:00")
    assert over_edge.status_code == 400
    assert "отметка 60 м" in over_edge.json()["detail"], over_edge.text


def test_unknown_length_takes_whole_berth(client, admin_hdr):
    """Длина не указана — считаем занятым весь причал, иначе кабинет разрешил бы
    вторую постановку «рядом», не зная, сколько на самом деле стоит."""
    a = _mk(client, admin_hdr, code="РП-5", vessel="NO LENGTH",
            start="2026-09-01T00:00:00", end="2026-09-02T00:00:00")
    assert a.status_code == 201, a.text
    b = _mk(client, admin_hdr, code="РП-5", vessel="NEXT TO IT", length_m=30, offset_m=50,
            start="2026-09-01T06:00:00", end="2026-09-01T18:00:00")
    assert b.status_code == 409, b.text

    # без длины нельзя поставить и не с начала причала — непонятно, сколько занято
    c = _mk(client, admin_hdr, code="РП-5", vessel="OFFSET WITHOUT LENGTH", offset_m=20,
            start="2026-09-05T00:00:00", end="2026-09-06T00:00:00")
    assert c.status_code == 400 and "Укажите длину" in c.json()["detail"]


def test_free_offset_suggests_free_spot(client, admin_hdr):
    """Форма подсказывает, где свободно: не заставляем диспетчера гадать и ловить 409."""
    _mk(client, admin_hdr, code="РП-3", vessel="BLOCKS START", length_m=50, offset_m=0,
        start="2026-10-05T00:00:00", end="2026-10-06T00:00:00")
    r = client.get("/api/vessels/free-offset",
                   params={"berth_id": _berth_id(client, admin_hdr, "РП-3"),
                           "starts_at": "2026-10-05T00:00:00", "ends_at": "2026-10-06T00:00:00",
                           "length_m": 40},
                   headers=admin_hdr)
    assert r.status_code == 200, r.text
    assert r.json()["offset_m"] == 50 and r.json()["fits"] is True


def test_length_cells_scale(client, admin_hdr):
    """Сетка длины: 10 м на ячейку. 130 м → 13 ячеек, 100 м → 10."""
    rows = client.get("/api/vessels/berths", headers=admin_hdr).json()
    by_code = {b["code"]: b for b in rows}
    assert by_code["РП-1"]["length_cells"] == 10
    assert by_code["РП-3"]["length_cells"] == 13


def test_touching_slots_do_not_conflict(client, admin_hdr):
    """Одно судно ушло в 12:00, другое пришло в 12:00 — слот один, не пересечение?

    Нет: границы включительные, значит второй слот начинается ровно там же, где
    занят первый. Пересечение есть — это и есть защита от «нарисовать два судна
    в одной клетке».
    """
    a = _mk(client, admin_hdr, code="РП-4", lane=1, vessel="LEAVES",
            start="2026-06-10T00:00:00", end="2026-06-10T12:00:00")
    assert a.status_code == 201, a.text
    b = _mk(client, admin_hdr, code="РП-4", lane=1, vessel="ARRIVES",
            start="2026-06-10T18:00:00", end="2026-06-11T00:00:00")
    assert b.status_code == 201, b.text


def test_bad_slot_time(client, admin_hdr):
    r = _mk(client, admin_hdr, code="РП-5", vessel="WRONG TIME", start="2026-05-03T07:00:00",
            end="2026-05-04T00:00:00")
    assert r.status_code == 400 and "слот" in r.json()["detail"].lower()


def test_end_before_start(client, admin_hdr):
    r = _mk(client, admin_hdr, code="РП-5", vessel="BACKWARD", start="2026-05-06T00:00:00",
            end="2026-05-04T00:00:00")
    assert r.status_code == 400


def test_unknown_berth(client, admin_hdr):
    r = client.post("/api/vessels/placements",
                    json={"berth_id": 999999, "vessel": "GHOST",
                          "starts_at": "2026-05-01T00:00:00", "ends_at": "2026-05-02T00:00:00"},
                    headers=admin_hdr)
    assert r.status_code == 400


# --------------------------------------------------------------- правка/удаление ----


def test_update_moves_and_allows_own_slot(client, admin_hdr):
    r = _mk(client, admin_hdr, code="РП-6", lane=1, vessel="MOVER",
            start="2026-08-01T00:00:00", end="2026-08-02T00:00:00")
    pid = r.json()["id"]
    # переносим в ту же самую клетку: сама с собой конфликта быть не должно
    upd = client.patch(f"/api/vessels/placements/{pid}",
                       json={"starts_at": "2026-08-01T00:00:00", "ends_at": "2026-08-02T12:00:00",
                             "cargo": "МЛК (БКК)"},
                       headers=admin_hdr)
    assert upd.status_code == 200, upd.text
    assert upd.json()["cargo"] == "МЛК (БКК)" and upd.json()["ends_at"] == "2026-08-02T12:00:00"

    # а перенос на соседнее судно — конфликт
    other = _mk(client, admin_hdr, code="РП-6", lane=1, vessel="BLOCKER",
                start="2026-08-05T00:00:00", end="2026-08-06T00:00:00")
    assert other.status_code == 201
    clash = client.patch(f"/api/vessels/placements/{pid}",
                         json={"starts_at": "2026-08-05T12:00:00", "ends_at": "2026-08-06T06:00:00"},
                         headers=admin_hdr)
    assert clash.status_code == 409 and "BLOCKER" in clash.json()["detail"]


def test_delete_only_admin(client, admin_hdr, manager_hdr):
    pid = _mk(client, admin_hdr, code="РП-1", lane=2, vessel="TO DELETE",
              start="2026-09-01T00:00:00", end="2026-09-02T00:00:00").json()["id"]
    assert client.delete(f"/api/vessels/placements/{pid}", headers=manager_hdr).status_code == 403
    assert client.delete(f"/api/vessels/placements/{pid}", headers=admin_hdr).status_code == 204
    data = client.get("/api/vessels/placements",
                      params={"from": "2026-09-01", "to": "2026-09-30"}, headers=admin_hdr).json()
    assert pid not in [p["id"] for p in data["placements"]]


# ---------------------------------------------------------------------- права ----


def test_manager_reads_but_not_writes(client, manager_hdr):
    data = client.get("/api/vessels/placements", params=PERIOD, headers=manager_hdr)
    assert data.status_code == 200
    assert len(data.json()["berths"]) >= 6
    r = _mk(client, manager_hdr, vessel="MANAGER TRY")
    assert r.status_code == 403


def test_top_can_write(client):
    tok = client.post("/api/auth/login",
                      json={"login": "ivanova", "password": "demo1234"}).json()["access_token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    r = _mk(client, hdr, code="РП-2", lane=2, vessel="TOP PLAN",
            start="2026-10-01T00:00:00", end="2026-10-03T06:00:00")
    assert r.status_code == 201, r.text


def test_anonymous_denied(client):
    assert client.get("/api/vessels/placements").status_code == 401


def test_audit_records_actions(client, admin_hdr):
    """Правки плана должны оставаться в журнале — это документ, а не заметка."""
    _mk(client, admin_hdr, code="РП-3", lane=2, vessel="AUDIT SHIP",
        start="2026-11-01T00:00:00", end="2026-11-02T00:00:00")
    rows = client.get("/api/admin/audit", params={"limit": 200}, headers=admin_hdr).json()
    actions = [r["action"] for r in rows]
    assert "vessels.create" in actions
    detail = next(r["detail"] for r in rows if r["action"] == "vessels.create")
    assert "AUDIT SHIP" in detail


# ----------------------------------------------------- роли из настроек админки ----


def _set_settings(client, hdr, patch: dict):
    return client.put("/api/admin/settings", json=patch, headers=hdr)


def _reset(client, hdr, keys):
    return client.post("/api/admin/settings/reset", json=keys, headers=hdr)


def test_roles_come_from_settings(client, admin_hdr, manager_hdr):
    """Право ставить суда можно выдать менеджеру настройкой, без смены роли."""
    keys = ["vessels.view_roles", "vessels.edit_roles", "vessels.delete_roles"]
    try:
        r = _set_settings(client, admin_hdr, {"vessels.edit_roles": "manager"})
        assert r.status_code == 200, r.text

        made = _mk(client, manager_hdr, code="РП-4", lane=2, vessel="MANAGER NOW",
                   start="2027-01-05T00:00:00", end="2027-01-06T00:00:00")
        assert made.status_code == 201, made.text
        pid = made.json()["id"]
        # но удалять по-прежнему нельзя: право на удаление — отдельная настройка
        assert client.delete(f"/api/vessels/placements/{pid}", headers=manager_hdr).status_code == 403
        assert client.delete(f"/api/vessels/placements/{pid}", headers=admin_hdr).status_code == 204
    finally:
        _reset(client, admin_hdr, keys)

    # после сброса менеджер снова только наблюдает
    assert _mk(client, manager_hdr, code="РП-4", lane=2, vessel="AFTER RESET",
               start="2027-02-05T00:00:00", end="2027-02-06T00:00:00").status_code == 403


def test_view_can_be_closed_for_role(client, admin_hdr, manager_hdr):
    keys = ["vessels.view_roles"]
    try:
        assert _set_settings(client, admin_hdr, {"vessels.view_roles": "admin,top"}).status_code == 200
        r = client.get("/api/vessels/placements", params=PERIOD, headers=manager_hdr)
        assert r.status_code == 403 and "закрыт" in r.json()["detail"]
        assert client.get("/api/vessels/berths", headers=manager_hdr).status_code == 403
        assert client.get("/api/vessels/placements", params=PERIOD, headers=admin_hdr).status_code == 200
    finally:
        _reset(client, admin_hdr, keys)


def test_unknown_role_in_setting_rejected(client, admin_hdr):
    r = _set_settings(client, admin_hdr, {"vessels.edit_roles": "dispatcher"})
    assert r.status_code == 422
    assert "admin, top, manager" in r.json()["detail"]
    # пустой список тоже не проходят: план не должен остаться без владельцев
    assert _set_settings(client, admin_hdr, {"vessels.delete_roles": ""}).status_code == 422


def test_public_settings_expose_roles(client, manager_hdr):
    """Фронт рисует кнопки по этим же значениям — иначе права расходятся с UI."""
    pub = client.get("/api/settings/public", headers=manager_hdr).json()
    roles = lambda k: set(pub[k].split(","))
    assert roles("vessels.view_roles") == {"admin", "top", "manager"}
    assert roles("vessels.edit_roles") == {"admin", "top"}
    assert roles("vessels.delete_roles") == {"admin"}


# ------------------------------------------- личное право пользователя (как склады) ----


def _user_id(client, hdr, login):
    return next(u["id"] for u in client.get("/api/admin/users", headers=hdr).json() if u["login"] == login)


def _token(client, login, password):
    return client.post("/api/auth/login", json={"login": login, "password": password}).json()["access_token"]


def test_personal_right_beats_role(client, admin_hdr):
    """Диспетчеру можно выдать постановку прямо в карточке пользователя, не меняя роль."""
    uid = _user_id(client, admin_hdr, "sidorova")
    hdr = {"Authorization": f"Bearer {_token(client, 'sidorova', 'demo1234')}"}
    try:
        # по роли менеджер ставить не может
        assert _mk(client, hdr, code="РП-5", lane=2, vessel="BEFORE GRANT",
                   start="2027-03-01T00:00:00", end="2027-03-02T00:00:00").status_code == 403

        r = client.patch(f"/api/admin/users/{uid}", json={"vessel_right": "edit"}, headers=admin_hdr)
        assert r.status_code == 200, r.text
        assert r.json()["vessel_right"] == "edit"

        made = _mk(client, hdr, code="РП-5", lane=2, vessel="DISPATCHER",
                   start="2027-03-01T00:00:00", end="2027-03-02T00:00:00")
        assert made.status_code == 201, made.text
        # но удаление остаётся закрытым: «edit» не включает «delete»
        assert client.delete(f"/api/vessels/placements/{made.json()['id']}", headers=hdr).status_code == 403
    finally:
        client.patch(f"/api/admin/users/{uid}", json={"vessel_right": None}, headers=admin_hdr)

    assert client.get("/api/admin/users", headers=admin_hdr).json()  # не упало
    assert _mk(client, hdr, code="РП-5", lane=2, vessel="AFTER REVOKE",
               start="2027-04-01T00:00:00", end="2027-04-02T00:00:00").status_code == 403


def test_personal_none_closes_even_admin(client, admin_hdr):
    """Личное «нет доступа» перекрывает роль: админу закрыли план — плана нет.

    Справочник причалов — исключение: его администратор ведёт сам, иначе
    админская вкладка «Причалы» ломается из-за запрета на другой документ.
    """
    uid = _user_id(client, admin_hdr, "admin")
    try:
        assert client.patch(f"/api/admin/users/{uid}", json={"vessel_right": "none"},
                            headers=admin_hdr).status_code == 200
        assert client.get("/api/vessels/placements", params=PERIOD, headers=admin_hdr).status_code == 403
        assert client.get("/api/vessels/berths", headers=admin_hdr).status_code == 200
    finally:
        assert client.patch(f"/api/admin/users/{uid}", json={"vessel_right": None},
                            headers=admin_hdr).status_code == 200
    assert client.get("/api/vessels/placements", params=PERIOD, headers=admin_hdr).status_code == 200


def test_view_only_right(client, admin_hdr):
    uid = _user_id(client, admin_hdr, "petrov")
    hdr = {"Authorization": f"Bearer {_token(client, 'petrov', 'demo1234')}"}
    try:
        assert client.patch(f"/api/admin/users/{uid}", json={"vessel_right": "view"},
                            headers=admin_hdr).status_code == 200
        assert client.get("/api/vessels/placements", params=PERIOD, headers=hdr).status_code == 200
        assert _mk(client, hdr, code="РП-6", lane=2, vessel="READER",
                   start="2027-05-01T00:00:00", end="2027-05-02T00:00:00").status_code == 403
    finally:
        client.patch(f"/api/admin/users/{uid}", json={"vessel_right": None}, headers=admin_hdr)


def test_bad_right_rejected(client, admin_hdr):
    uid = _user_id(client, admin_hdr, "sidorova")
    r = client.patch(f"/api/admin/users/{uid}", json={"vessel_right": "superuser"}, headers=admin_hdr)
    assert r.status_code == 400 and "none, view, edit, manage" in r.json()["detail"]


def test_manage_right_includes_delete(client, admin_hdr):
    uid = _user_id(client, admin_hdr, "sidorova")
    hdr = {"Authorization": f"Bearer {_token(client, 'sidorova', 'demo1234')}"}
    try:
        assert client.patch(f"/api/admin/users/{uid}", json={"vessel_right": "manage"},
                            headers=admin_hdr).status_code == 200
        pid = _mk(client, hdr, code="РП-1", lane=1, vessel="MANAGER DELETES",
                  start="2027-06-01T00:00:00", end="2027-06-02T00:00:00").json()["id"]
        assert client.delete(f"/api/vessels/placements/{pid}", headers=hdr).status_code == 204
    finally:
        client.patch(f"/api/admin/users/{uid}", json={"vessel_right": None}, headers=admin_hdr)


def test_rights_ship_with_the_user(client, admin_hdr):
    """Вход и /me отдают посчитанные права — фронт прячет вкладку сразу, без
    отдельного запроса настроек (иначе запрещённая вкладка успевает мигнуть)."""
    body = client.post("/api/auth/login", json={"login": "petrov", "password": "demo1234"}).json()
    assert body["user"]["vessel_rights"] == {"view": True, "edit": False, "delete": False}

    hdr = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.get("/api/auth/me", headers=hdr).json()["vessel_rights"]["view"] is True

    # закрываем просмотр менеджеру — права уезжают те же, что и у API
    assert client.put("/api/admin/settings", json={"vessels.view_roles": "admin,top"},
                      headers=admin_hdr).status_code == 200
    assert client.get("/api/auth/me", headers=hdr).json()["vessel_rights"] == {
        "view": False, "edit": False, "delete": False,
    }
    assert client.get("/api/vessels/placements", params=PERIOD, headers=hdr).status_code == 403


def test_pocket_and_reberthing(client, admin_hdr):
    """«Ожидание» — карман без конфликтов; перешвартовка — копия со ссылкой на оригинал."""
    berths = client.get("/api/vessels/berths", headers=admin_hdr).json()
    park = next(b for b in berths if b["code"] == "ОЖИДАНИЕ")
    body = {"berth_id": park["id"], "vessel": "ЖДУЩИЙ", "length_m": 60, "offset_m": 0,
            "starts_at": "2026-05-04T06:00:00", "ends_at": "2026-05-05T12:00:00"}
    src = client.post("/api/vessels/placements", json=body, headers=admin_hdr)
    assert src.status_code == 201, src.text
    # в кармане суда не мешают друг другу: ни временем, ни одними и теми же метрами
    twin = client.post("/api/vessels/placements", json={**body, "vessel": "ТОЖЕ ЖДЁТ"}, headers=admin_hdr)
    assert twin.status_code == 201, twin.text

    rp1 = next(b for b in berths if b["code"] == "РП-1")
    copy = client.post("/api/vessels/placements", json={
        "berth_id": rp1["id"], "vessel": "ЖДУЩИЙ", "length_m": 60, "offset_m": 0,
        "starts_at": "2026-05-10T06:00:00", "ends_at": "2026-05-11T12:00:00",
        "moved_from_id": src.json()["id"],
    }, headers=admin_hdr)
    assert copy.status_code == 201, copy.text
    assert copy.json()["moved_from_id"] == src.json()["id"]
    # исходной постановки нет — стрелке рисовать не из чего, 400
    ghost = client.post("/api/vessels/placements", json={
        "berth_id": rp1["id"], "vessel": "ПРИЗРАК", "length_m": 40, "offset_m": 80,
        "starts_at": "2026-05-20T06:00:00", "ends_at": "2026-05-20T12:00:00",
        "moved_from_id": 999999,
    }, headers=admin_hdr)
    assert ghost.status_code == 400
    # на обычном причале правила прежние: те же слоты и метры — 409
    clash = client.post("/api/vessels/placements", json={
        "berth_id": rp1["id"], "vessel": "НАЕЗД", "length_m": 60, "offset_m": 0,
        "starts_at": "2026-05-10T06:00:00", "ends_at": "2026-05-11T12:00:00",
    }, headers=admin_hdr)
    assert clash.status_code == 409


def test_at_other_terminal_flag(client, admin_hdr):
    """Красная рамка диспетчера: судно «на другом терминале» — флаг живёт в БД,
    ставится при создании и снимается PATCH'ем (без поля — не меняется)."""
    row = _mk(client, admin_hdr, vessel="ЧУЖОЙ ТЕРМИНАЛ",
              start="2026-05-15T06:00:00", end="2026-05-16T12:00:00",
              at_other_terminal=True).json()
    assert row["at_other_terminal"] is True
    plain = _mk(client, admin_hdr, vessel="В ПРОЛИВЕ", start="2026-05-17T06:00:00",
                end="2026-05-18T12:00:00").json()
    assert plain["at_other_terminal"] is False
    patched = client.patch(f"/api/vessels/placements/{row['id']}",
                           json={"at_other_terminal": False}, headers=admin_hdr)
    assert patched.status_code == 200 and patched.json()["at_other_terminal"] is False
    untouched = client.patch(f"/api/vessels/placements/{plain['id']}",
                             json={"volume": "500"}, headers=admin_hdr)
    assert untouched.json()["at_other_terminal"] is False


def test_manual_bar_color(client, admin_hdr):
    """Цвет полосы по желанию диспетчера: ручной задаётся при создании,
    снимается PATCH'ем в null, мусор отклоняется."""
    row = _mk(client, admin_hdr, code="РП-2", vessel="ЦВЕТНОЙ",
              start="2026-05-15T06:00:00", end="2026-05-16T12:00:00", color="#A03F37").json()
    assert row["color"] == "#a03f37"  # канонический нижний регистр
    patched = client.patch(f"/api/vessels/placements/{row['id']}",
                           json={"color": None}, headers=admin_hdr)
    assert patched.json()["color"] is None
    plain = _mk(client, admin_hdr, code="РП-2", vessel="БЕЗ ЦВЕТА",
                start="2026-05-17T06:00:00", end="2026-05-18T12:00:00").json()
    assert plain["color"] is None
    bad = _mk(client, admin_hdr, code="РП-2", vessel="КРАСНЫЙ",
              start="2026-05-19T06:00:00", end="2026-05-19T12:00:00", color="красный")
    assert bad.status_code == 422


def test_scheduled_move_takes_effect_on_its_date(client, admin_hdr):
    """Перестановка по стрелке наступает сама: в дату копии судно «передвинуто» —
    копия обычная (committed), оригинал не показывают от начала копии. Данные при
    этом не меняются: редактор по-прежнему видит исходные границы."""
    orig = _mk(client, admin_hdr, code="РП-2", vessel="ХОДОК",
               start="2026-05-03T06:00:00", end="2026-05-05T12:00:00").json()
    rp3 = _berth_id(client, admin_hdr, "РП-3")
    past = client.post("/api/vessels/placements", json={
        "berth_id": rp3, "vessel": "ХОДОК", "length_m": 40, "offset_m": 0,
        "starts_at": "2026-05-04T00:00:00", "ends_at": "2026-05-06T12:00:00",
        "moved_from_id": orig["id"],
    }, headers=admin_hdr)
    assert past.status_code == 201 and past.json()["committed"] is True  # дата в прошлом
    rows = {r["id"]: r for r in client.get(
        "/api/vessels/placements?from=2026-05-01&to=2026-05-31", headers=admin_hdr
    ).json()["placements"]}
    # оригинал обрезают за слот до старта копии…
    assert rows[orig["id"]]["effective_ends_at"] == "2026-05-03T18:00:00"
    # …но хранимая дата остаётся прежней
    assert rows[orig["id"]]["ends_at"] == "2026-05-05T12:00:00"

    # прошлый период показывают «как тогда»: на 3 мая перестановка ещё не наступила —
    # оригинал не обрезан, состояние считают на последний день периода
    rows_then = {r["id"]: r for r in client.get(
        "/api/vessels/placements?from=2026-05-01&to=2026-05-03", headers=admin_hdr
    ).json()["placements"]}
    assert rows_then[orig["id"]]["effective_ends_at"] is None

    # запланированная в будущее перестановка: дата не наступила — вид прежний
    orig2 = _mk(client, admin_hdr, code="РП-4", vessel="ЖДУН",
                start="2027-11-01T06:00:00", end="2027-11-05T12:00:00").json()
    rp5 = _berth_id(client, admin_hdr, "РП-5")
    future = client.post("/api/vessels/placements", json={
        "berth_id": rp5, "vessel": "ЖДУН", "length_m": 40, "offset_m": 0,
        "starts_at": "2027-11-03T00:00:00", "ends_at": "2027-11-04T12:00:00",
        "moved_from_id": orig2["id"],
    }, headers=admin_hdr).json()
    assert future["committed"] is False
    rows2 = {r["id"]: r for r in client.get(
        "/api/vessels/placements?from=2027-11-01&to=2027-11-30", headers=admin_hdr
    ).json()["placements"]}
    assert rows2[orig2["id"]]["effective_ends_at"] is None
    assert rows2[future["id"]]["committed"] is False
    assert rows2[orig2["id"]]["effective_ends_at"] is None
    assert rows2[future["id"]]["committed"] is False


# ------------------------------------------------ PDF: стрелки перешвартовок ----


def _own_berth(client, hdr, code: str, length_m: int = 130) -> int:
    """Свой причал под PDF-тест: чужие постановки не должны попадать в лист."""
    r = client.post("/api/vessels/berths", json={"code": code, "length_m": length_m}, headers=hdr)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _clear_reberthings(client, hdr) -> None:
    """Убрать перешвартовки, оставшиеся от других тестов.

    Любая пара «чип → полоса» дотягивает свой причал в каждый PDF (см. keep
    в plan_pdf), иначе пагинация и подписи «лист N» зависели бы от того, что
    наработали предыдущие тесты.
    """
    rows = client.get("/api/vessels/placements",
                      params={"from": "2020-01-01", "to": "2035-12-31"}, headers=hdr).json()
    park_ids = {b["id"] for b in rows["berths"] if b["code"] == "ОЖИДАНИЕ"}
    for p in rows["placements"]:
        if p["berth_id"] in park_ids or p["moved_from_id"]:
            assert client.delete(f"/api/vessels/placements/{p['id']}", headers=hdr).status_code == 204


def _plan_pdf(client, hdr, berth_ids, period_from: str, period_to: str) -> bytes:
    """Скачать PDF всего периода кадра; кадр = все сутки периода."""
    r = client.get("/api/vessels/plan.pdf", params={
        "from": period_from, "to": period_to, "day_from": 0,
        "day_to": (date.fromisoformat(period_to) - date.fromisoformat(period_from)).days,
        "berth_ids": ",".join(str(i) for i in berth_ids)}, headers=hdr)
    assert r.status_code == 200, r.text
    return r.content


def _pdf_page_texts(content: bytes) -> list[str]:
    """Страницы PDF как текст: стрелки и подписи проверяются извлечением, не байтами."""
    import io

    from pypdf import PdfReader
    return [p.extract_text() or "" for p in PdfReader(io.BytesIO(content)).pages]


def _pdf_label_positions(content: bytes) -> dict[str, tuple[int, float, float]]:
    """Координаты подписей полос в PDF: (лист, x, y) — по ним видно, куда встали полосы."""
    import io

    from pypdf import PdfReader
    found: dict[str, tuple[int, float, float]] = {}
    for n, page in enumerate(PdfReader(io.BytesIO(content)).pages):
        def cb(text, cm, tm, fd, fs, n=n):
            t = text.strip()
            if t.startswith("ЛОЖЕНИЕ"):
                found[t] = (n, round(tm[4], 1), round(tm[5], 1))
        page.extract_text(visitor_text=cb)
    return found


def test_plan_pdf_reberthing_across_sheets(client, admin_hdr):
    """Перешвартовка не должна исчезать, когда причалы разъехались по листам.

    Шесть причалов по 130 м: на первый лист лезут четыре, чип «Ожидания»
    внизу не влезает и уезжает на лист 2, остальные причалы — на лист 3.
    Цепочка АРВ-1 (лист 1) → АРВ-6 (лист 3) → чип «Ожидания» → АРВ-3 (лист 1):
    на листе начала остаётся стрелка в нижний край блока с подписью «лист 3»,
    на листе цели стрелка входит в полосу со стороны шапки с подписью «лист 1»,
    а у каждой стрелки — свой номер шага цепочки.
    """
    _clear_reberthings(client, admin_hdr)
    bids = [_own_berth(client, admin_hdr, f"АРВ-{i}") for i in range(1, 7)]
    made: list[int] = []

    def mk(berth_id, vessel, start, end, **kw):
        r = client.post("/api/vessels/placements", json={
            "berth_id": berth_id, "vessel": vessel, "length_m": 60, "offset_m": 0,
            "starts_at": start, "ends_at": end, **kw}, headers=admin_hdr)
        assert r.status_code == 201, r.text
        made.append(r.json()["id"])
        return r.json()["id"]

    try:
        park_id = next(b["id"] for b in client.get("/api/vessels/berths", headers=admin_hdr).json()
                       if b["code"] == "ОЖИДАНИЕ")
        a = mk(bids[0], "ШАГ ОДИН", "2028-01-05T00:00:00", "2028-01-07T12:00:00")
        b2 = mk(bids[5], "ШАГ ДВА", "2028-01-09T00:00:00", "2028-01-11T12:00:00", moved_from_id=a)
        c3 = mk(park_id, "ШАГ ТРИ", "2028-01-12T00:00:00", "2028-01-14T12:00:00",
                moved_from_id=b2, at_other_terminal=True)
        mk(bids[2], "ШАГ ЧЕТЫРЕ", "2028-01-16T00:00:00", "2028-01-18T12:00:00", moved_from_id=c3)

        pages = _pdf_page_texts(_plan_pdf(client, admin_hdr, bids, "2028-01-01", "2028-01-31"))
        assert len(pages) == 3, f"ждём три листа (4 причала + чип + 2), а их {len(pages)}"
        # лист 1: оригинал уходит на лист 3; копия из чипа приходит с листа 2
        assert "шаг 1→2 → АРВ-6 · лист 3" in pages[0], pages[0][:400]
        assert "шаг 3→4 от Ожидание · лист 2" in pages[0]
        # лист 3: стрелка входит в полосу со стороны шапки + стрелка в чип этого же листа
        assert "шаг 1→2 от АРВ-1 · лист 1" in pages[2], pages[2][:400]
        assert "шаг 2→3" in pages[2], "стрелка полоса → чип не рисуется вовсе"
        # вся цепочка подписана шагами — на резаном по причалам листу иначе не свести
        for tag in ("шаг 1→2", "шаг 2→3", "шаг 3→4"):
            assert any(tag in t for t in pages), f"у цепочки потерялась подпись {tag}"
    finally:
        for pid in made:
            client.delete(f"/api/vessels/placements/{pid}", headers=admin_hdr)
        for bid in bids:
            client.delete(f"/api/vessels/berths/{bid}", headers=admin_hdr)


def test_plan_pdf_bar_to_waiting_chip(client, admin_hdr):
    """Перешвартовка в «Ожидание» обязана быть видна на бумаге: полоса → чип.

    Раньше рисовалась только обратная стрелка (чип → полоса), и постановка
    судна в карман на распечатке выглядела исчезновением. Теперь направление
    как на экране — от оригинала к копии, с подписью шага.
    """
    _clear_reberthings(client, admin_hdr)
    own = _own_berth(client, admin_hdr, "ЧИП-1", 100)
    made: list[int] = []
    try:
        park_id = next(b["id"] for b in client.get("/api/vessels/berths", headers=admin_hdr).json()
                       if b["code"] == "ОЖИДАНИЕ")
        src = client.post("/api/vessels/placements", json={
            "berth_id": own, "vessel": "ИСХОДНИК", "length_m": 60, "offset_m": 0,
            "starts_at": "2029-02-02T00:00:00", "ends_at": "2029-02-03T12:00:00"},
            headers=admin_hdr).json()["id"]
        made.append(src)
        chip = client.post("/api/vessels/placements", json={
            "berth_id": park_id, "vessel": "ПОСЕЛИНЕЦ", "length_m": 60, "offset_m": 0,
            "starts_at": "2029-02-05T00:00:00", "ends_at": "2029-02-06T12:00:00",
            "moved_from_id": src}, headers=admin_hdr)
        assert chip.status_code == 201, chip.text
        made.append(chip.json()["id"])

        pages = _pdf_page_texts(_plan_pdf(client, admin_hdr, [own], "2029-02-01", "2029-02-07"))
        assert len(pages) == 1
        assert "ПОСЕЛИНЕЦ" in pages[0], "чип не напечатан"
        assert "шаг 1→2" in pages[0], "стрелка полоса → чип с подписью шага не появилась"
    finally:
        for pid in made:
            client.delete(f"/api/vessels/placements/{pid}", headers=admin_hdr)
        client.delete(f"/api/vessels/berths/{own}", headers=admin_hdr)


def test_plan_pdf_layout_coordinates_frozen(client, admin_hdr):
    """Геометрия листа заморожена: стрелки появились, полосы — не сдвинулись.

    Восемь причалов по 140 м: cell_h = 6 pt независимо от чипов «Ожидания»
    (сетка всё равно не влезает), на лист встаёт по три блока, чип всегда
    помещается внизу — значит координаты не зависят от того, что осталось
    в кармане после других тестов. Значения зафиксированы с версии кода
    до двухпроходной раскладки: после правок они обязаны совпасть.
    """
    _clear_reberthings(client, admin_hdr)
    bids = [_own_berth(client, admin_hdr, f"ЛАЙ-{i}", 140) for i in range(1, 9)]
    made: list[int] = []
    try:
        for bid, vessel, off in ((bids[0], "ЛОЖЕНИЕ А", 0), (bids[3], "ЛОЖЕНИЕ Б", 20),
                                 (bids[7], "ЛОЖЕНИЕ В", 40)):
            r = client.post("/api/vessels/placements", json={
                "berth_id": bid, "vessel": vessel, "length_m": 60, "offset_m": off,
                "starts_at": "2028-03-02T00:00:00", "ends_at": "2028-03-04T12:00:00"},
                headers=admin_hdr)
            assert r.status_code == 201, r.text
            made.append(r.json()["id"])
        content = _plan_pdf(client, admin_hdr, bids, "2028-03-01", "2028-03-07")
        texts = _pdf_page_texts(content)
        assert len(texts) == 3, f"число листов поехало: {len(texts)} вместо 3"
        assert _pdf_label_positions(content) == {
            "ЛОЖЕНИЕ А": (0, 222.1, 493.1),   # первый блок первого листа
            "ЛОЖЕНИЕ Б": (1, 222.1, 481.1),   # первый блок второго листа, отм. 20 м
            "ЛОЖЕНИЕ В": (2, 222.1, 337.1),   # второй блок третьего листа, отм. 40 м
        }
    finally:
        for pid in made:
            client.delete(f"/api/vessels/placements/{pid}", headers=admin_hdr)
        for bid in bids:
            client.delete(f"/api/vessels/berths/{bid}", headers=admin_hdr)
