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

    Шапка суток теперь одна на лист (а не над каждым причалом), поэтому на
    каждом листе она обязана оказаться ровно один раз — и ни на одном листе
    она не имеет права исчезнуть.
    """
    made = []
    # двенадцать причалов по 130 м — план, который не влезает даже при поле
    # шкалы: именно он и обязан распасться на листы
    for i in range(12):
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
    assert len(pages) > 1, f"двенадцать причалов по 130 м должны не на один лист, а их {len(pages)}"
    for n, page in enumerate(pages, start=1):
        text = page.extract_text() or ""
        assert "План постановки судов" in text, f"лист {n} без шапки"
        assert "Пунктир" in text, f"лист {n} без сноски"
        assert text.count("причал / метры") == 1, f"лист {n} без шапки суток (или их несколько)"

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


def _plan_pdf(client, hdr, berth_ids, period_from: str, period_to: str,
              day_from: int = 0, day_to: int | None = None) -> bytes:
    """Скачать PDF кадра: по умолчанию — все сутки периода, иначе [day_from, day_to]."""
    if day_to is None:
        day_to = (date.fromisoformat(period_to) - date.fromisoformat(period_from)).days
    r = client.get("/api/vessels/plan.pdf", params={
        "from": period_from, "to": period_to, "day_from": day_from,
        "day_to": day_to,
        "berth_ids": ",".join(str(i) for i in berth_ids)}, headers=hdr)
    assert r.status_code == 200, r.text
    return r.content


def _chain(client, hdr, berth_ids, start: str, *, gap_days: int = 4) -> list[int]:
    """Цепочка перешвартовок «по одному причалу»: id постановок в порядке шагов."""
    made: list[int] = []
    src: int | None = None
    for i, bid in enumerate(berth_ids):
        day = date.fromisoformat(start) + timedelta(days=i * gap_days)
        body = {
            "berth_id": bid, "vessel": f"ЦЕПОЧКА {i + 1}", "length_m": 60, "offset_m": 0,
            "starts_at": f"{day.isoformat()}T00:00:00",
            "ends_at": f"{(day + timedelta(days=2)).isoformat()}T12:00:00",
        }
        if src:
            body["moved_from_id"] = src
        r = client.post("/api/vessels/placements", json=body, headers=hdr)
        assert r.status_code == 201, r.text
        made.append(r.json()["id"])
        src = r.json()["id"]
    return made


def _drop(client, hdr, placement_ids, berth_ids) -> None:
    """Убрать за тестом: чужие стрелки меняют и замыкание фильтра, и пагинацию."""
    for pid in placement_ids:
        client.delete(f"/api/vessels/placements/{pid}", headers=hdr)
    for bid in berth_ids:
        client.delete(f"/api/vessels/berths/{bid}", headers=hdr)


def _pdf_page_texts(content: bytes) -> list[str]:
    """Страницы PDF как текст: стрелки и подписи проверяются извлечением, не байтами."""
    import io

    from pypdf import PdfReader
    return [p.extract_text() or "" for p in PdfReader(io.BytesIO(content)).pages]


def _pdf_label_positions(content: bytes, prefix: str = "ЛОЖЕНИЕ") -> dict[str, tuple[int, float, float]]:
    """Координаты подписей в PDF: (лист, x, y) — по ним видно, куда встали полосы.

    Префикс — по какому слову собирать подписи: у теста про огрызки это «шаг», у
    теста про геометрию листа — шифр полосы. Координаты берутся из текстовой
    матрицы самого PDF, то есть проверяется факт расстановки, а не намерение.
    """
    import io

    from pypdf import PdfReader
    found: dict[str, tuple[int, float, float]] = {}
    for n, page in enumerate(PdfReader(io.BytesIO(content)).pages):
        def cb(text, cm, tm, fd, fs, n=n):
            t = text.strip()
            if t.startswith(prefix):
                found[t] = (n, round(tm[4], 1), round(tm[5], 1))
        page.extract_text(visitor_text=cb)
    return found


def _pdf_strokes(content: bytes) -> list[tuple[int, float, float, float, float]]:
    """Отрезки, напечатанные в PDF: (лист, x0, y0, x1, y1) — куда по листу ходят линии.

    Reportlab пишет каждую линию как «x0 y0 m x1 y1 l S», поэтому достаточно
    пройти по операторному потоку и собрать пары m→l: стороны прямоугольников
    (`re`) и скруглённые углы с кружками (кривые Безье) в такие отрезки не
    попадают, а настоящие линии — попадают. Нужны они затем, что подпись
    подписью, а линия через шесть блоков причалов — это испорченный лист.
    """
    import io
    import re

    from pypdf import PdfReader
    segs: list[tuple[int, float, float, float, float]] = []
    for n, page in enumerate(PdfReader(io.BytesIO(content)).pages):
        data = page.get_contents().get_data().decode("latin-1")
        cur: tuple[float, float] | None = None
        for m in re.finditer(r"(-?[\d.]+) (-?[\d.]+) (m|l)\b", data):
            x, y = float(m.group(1)), float(m.group(2))
            if m.group(3) == "m":
                cur = (x, y)
            elif cur is not None:
                segs.append((n, cur[0], cur[1], x, y))
    return segs


def _page_of(pages: list[str], needle: str) -> int:
    """Лист, на котором напечатана подпись (шифр судна или причала).

    Номер листа берётся из самого рендера: подписи «лист N» проверяются против
    факта, а не против числа, которое кто-то посчитал руками на глаз.
    """
    hits = [n for n, t in enumerate(pages) if needle in t]
    assert hits, f"«{needle}» не найдено ни на одном листе"
    return hits[0]


def test_plan_pdf_reberthing_across_sheets(client, admin_hdr):
    """Перешвартовка не должна исчезать, когда причалы разъехались по листам.

    План заведомо крупнее листа: восемь причалов по 200 м даже при поле шкалы
    в один лист не просятся. Цепочка АРВ-1 → АРВ-6 → чип «Ожидания» → АРВ-3
    лежит на разных листах, а номер листа в подписи берётся из самого рендера:
    тест проверяет, что огрызок смотрит туда, где конец связи напечатан, а не
    туда, куда удобно раскладке.
    """
    _clear_reberthings(client, admin_hdr)
    bids = [_own_berth(client, admin_hdr, f"АРВ-{i}", 200) for i in range(1, 9)]
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
        assert len(pages) > 1, f"причалы обязаны не влезть в один лист, а их {len(pages)}"
        p1 = _page_of(pages, "ШАГ ОДИН")     # лист оригинала (АРВ-1)
        p6 = _page_of(pages, "ШАГ ДВА")      # лист копии (АРВ-6) — другой лист
        p3 = _page_of(pages, "ШАГ ЧЕТЫРЕ")   # лист полосы после «Ожидания» (АРВ-3)
        assert p1 != p6, "причалы цепочки должны разъехаться по листам — иначе тест ни о чём"
        # лист начала: стрелка уходит в нижний край блока и знает номер листа цели
        assert f"шаг 1→2 → АРВ-6 · лист {p6 + 1}" in pages[p1], pages[p1][:400]
        # лист цели: стрелка входит в полосу со стороны шапки и знает номер начала
        assert f"шаг 1→2 от АРВ-1 · лист {p1 + 1}" in pages[p6], pages[p6][:400]
        # чип «Ожидания» печатается внизу листа: если полоса и его чип лежат на
        # одном листе, связь рисуется ЦЕЛОЙ линией, а если чипа на этом листе
        # нет — огрызком «· лист N». Подписаны оба случая одинаково, поэтому
        # здесь проверяется сам шаг, а не хвост подписи.
        assert "шаг 2→3" in pages[p6], "связь полосы с чипом «Ожидания» не рисуется вовсе"
        assert "шаг 3→4" in pages[p3], "связь чипа с полосой не рисуется вовсе"
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
    как на экране — от оригинала к копии, и рисуется оно настоящей линией:
    оба конца напечатаны на этом же листе, значит огрызкам тут нечего делать,
    а подпись не несёт ничего, кроме «шаг N→M».
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
        assert "шаг 1→2" in pages[0], "связь полосы с её чипом не появилась"
        assert "→ Ожидание" not in pages[0], "у полосы вернулся огрызок в чип"
        assert "от ЧИП-1" not in pages[0], "огрызок чипа от причала вернулся"
    finally:
        for pid in made:
            client.delete(f"/api/vessels/placements/{pid}", headers=admin_hdr)
        client.delete(f"/api/vessels/berths/{own}", headers=admin_hdr)


def test_plan_pdf_bar_and_its_waiting_chip_are_tied_by_one_line(client, admin_hdr):
    """Полоса и её чип «Ожидания» на одном листе связаны ОДНОЙ линией.

    Кадр, из-за которого правка и началась: полоса стоит в блоке причала, а её
    копия лежит в чипе «Ожидания» внизу того же листа. Три правки подряд рисовали
    здесь по два огрызка — «шаг 1→2 → Ожидание» у полосы и «шаг 1→2 от ОГРЫ-1» у
    чипа, — и читатель должен был сам догадываться, какой штрих к какой линии
    относится. На экране же стоит настоящая прямая из А в Б; теперь она и на
    бумаге: один отрезок от нижней грани полосы до верхней грани чипа и подпись
    просто «шаг 1→2». Что он идёт через блоки причалов — сознательно: ровно так
    же он выглядит на сетке. Проверяется не одними подписями: координаты концов
    берутся из разбора штрихов самого PDF.
    """
    _clear_reberthings(client, admin_hdr)
    bids = [_own_berth(client, admin_hdr, f"ОГРЫ-{i}", 100) for i in range(1, 4)]
    made: list[int] = []
    try:
        park_id = next(b["id"] for b in client.get("/api/vessels/berths", headers=admin_hdr).json()
                       if b["code"] == "ОЖИДАНИЕ")
        src = client.post("/api/vessels/placements", json={
            "berth_id": bids[0], "vessel": "УХОДИТ", "length_m": 60, "offset_m": 0,
            "starts_at": "2033-03-02T00:00:00", "ends_at": "2033-03-03T12:00:00"},
            headers=admin_hdr).json()["id"]
        made.append(src)
        chip = client.post("/api/vessels/placements", json={
            "berth_id": park_id, "vessel": "ЖДЁТ", "length_m": 60, "offset_m": 0,
            "starts_at": "2033-03-05T00:00:00", "ends_at": "2033-03-06T12:00:00",
            "moved_from_id": src}, headers=admin_hdr)
        assert chip.status_code == 201, chip.text
        made.append(chip.json()["id"])

        content = _plan_pdf(client, admin_hdr, bids, "2033-03-01", "2033-03-07")
        pages = _pdf_page_texts(content)
        assert len(pages) == 1, f"три причала по 100 м должны влезть в один лист: {len(pages)}"
        text = pages[0]
        assert "ЖДЁТ" in text, "чип не напечатан"
        assert "шаг 1→2" in text, "у связи не осталось подписи"
        # оба конца напечатаны на этом листе — слов к подписи не нужно
        assert "→ Ожидание" not in text, f"у полосы вернулся огрызок в чип: {text[:400]}"
        assert "от ОГРЫ-1" not in text, f"у чипа вернулся огрызок от причала: {text[:400]}"

        pos = _pdf_label_positions(content, "шаг 1→2")
        assert set(pos) == {"шаг 1→2"}, f"одной связи положена одна подпись: {pos}"
        label = pos["шаг 1→2"]
        ship = _pdf_label_positions(content, "УХОДИТ")["УХОДИТ"]
        chip_txt = _pdf_label_positions(content, "ЖДЁТ")["ЖДЁТ"]
        assert label[0] == 0, "подпись уехала с листа своей связи"

        strokes = _pdf_strokes(content)

        def bottom_edge(at):
            """Нижняя грань скруглённого прямоугольника (полосы или чипа).

            Reportlab начинает путь скруглённого прямоугольника с её нижней
            грани, поэтому в потоке это горизонтальный отрезок, который стартует
            на 1 pt левее подписи внутри него (подпись смещена на 3 pt, угол
            скруглён на 2) и подниматься ниже подписи не может.
            """
            hits = [s for s in strokes if s[0] == 0 and abs(s[2] - s[4]) < 0.05
                    and abs(s[1] - (at[1] - 1)) < 0.5 and s[2] < at[2]]
            assert len(hits) == 1, f"нижняя грань для {at} не найдена: {hits}"
            return hits[0]

        bar = bottom_edge(ship)
        chip = bottom_edge(chip_txt)
        bar_y, bar_left, bar_right = bar[2], bar[1] - 2, bar[3] + 2
        chip_top = chip_txt[2] + 6.5  # baseline подписи чипа → его верхняя грань
        chip_cx = (chip[1] + chip[3]) / 2

        # Одно целое соединение полосы и её чипа: старт — ровно нижняя грань
        # полосы, конец — верхняя грань чипа (до неё линия не доезжает 5.5 pt,
        # которые arrow_straight оставляет под остриём).
        line = [s for s in strokes if s[0] == 0
                and abs(max(s[2], s[4]) - bar_y) <= 0.6
                and chip_top - 0.6 <= min(s[2], s[4]) <= chip_top + 6.5]
        assert len(line) == 1, f"полосу и её чип должна соединять одна линия: {line}"
        seg = line[0]
        x_bar = seg[1] if seg[2] > seg[4] else seg[3]
        assert bar_left + 3 - 0.7 <= x_bar <= bar_right - 3 + 0.7, \
            f"линия стартовала не с ближайшего к чипу торца: {x_bar} " \
            f"при полосе [{bar_left:.1f}, {bar_right:.1f}]"
        # остриё вошло ровно в верхнюю грань чипа: это два пера наконечника,
        # выходящие из самой точки острия
        head = [s for s in strokes if s[0] == 0 and abs(s[1] - chip_cx) <= 0.6
                and abs(s[2] - chip_top) <= 0.6 and abs(s[4] - chip_top) > 0.3]
        assert len(head) == 2, f"остриё не в верхней грани чипа: {head}"

        # подпись — у чипа, над его верхней гранью, и никогда на полосе
        assert chip_top < label[2] < bar_y, \
            f"подпись ушла от чипа: {label[2]} при чипе {chip_top} и полосе {bar_y}"
        assert chip[1] - 2 <= label[1] <= chip[3] + 2, "подпись не над своим чипом"

        # инверсия старой проверки: от блока причала к чипу теперь обязан идти
        # ровно один отрезок — та самая линия, которой здесь «не должно быть»
        across = [s for s in strokes if s[0] == 0
                  and min(s[2], s[4]) < chip_top + 8  # 5.5 pt под остриём не доезжают
                  and max(s[2], s[4]) > chip_top + 20]
        assert len(across) == 1, f"к чипу ведёт не одна линия: {across}"
    finally:
        _drop(client, admin_hdr, made, bids)


def test_plan_pdf_chip_line_label_jumps_over_the_bar_under_it(client, admin_hdr):
    """Подпись линии в чип не имеет права садиться на полосу с подписью судна.

    Линия «полоса ↔ чип» подписывается над самим чипом, но верхняя грань чипа
    первой строки лежит впритык к последнему блоку причалов, и если у его подошвы
    стоит полоса, подпись легла бы ровно на шифр судна. Здесь ровно такой кадр:
    полоса прижата к концу причала (низ поля) и накрывает чип по горизонтали.
    Гарантия одна: подпись уходит над верхней гранью полосы, а не ложится на неё.
    """
    _clear_reberthings(client, admin_hdr)
    own = _own_berth(client, admin_hdr, "НИЖНЯЯ", 100)
    made: list[int] = []
    try:
        park_id = next(b["id"] for b in client.get("/api/vessels/berths", headers=admin_hdr).json()
                       if b["code"] == "ОЖИДАНИЕ")
        src = client.post("/api/vessels/placements", json={
            "berth_id": own, "vessel": "У САМОЙ ЗЕМЛИ", "length_m": 60, "offset_m": 40,
            "starts_at": "2035-06-01T00:00:00", "ends_at": "2035-06-04T12:00:00"},
            headers=admin_hdr).json()["id"]
        made.append(src)
        chip = client.post("/api/vessels/placements", json={
            "berth_id": park_id, "vessel": "НИЖНИЙ ЧИП", "length_m": 60, "offset_m": 0,
            "starts_at": "2035-06-05T00:00:00", "ends_at": "2035-06-06T12:00:00",
            "moved_from_id": src}, headers=admin_hdr)
        assert chip.status_code == 201, chip.text
        made.append(chip.json()["id"])

        content = _plan_pdf(client, admin_hdr, [own], "2035-06-01", "2035-06-07")
        pages = _pdf_page_texts(content)
        assert len(pages) == 1, f"один причал обязан влезть в один лист: {len(pages)}"
        assert "шаг 1→2" in pages[0], "связь полосы с её чипом не появилась"
        label = _pdf_label_positions(content, "шаг 1→2")["шаг 1→2"]
        ship = _pdf_label_positions(content, "У САМОЙ ЗЕМЛИ")["У САМОЙ ЗЕМЛИ"]
        chip_txt = _pdf_label_positions(content, "НИЖНИЙ ЧИП")["НИЖНИЙ ЧИП"]
        # полоса этого кадра реально нависает над местом подписи: проверка не пустая
        assert chip_txt[2] < ship[2], "полоса не оказалась между чипом и его подписью"
        assert label[2] > ship[2], \
            f"подпись легла на подпись судна: {label[2]} против {ship[2]}"
    finally:
        _drop(client, admin_hdr, made, [own])


def test_plan_pdf_arrow_label_jumps_over_the_bar_it_hits(client, admin_hdr):
    """Подпись стрелки не имеет права ложиться на полосу, в которую уперлась.

    Тот же кадр: подпись «шаг 2→3» легла поверх «2 400 т · 74 м» того же судна и
    читалась с трудом — у полосы-цели слева от наконечника не осталось места до
    края поля шкалы. Правило: места нет — подпись идёт над верхней гранью полосы.
    Проверка парная: там, где место слева есть, подпись обязана остаться у
    наконечника, иначе правило превратилось бы в «всегда рисуй сверху».
    """
    _clear_reberthings(client, admin_hdr)
    bids = [_own_berth(client, admin_hdr, f"МЕСТО-{i}", 100) for i in range(1, 3)]
    made: list[int] = []
    try:
        src = client.post("/api/vessels/placements", json={
            "berth_id": bids[0], "vessel": "ОРИГИНАЛ", "length_m": 60, "offset_m": 0,
            "starts_at": "2034-04-01T00:00:00", "ends_at": "2034-04-10T12:00:00"},
            headers=admin_hdr).json()["id"]
        copy = client.post("/api/vessels/placements", json={
            "berth_id": bids[1], "vessel": "КОПИЯ", "length_m": 60, "offset_m": 0,
            "starts_at": "2034-04-05T00:00:00", "ends_at": "2034-04-08T12:00:00",
            "moved_from_id": src}, headers=admin_hdr)
        assert copy.status_code == 201, copy.text
        made = [src, copy.json()["id"]]

        # кадр с самого начала периода: до левого края поля у копии место есть,
        # и подпись обязана остаться у наконечника — ниже своей подписи судна
        wide_pdf = _plan_pdf(client, admin_hdr, bids, "2034-04-01", "2034-04-30")
        wide = _pdf_label_positions(wide_pdf, "шаг 1→2")["шаг 1→2"]
        ship = _pdf_label_positions(wide_pdf, "КОПИЯ")["КОПИЯ"]
        assert wide[2] < ship[2], "подпись прыгнула над полосой там, где места хватало"

        # кадр, который начинается с постановки копии: её левый торец — это левый
        # край поля, слева от наконечника места нет вовсе, и подпись уходит НАД
        tight = _plan_pdf(client, admin_hdr, bids, "2034-04-01", "2034-04-30",
                          day_from=4, day_to=29)
        pos = _pdf_label_positions(tight, "шаг 1→2")["шаг 1→2"]
        ship2 = _pdf_label_positions(tight, "КОПИЯ")["КОПИЯ"]
        assert pos[2] > ship2[2], "подпись легла на полосу вместо места над ней"
        field_left = (8 + 34) * 72 / 25.4  # поле листа + колонка причала
        assert pos[1] >= field_left - 0.5, "подпись вылезла за левый край поля шкалы"
    finally:
        _drop(client, admin_hdr, made, bids)


def test_plan_pdf_moved_vessel_leaves_waiting_list(client, admin_hdr):
    """Окно, захватившее плановое перемещение, снимает судно с очереди.

    Кадр пользователя: он сдвинул окно на конец стоянки, и под причалами всё
    ещё висел чип «ждёт РП-1» с двумя стрелками — хотя перемещение на РП-1 уже
    началось и соответствующая полоса на листе была. «Ожидание» — это очередь
    на конец печатаемых суток: получил причал — вышел из списка, вместе с обеими
    своими стрелками.
    """
    _clear_reberthings(client, admin_hdr)
    a_b = _own_berth(client, admin_hdr, "ОЧР-1", 100)
    b_b = _own_berth(client, admin_hdr, "ОЧР-2", 100)
    park = next(x["id"] for x in client.get("/api/vessels/berths", headers=admin_hdr).json()
                if x["code"] == "ОЖИДАНИЕ")
    made: list[int] = []

    def mk(berth_id, start, end, **kw):
        r = client.post("/api/vessels/placements", json={
            "berth_id": berth_id, "vessel": "ПЕРЕХОД", "length_m": 60, "offset_m": 0,
            "starts_at": start, "ends_at": end, **kw}, headers=admin_hdr)
        assert r.status_code == 201, r.text
        made.append(r.json()["id"])
        return r.json()["id"]

    try:
        first = mk(a_b, "2032-03-01T00:00:00", "2032-03-03T12:00:00")
        wait = mk(park, "2032-03-04T00:00:00", "2032-03-05T12:00:00",
                  moved_from_id=first, at_other_terminal=True)
        mk(b_b, "2032-03-06T00:00:00", "2032-03-09T12:00:00", moved_from_id=wait)

        # окно до перемещения — судно ждёт: чип и обе его стрелки на бумаге
        early = " ".join(_pdf_page_texts(_plan_pdf(
            client, admin_hdr, [a_b, b_b], "2032-03-01", "2032-03-05")))
        assert "Ожидание — ждут череди" in early, early[:400]
        assert "шаг 1→2" in early and "ПЕРЕХОД" in early, early[:400]

        # окно после начала перемещения — судно свой причал уже получило
        late = " ".join(_pdf_page_texts(_plan_pdf(
            client, admin_hdr, [a_b, b_b], "2032-03-08", "2032-03-12")))
        assert "ПЕРЕХОД" in late, "сама полоса никуда не делась — проверка не пустая"
        assert "Ожидание" not in late, f"судно получилo причал и всё ещё в списке: {late[:400]}"
        assert "шаг" not in late, f"перемещение состоялось, а стрелка осталась: {late[:400]}"

        # окно, которое накрывает всю цепочку, обязано оставить и чип, и обе
        # стрелки: здесь перемещение ещё только предстоит первому дню кадра,
        # и обрезка по последним суткам съела бы всю перешвартовку целиком
        whole = " ".join(_pdf_page_texts(_plan_pdf(
            client, admin_hdr, [a_b, b_b], "2032-03-01", "2032-03-12")))
        assert "Ожидание — ждут череди" in whole, f"цепочка целиком в кадре, а чипа нет: {whole[:400]}"
        for tag in ("шаг 1→2", "шаг 2→3"):
            assert tag in whole, f"у цепочки в кадре потерялась подпись {tag}: {whole[:400]}"
    finally:
        _drop(client, admin_hdr, made, [a_b, b_b])


def test_plan_pdf_typical_window_fits_one_sheet(client, admin_hdr):
    """Типовое окно плана — шесть причалов и 19 суток — обязано влезть в один лист.

    Ради этого лист и сжали по высоте: пока план резался на листы, каждая
    стрелка через границу превращалась в огрызок «лист N», и диспетчер сводил
    листы руками. Здесь ровно тот кадр, из-за которого правка и началась:
    шесть причалов, девятнадцать суток, чип «Ожидания» и цепочка из пяти
    постановок — и ни одной границы листа.
    """
    _clear_reberthings(client, admin_hdr)
    bids = [_own_berth(client, admin_hdr, f"ТЕСН-{i}", 110) for i in range(1, 7)]
    made: list[int] = []

    def mk(berth_id, vessel, start, end, **kw):
        r = client.post("/api/vessels/placements", json={
            "berth_id": berth_id, "vessel": vessel, "length_m": 80, "offset_m": 0,
            "starts_at": start, "ends_at": end, **kw}, headers=admin_hdr)
        assert r.status_code == 201, r.text
        made.append(r.json()["id"])
        return r.json()["id"]

    try:
        park_id = next(b["id"] for b in client.get("/api/vessels/berths", headers=admin_hdr).json()
                       if b["code"] == "ОЖИДАНИЕ")
        a = mk(bids[0], "ТЕСН ОДИН", "2031-05-02T00:00:00", "2031-05-04T12:00:00")
        b2 = mk(bids[4], "ТЕСН ДВА", "2031-05-06T00:00:00", "2031-05-08T12:00:00", moved_from_id=a)
        c3 = mk(park_id, "ТЕСН ТРИ", "2031-05-09T00:00:00", "2031-05-11T12:00:00",
                moved_from_id=b2, at_other_terminal=True)
        d4 = mk(bids[2], "ТЕСН ЧЕТЫРЕ", "2031-05-12T00:00:00", "2031-05-14T12:00:00",
                moved_from_id=c3)
        mk(bids[1], "ТЕСН ПЯТЬ", "2031-05-15T00:00:00", "2031-05-17T12:00:00", moved_from_id=d4)

        pages = _pdf_page_texts(_plan_pdf(client, admin_hdr, bids, "2031-05-01", "2031-05-19"))
        assert len(pages) == 1, f"шесть причалов должны влезть в один лист, а их {len(pages)}"
        text = pages[0]
        assert "План постановки судов" in text and "Пунктир" in text
        assert text.count("причал / метры") == 1, "шапка суток должна быть одна на лист"
        assert "Ожидание — ждут череди" in text, "чипов «Ожидания» не стало"
        for tag in ("шаг 1→2", "шаг 2→3", "шаг 3→4", "шаг 4→5"):
            assert tag in text, f"на листе не осталось подписи {tag}: {text[:300]}"
        # план на одном листе — значит ни одна стрелка не имеет права смотреть «за лист»
        # (сама сноска слова «лист N» и «вне кадра» упоминает всегда — ищем огрызки)
        assert "· лист" not in text and "· вне кадра" not in text, text[:400]
    finally:
        _drop(client, admin_hdr, made, bids)


def test_plan_pdf_overflow_still_splits_sheets(client, admin_hdr):
    """Невмещающийся план обязан распадаться на листы и не терять стрелки.

    Десять причалов по 220 м — больше, чем лист вмещает даже при поле шкалы, и
    компактность не имеет права отнимать fallback: план уезжает на второй лист,
    а стрелка через границу оставляет огрызки с номером листа с обеих сторон —
    ровно как печаталась до сжатия.
    """
    _clear_reberthings(client, admin_hdr)
    bids = [_own_berth(client, admin_hdr, f"МНОГО-{i:02d}", 220) for i in range(1, 11)]
    made = _chain(client, admin_hdr, [bids[0], bids[-1]], "2032-04-02", gap_days=6)
    try:
        pages = _pdf_page_texts(_plan_pdf(client, admin_hdr, bids, "2032-04-01", "2032-04-30"))
        assert len(pages) > 1, "план из десяти причалов по 220 м не должен влезать в один лист"
        src_page = _page_of(pages, "ЦЕПОЧКА 1")
        dst_page = _page_of(pages, "ЦЕПОЧКА 2")
        assert src_page != dst_page, "причалы цепочки должны разъехаться по листам"
        assert f"шаг 1→2 → МНОГО-10 · лист {dst_page + 1}" in pages[src_page], pages[src_page][:400]
        assert f"шаг 1→2 от МНОГО-01 · лист {src_page + 1}" in pages[dst_page], pages[dst_page][:400]
    finally:
        _drop(client, admin_hdr, made, bids)


def test_plan_pdf_step_numbers_survive_the_filter(client, admin_hdr):
    """Номера шагов на бумаге — те же, что на экране, при любом фильтре причалов.

    Экран нумерует цепочку целиком, а лист печатает то, что в кадре. Если номера
    считать по тому, что влезло в лист, копия без оригинала становится корнем, и
    там, где экран пишет «шаг 3→4», бумага пишет «шаг 2→3». Номер — опознавательный
    знак стрелки: по нему распечатку сводят с экраном и между листами.
    """
    import re

    _clear_reberthings(client, admin_hdr)
    bids = [_own_berth(client, admin_hdr, f"НОМ-{i}") for i in range(1, 5)]
    made: list[int] = []
    try:
        made = _chain(client, admin_hdr, bids, "2030-06-02")
        # просим только хвостовой причал: без замыкания в лист попал бы он один
        pages = _pdf_page_texts(
            _plan_pdf(client, admin_hdr, bids[-1:], "2030-06-01", "2030-06-30"))
        pairs = {tuple(map(int, m)) for m in re.findall(r"шаг\s*(\d+)→(\d+)", " ".join(pages))}
        assert pairs == {(1, 2), (2, 3), (3, 4)}, f"цепочка перенумеровалась: {sorted(pairs)}"
    finally:
        _drop(client, admin_hdr, made, bids)


def test_plan_pdf_berth_filter_closes_the_whole_chain(client, admin_hdr):
    """Фильтр причалов обязан замыкаться вдоль цепочки в обе стороны и до упора.

    Просили один причал из середины цепочки из четырёх: лист печатает все четыре,
    иначе половина связи повисает без второй — при однопроходном замыкании причал
    самого дальнего оригинала терялся.
    """
    _clear_reberthings(client, admin_hdr)
    bids = [_own_berth(client, admin_hdr, f"ЗАМ-{i}") for i in range(1, 5)]
    made: list[int] = []
    try:
        made = _chain(client, admin_hdr, bids, "2030-09-02")
        text = " ".join(_pdf_page_texts(
            _plan_pdf(client, admin_hdr, [bids[1]], "2030-09-01", "2030-09-30")))
        for i in range(1, 5):
            assert f"ЗАМ-{i}" in text, f"причал ЗАМ-{i} не попал в лист: {text[:300]}"
    finally:
        _drop(client, admin_hdr, made, bids)


def test_plan_pdf_copy_outside_frame_stays_on_paper(client, admin_hdr):
    """Копия, уехавшая за напечатанные сутки, обязана оставить на бумаге след.

    Кадр — первые сутки периода: полоса копии в лист не попала, и раньше стрелка
    исчезала молча, хотя оригинал на листе стоял. Теперь от полосы идёт огрызок
    к нижнему краю блока с подписью «→ причал · вне кадра».
    """
    _clear_reberthings(client, admin_hdr)
    a = _own_berth(client, admin_hdr, "ВНЕ-1", 100)
    b = _own_berth(client, admin_hdr, "ВНЕ-2", 100)
    made: list[int] = []
    try:
        made = _chain(client, admin_hdr, [a, b], "2030-07-02", gap_days=5)
        pages = _pdf_page_texts(_plan_pdf(
            client, admin_hdr, [a, b], "2030-07-01", "2030-07-30", day_from=0, day_to=3))
        assert "ЦЕПОЧКА 1" in pages[0], "оригинал должен быть на листе"
        assert "ЦЕПОЧКА 2" not in " ".join(pages), "копия вне кадра не печатается"
        assert "шаг 1→2 → ВНЕ-2 · вне кадра" in pages[0], pages[0][:400]
    finally:
        _drop(client, admin_hdr, made, [a, b])


def test_plan_pdf_original_outside_frame_stays_on_paper(client, admin_hdr):
    """Оригинал до кадра — не повод терять стрелку: на листе цели она обязана быть.

    Парная половина предыдущего случая: судно стоит полосу, а пришла оно из
    постановки, которая в напечатанные сутки не попала. Огрызок входит в полосу
    со стороны шапки с подписью «от причал · вне кадра».
    """
    _clear_reberthings(client, admin_hdr)
    a = _own_berth(client, admin_hdr, "ВНЕ-3", 100)
    b = _own_berth(client, admin_hdr, "ВНЕ-4", 100)
    made: list[int] = []
    try:
        made = _chain(client, admin_hdr, [a, b], "2030-08-02", gap_days=4)
        pages = _pdf_page_texts(_plan_pdf(
            client, admin_hdr, [a, b], "2030-08-01", "2030-08-30", day_from=4, day_to=7))
        assert "ЦЕПОЧКА 2" in pages[0], "копия должна быть на листе"
        assert "шаг 1→2 от ВНЕ-3 · вне кадра" in pages[0], pages[0][:400]
    finally:
        _drop(client, admin_hdr, made, [a, b])


def test_plan_pdf_chip_alone_in_frame_draws_no_arrows(client, admin_hdr):
    """Если от связи в кадре осталась одна чип-половина — стрелок быть не должно.

    Кадр пользователя: он сдвинул сетку так, что полос цепочки не стало видно,
    а под причалами по-прежнему висели два огрызка «· вне кадра» — стрелки, у
    которых оба конца не напечатаны. Смотреть им некуда: карман «Ожидание»
    печатают на каждом листе независимо от кадра, поэтому сам по себе чип
    поводом нарисовать стрелку не считается.
    """
    _clear_reberthings(client, admin_hdr)
    a = _own_berth(client, admin_hdr, "ЧН-1", 100)
    b = _own_berth(client, admin_hdr, "ЧН-2", 100)
    park = next(x["id"] for x in client.get("/api/vessels/berths", headers=admin_hdr).json()
                if x["code"] == "ОЖИДАНИЕ")
    made: list[int] = []
    try:
        # полоса → карман → полоса; кадр — одни сутки между первой и третьей
        made = _chain(client, admin_hdr, [a, park, b], "2030-09-02", gap_days=4)
        pages = _pdf_page_texts(_plan_pdf(
            client, admin_hdr, [a, b], "2030-09-01", "2030-09-30", day_from=4, day_to=4))
        text = " ".join(pages)
        assert "ЦЕПОЧКА 2" in text, "сам чип печатается — проверка не должна быть пустой"
        assert "ЦЕПОЧКА 1" not in text and "ЦЕПОЧКА 3" not in text, "полосы вне кадра"
        assert "шаг" not in text, f"у чипа без второй половины стрелок быть не может: {text[:400]}"
    finally:
        _drop(client, admin_hdr, made, [a, b])


def test_arrow_plan_picks_the_stub_for_every_combination():
    """`_arrow_plan` крыт без рендера: линия это или огрызок — зависит только от
    того, что напечатано на каком листе: оба конца на одном листе — целая линия,
    одного конца нет — огрызок «лист N» / «вне кадра», чип без второй половины —
    вообще ничего."""
    from app.services.plan_pdf import _arrow_plan

    links = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16),
             (17, 18), (19, 20)]
    printed = {1: {0}, 2: {1}, 3: {0}, 6: {0}, 7: {0}, 8: {0}, 9: {0, 1}, 11: {0},
               12: {1}, 15: {0}, 16: {0}, 17: {0}, 18: {0}, 19: {0, 1}, 20: {1}}
    chips = {8, 9, 12, 15, 16, 17, 19}  # «Ожидание»: эти концы рисуются чипом
    code_of = {i: ("Ожидание" if i in chips else f"РП-{i}") for i in range(1, 21)}
    steps = {i: i for i in range(1, 21)}
    plan = _arrow_plan(links, printed, chips, code_of, steps)

    # половины на разных листах — огрызки с обеих сторон, номера одни и те же
    assert ("exit", 0, 1, 2, "шаг 1→2 → РП-2 · лист 2") in plan
    assert ("entry", 1, 1, 2, "шаг 1→2 от РП-1 · лист 1") in plan
    # второй конец не напечатан вовсе — «вне кадра», а не «лист N»
    assert ("exit", 0, 3, 4, "шаг 3→4 → РП-4 · вне кадра") in plan
    assert ("entry", 0, 5, 6, "шаг 5→6 от РП-5 · вне кадра") in plan
    # полоса и её чип на одном листе — ОДНА линия «полоса → чип», а не два
    # огрызка: оба конца на бумаге, их видно, поэтому и подпись просто «шаг N→M»
    assert [d for d in plan if d[2] == 7] == [("bar2chip", 0, 7, 8, "шаг 7→8")]
    # чип напечатан на двух листах, копия — ни на одном: огрызка нет ни на
    # одном. Карман «Ожидание» печатают всегда, поэтому стрелка чипа в
    # не напечатанное место смотрела бы в пустоту под причалами
    assert [d for d in plan if d[2] == 9] == []
    # но если вторая половина связи всё же на бумаге (на другом листе) —
    # огрызок у чипа нужен, и он говорит, где искать половину
    assert [d for d in plan if d[2] == 11] == [
        ("exit", 0, 11, 12, "шаг 11→12 → Ожидание · лист 2"),
        ("chip-entry", 1, 11, 12, "шаг 11→12 от РП-11 · лист 1"),
    ]
    # чип → чип на одном листе: одна стрелка, а не два огрызка
    assert [d for d in plan if d[2] == 15] == [("chip2chip", 0, 15, 16, "шаг 15→16")]
    # чип и его полоса на одном листе — тот же разворот, только наоборот:
    # линия идёт от чипа к полосе, остриём в её нижнюю грань
    assert [d for d in plan if d[2] == 17] == [("chip2bar", 0, 17, 18, "шаг 17→18")]
    # чип печатается на каждом листе, полоса — на одном: линия едет только туда,
    # где напечатана полоса, а не на все листы с чипом
    assert [d for d in plan if d[2] == 19] == [("chip2bar", 1, 19, 20, "шаг 19→20")]
    # ни один конец не напечатан — на бумаге рисовать нечего
    assert not [d for d in plan if 13 in (d[2], d[3]) or 14 in (d[2], d[3])]


def test_plan_pdf_layout_coordinates_frozen(client, admin_hdr):
    """Геометрия листа заморожена: стрелки появились, полосы — не сдвинулись.

    Значения зафиксированы рендером версии «план влезает в один лист» (правка
    раскладки в plan_pdf.py: одна шапка суток на лист вместо шапки над каждым
    причалом, строка метража 16 → 10 точек, зазор между блоками 6 → 3, шкала
    сжимается до 3,5 точек вместо шести). Из-за этого полосы уехали вверх:
    раньше над каждой из них висело по 26 точек повторных дат.

    Восемь причалов по 140 м — как раз тот случай, который в лист не влезает
    даже при поле шкалы: он и обязан распасться на листы (теперь на два, а не
    на три). Координаты здесь именно что заморожены: они ловят случайный дрейф
    раскладки, а не запрещают жить нужной правке — поправляются только вместе
    с осознанным изменением вёрстки листа.
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
        assert len(texts) == 2, f"число листов поехало: {len(texts)} вместо 2"
        assert _pdf_label_positions(content) == {
            "ЛОЖЕНИЕ А": (0, 222.1, 499.1),   # первый блок первого листа
            "ЛОЖЕНИЕ Б": (0, 222.1, 306.1),   # четвёртый блок первого листа, отм. 20 м
            "ЛОЖЕНИЕ В": (1, 222.1, 485.1),   # второй блок второго листа, отм. 40 м
        }
    finally:
        for pid in made:
            client.delete(f"/api/vessels/placements/{pid}", headers=admin_hdr)
        for bid in bids:
            client.delete(f"/api/vessels/berths/{bid}", headers=admin_hdr)
