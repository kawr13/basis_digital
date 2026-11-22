"""Нахлёст на соседний причал: судно длиннее своего причала и стоит частью
корпуса на соседе. Проверяем три вещи: метры соседа заняты по-настоящему,
направлением командует фронт (sort_order), и без явного указания соседа
постановка по-прежнему не проходит.

Своя линия причалов (НХ-1…НХ-4), а не штатные РП-1…: база в тестах одна на
весь прогон, и к моменту запуска этого модуля РП-1 уже забит чужими судами из
test_vessels.py — «конфликт ни с того ни с сего» хуже любого мока. Своя линия
даёт ещё и гарантированные длины: 100 / 110 / 130 / 100 м, соседи идут именно
в этом порядке.
"""
from __future__ import annotations

import pytest

PERIOD = {"from": "2026-06-01", "to": "2026-06-30"}
FRONT = [("НХ-1", 100), ("НХ-2", 110), ("НХ-3", 130), ("НХ-4", 100)]


@pytest.fixture(scope="module")
def front(client, admin_hdr) -> dict[str, int]:
    """Собственный фронт из четырёх причалов; создаётся один раз на модуль."""
    ids: dict[str, int] = {}
    for i, (code, length_m) in enumerate(FRONT):
        r = client.post("/api/vessels/berths",
                        json={"code": code, "length_m": length_m, "sort_order": 600 + i},
                        headers=admin_hdr)
        assert r.status_code in (201, 409), r.text
        ids[code] = (r.json()["id"] if r.status_code == 201
                     else _berth_id(client, admin_hdr, code))
    return ids


def _berth_id(client, hdr, code) -> int:
    rows = client.get("/api/vessels/berths", headers=hdr).json()
    return next(b["id"] for b in rows if b["code"] == code)


def _mk(client, hdr, *, code, vessel, day=3, length_m=None, offset_m=0, spill=None, **kw):
    body = {
        "berth_id": _berth_id(client, hdr, code),
        "vessel": vessel,
        "starts_at": f"2026-06-{day:02d}T00:00:00",
        "ends_at": f"2026-06-{day:02d}T12:00:00",
        "length_m": length_m,
        "offset_m": offset_m,
        **kw,
    }
    if spill is not None:
        body["spill_berth_id"] = spill
    return client.post("/api/vessels/placements", json=body, headers=hdr)


def _plan(client, hdr):
    return client.get("/api/vessels/placements", params=PERIOD, headers=hdr).json()


# ------------------------------------------------------------------ вперёд ----


def test_spill_forward_is_allowed_and_recorded(client, admin_hdr, front):
    """120 м у 100-метрового причала — если назван сосед, это не ошибка ввода."""
    r = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-FWD", day=3, length_m=120,
            spill=front["НХ-2"])
    assert r.status_code == 201, r.text
    assert r.json()["spill_berth_id"] == front["НХ-2"]


def test_spill_without_neighbour_stays_an_error(client, admin_hdr, front):
    """Молча «обрезать» судно до причала нельзя: просим назвать соседа."""
    r = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-NONE", day=4, length_m=120)
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "НХ-2" in detail, detail      # подсказка: куда именно лезет хвост
    assert "120" in detail


def test_spill_meters_are_busy_on_the_neighbour(client, admin_hdr, front):
    """20 м, отданные соседу, заняты: второе судно туда не пустят."""
    ok = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-LONG", day=5, length_m=120,
             spill=front["НХ-2"])
    assert ok.status_code == 201, ok.text

    # 10–30 м на НХ-2 пересекаются с чужим хвостом 0–20 м
    clash = _mk(client, admin_hdr, code="НХ-2", vessel="SPILL-CLASH", day=5, length_m=20, offset_m=10)
    assert clash.status_code == 409, clash.text
    assert "SPILL-LONG" in clash.json()["detail"]

    # а за концом нахлёста место есть — но не впритык: между бортами обязано
    # остаться `MIN_GAP_M` = 10 м
    free = _mk(client, admin_hdr, code="НХ-2", vessel="SPILL-FREE", day=5, length_m=20, offset_m=30)
    assert free.status_code == 201, free.text


def test_neighbour_can_see_who_stands_on_it(client, admin_hdr, front):
    """Конфликт обязан назвать причал, ГДЕ встретились борта, и кто сюда зашёл."""
    _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-WHO", day=6, length_m=120,
        spill=front["НХ-2"])
    clash = _mk(client, admin_hdr, code="НХ-2", vessel="SPILL-WHO2", day=6, length_m=20, offset_m=10)
    detail = clash.json()["detail"]
    assert "НХ-2" in detail and "заходит" in detail and "НХ-1" in detail, detail
    assert "SPILL-WHO" in detail


def test_conflict_on_the_spill_side_names_the_other_berth(client, admin_hdr, front):
    """Ставим своё судно на НХ-1 с хвостом на НХ-2 — занять НХ-2 уже нельзя,
    и в отказе должен быть назван НХ-2, а не причал заявителя."""
    _mk(client, admin_hdr, code="НХ-2", vessel="SPILL-HOST", day=21, length_m=30, offset_m=0)
    clash = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-GUEST", day=21, length_m=120,
                spill=front["НХ-2"])
    assert clash.status_code == 409, clash.text
    detail = clash.json()["detail"]
    assert "НХ-2" in detail and "выходит" in detail, detail


def test_first_free_offset_skips_spilled_meters(client, admin_hdr, front):
    """Подсказка «первая свободная отметка» не должна совать судно под чужой хвост."""
    _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-OFF", day=7, length_m=120,
        spill=front["НХ-2"])
    r = client.get(
        "/api/vessels/free-offset",
        params={
            "berth_id": front["НХ-2"], "length_m": 30,
            "starts_at": "2026-06-07T00:00:00", "ends_at": "2026-06-07T12:00:00",
        },
        headers=admin_hdr,
    )
    assert r.status_code == 200, r.text
    assert r.json()["offset_m"] >= 20, r.json()


# ------------------------------------------------------------------- назад ----


def test_spill_backward_occupies_the_end_of_the_berth_above(client, admin_hdr, front):
    """Отрицательная отметка = нос на причале выше; у того причала заняты последние метры."""
    r = _mk(client, admin_hdr, code="НХ-2", vessel="SPILL-BACK", day=8, length_m=50, offset_m=-20,
            spill=front["НХ-1"])
    assert r.status_code == 201, r.text
    assert r.json()["offset_m"] == -20

    # НХ-1 (100 м): заняты 80–100 м
    clash = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-BACK2", day=8, length_m=15, offset_m=85)
    assert clash.status_code == 409, clash.text
    free = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-BACK3", day=8, length_m=15, offset_m=40)
    assert free.status_code == 201, free.text


def test_any_live_berth_can_take_the_hull(client, admin_hdr, front):
    """Куда уходит корпус, решает диспетчер: причал нахлёста — любой живой.

    Порядок блоков на экране не равен порядку участков у воды (между причалами
    бывают дамба и слип), поэтому «соседство» проверять нечем. Проверяются
    метры: выбрал причал — его стенка и считается занятой.
    """
    ok = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-FAR", day=9, length_m=120,
             spill=front["НХ-3"])
    assert ok.status_code == 201, ok.text
    clash = _mk(client, admin_hdr, code="НХ-3", vessel="SPILL-FAR2", day=9, length_m=15, offset_m=0)
    assert clash.status_code == 409, clash.text
    # причал «по списку» между ними при этом свободен — мы его не трогали
    free = _mk(client, admin_hdr, code="НХ-2", vessel="SPILL-FAR3", day=9, length_m=15, offset_m=0)
    assert free.status_code == 201, free.text


def test_bow_spill_books_the_end_of_the_chosen_berth(client, admin_hdr, front):
    """Носовой нахлёст ложится на КОНЕЦ выбранной стенки, хвостовой — на начало.

    Это единственное чтение, которое имеет смысл: часть корпуса, торчащая из
    начала своего причала, встречается с концом чужой стенки — независимо от
    того, какой причал на этот конец назначили.
    """
    r = _mk(client, admin_hdr, code="НХ-2", vessel="SPILL-BOW-FAR", day=23, length_m=50, offset_m=-20,
            spill=front["НХ-4"])
    assert r.status_code == 201, r.text
    clash = _mk(client, admin_hdr, code="НХ-4", vessel="SPILL-BOW-FAR2", day=23, length_m=15, offset_m=85)
    assert clash.status_code == 409, clash.text
    free = _mk(client, admin_hdr, code="НХ-4", vessel="SPILL-BOW-FAR3", day=23, length_m=15, offset_m=20)
    assert free.status_code == 201, free.text


def test_hull_across_both_edges_is_booked_on_both_sides(client, admin_hdr, front):
    """Нос и хвост в разные стороны — не запрет, а два соседа.

    Судно длиннее своего причала иначе нельзя сдвинуть ни на метр: стоит ему
    выйти за начало шкалы, как «вторая сторона» оказывается вне закона, кабинет
    отвечает 400, а полоса примагничивается назад. Поэтому названный причал
    забирает свою сторону фронта, а вторая достается соседу по фронту.
    """
    # НХ-2 = 110 м, судно 150 м на −20 м: нос 20 м на НХ-1, хвост 40 м на НХ-3
    r = _mk(client, admin_hdr, code="НХ-2", vessel="SPILL-BOTH", day=24, length_m=150, offset_m=-20,
            spill=front["НХ-1"])
    assert r.status_code == 201, r.text
    bow = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-BOTH-BOW", day=24, length_m=15, offset_m=85)
    assert bow.status_code == 409, bow.text
    stern = _mk(client, admin_hdr, code="НХ-3", vessel="SPILL-BOTH-STERN", day=24, length_m=15, offset_m=0)
    assert stern.status_code == 409, stern.text
    free = _mk(client, admin_hdr, code="НХ-3", vessel="SPILL-BOTH-FREE", day=24, length_m=15, offset_m=60)
    assert free.status_code == 201, free.text


def test_spill_to_the_same_berth_is_rejected(client, admin_hdr, front):
    """«Выхожу на тот же причал» — не выбор, а путаница в форме."""
    r = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-SELF", day=25, length_m=120,
            spill=front["НХ-1"])
    assert r.status_code == 400, r.text
    assert "тот же причал" in r.json()["detail"], r.text


def test_spill_longer_than_the_berth_that_takes_it_is_allowed(client, admin_hdr, front):
    """Названный причал короче нахлёста — это не запрет: дальше по фронту вода.

    Кабинет не знает, что стоит за соседом (дамба, слип, свободный плёс), и
    решать за диспетчера «туда столько не влезет» не может. Метры при этом
    занимают соседа целиком: весь его причал считается занятым, и второе судно
    туда уже не встанет.
    """
    ok = _mk(client, admin_hdr, code="НХ-3", vessel="SPILL-BIG-OK", day=10, length_m=200,
             spill=front["НХ-4"])
    assert ok.status_code == 201, ok.text
    big = _mk(client, admin_hdr, code="НХ-3", vessel="SPILL-BIG", day=22, length_m=280,
              spill=front["НХ-4"])
    assert big.status_code == 201, big.text
    # 150 м нахлёста при 100 метрах НХ-4: заняты все 100 м, встать некуда
    clash = _mk(client, admin_hdr, code="НХ-4", vessel="SPILL-BIG-CLASH", day=22, length_m=15,
                offset_m=85)
    assert clash.status_code == 409, clash.text
    clash2 = _mk(client, admin_hdr, code="НХ-4", vessel="SPILL-BIG-CLASH2", day=22, length_m=15,
                 offset_m=0)
    assert clash2.status_code == 409, clash2.text


def test_spill_needs_a_known_length(client, admin_hdr, front):
    """Длина неизвестна — сколько метров легло на соседа, не знает никто."""
    r = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-NOLEN", day=11, length_m=None,
            spill=front["НХ-2"])
    assert r.status_code == 400, r.text


def test_spill_that_is_not_needed_is_rejected(client, admin_hdr, front):
    """Судно влезает в свой причал — висящий «сосед» только портит план."""
    r = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-UNNEED", day=12, length_m=80,
            spill=front["НХ-2"])
    assert r.status_code == 400, r.text


def test_spill_into_waiting_pocket_is_rejected(client, admin_hdr, front):
    """«Ожидание» — карман без стенки: метров у него нет."""
    r = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-PARK", day=13, length_m=120,
            spill=_berth_id(client, admin_hdr, "ОЖИДАНИЕ"))
    assert r.status_code == 400, r.text


def test_spill_is_checked_against_the_berth_it_really_crosses(client, admin_hdr, front):
    """Отрицательная отметка без названного соседа — то же «не влезает», с подсказкой."""
    r = _mk(client, admin_hdr, code="НХ-2", vessel="SPILL-NEG", day=14, length_m=50, offset_m=-20)
    assert r.status_code == 400, r.text
    assert "НХ-1" in r.json()["detail"], r.text


# ------------------------------------------------------------------ PATCH ----


def test_patch_can_clear_the_spill(client, admin_hdr, front):
    """null в теле — «нахлёст снять»; метры соседа снова свободны."""
    created = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-CLR", day=15, length_m=120,
                  spill=front["НХ-2"])
    assert created.status_code == 201, created.text

    shrunk = client.patch(
        f"/api/vessels/placements/{created.json()['id']}",
        json={"length_m": 90, "spill_berth_id": None},
        headers=admin_hdr,
    )
    assert shrunk.status_code == 200, shrunk.text
    assert shrunk.json()["spill_berth_id"] is None

    free = _mk(client, admin_hdr, code="НХ-2", vessel="SPILL-CLR2", day=15, length_m=20, offset_m=0)
    assert free.status_code == 201, free.text


def test_patch_without_the_field_keeps_the_spill(client, admin_hdr, front):
    """Отсутствие поля — не «снять»: иначе перетаскивание теряло бы нахлёст."""
    created = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-KEEP", day=16, length_m=120,
                  spill=front["НХ-2"])
    assert created.status_code == 201, created.text
    moved = client.patch(
        f"/api/vessels/placements/{created.json()['id']}",
        json={"starts_at": "2026-06-16T06:00:00", "ends_at": "2026-06-16T18:00:00"},
        headers=admin_hdr,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["spill_berth_id"] == front["НХ-2"]


def test_patch_moving_a_spilled_ship_off_the_edge_clears_it(client, admin_hdr, front):
    """Переехал целиком в свой новый причал — висящий нахлёст снимается сам
    (поле не присылали), а не требует «удалите нахлёст»."""
    created = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-MOVE", day=17, length_m=120,
                  spill=front["НХ-2"])
    assert created.status_code == 201, created.text
    moved = client.patch(
        f"/api/vessels/placements/{created.json()['id']}",
        json={"berth_id": front["НХ-3"], "offset_m": 0, "length_m": 120},
        headers=admin_hdr,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["spill_berth_id"] is None


# ---------------------------------------------------------------- справочник ----


def test_berth_with_spilled_vessels_cannot_be_deleted(client, admin_hdr):
    """Причал, на который выходят чужие суда, удалить нельзя: метры повиснут.

    Причалы свои и пустые: иначе проверка «на причале стоят суда» сработала бы
    раньше и про нахлёст мы бы ничего не узнали.
    """
    a = client.post("/api/vessels/berths", json={"code": "ФР-1", "length_m": 100, "sort_order": 700},
                    headers=admin_hdr)
    b = client.post("/api/vessels/berths", json={"code": "ФР-2", "length_m": 110, "sort_order": 701},
                    headers=admin_hdr)
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    ok = _mk(client, admin_hdr, code="ФР-1", vessel="SPILL-DEL", day=18, length_m=120,
             spill=b.json()["id"])
    assert ok.status_code == 201, ok.text

    r = client.delete(f"/api/vessels/berths/{b.json()['id']}", headers=admin_hdr)
    assert r.status_code == 409, r.text
    assert "выходит" in r.json()["detail"], r.text


def test_inactive_neighbour_gives_no_meters(client, admin_hdr, front):
    """Снятый с линии причал своих метров не отдаёт (его нет во фронте)."""
    bid = front["НХ-4"]
    berth = next(b for b in client.get("/api/vessels/berths", headers=admin_hdr).json()
                 if b["id"] == bid)
    was = berth["is_active"]
    berth["is_active"] = False
    assert client.patch(f"/api/vessels/berths/{bid}", json=berth, headers=admin_hdr).status_code == 200
    try:
        r = _mk(client, admin_hdr, code="НХ-3", vessel="SPILL-OFF2", day=19, length_m=150, spill=bid)
        assert r.status_code == 400, r.text
    finally:
        berth["is_active"] = was
        client.patch(f"/api/vessels/berths/{bid}", json=berth, headers=admin_hdr)


def test_spill_survives_the_plan_read(client, admin_hdr, front):
    """Сетка отдаёт нахлёст фронту: без него полосу в блоке соседа не нарисовать."""
    _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-READ", day=20, length_m=120,
        spill=front["НХ-2"])
    plan = _plan(client, admin_hdr)
    row = next(p for p in plan["placements"] if p["vessel"] == "SPILL-READ")
    assert row["spill_berth_id"] == front["НХ-2"]


def test_overhang_cascades_to_the_third_berth(client, admin_hdr, front):
    """Хвост длиннее одного соседа — он ложится на следующего, а не испаряется.

    Единственный честный смысл: корпус — непрерывный отрезок стенки. МАРИНА 200 м
    на РП-2 (110 м) с 85 м отдаёт назад 25 м, а вперёд 175 м; если соседний РП-3
    имеет 130 м, оставшиеся 45 м лежат на РП-4. Пока нахлёст обрезался по длине
    соседа, эти 45 м не видел ни экран, ни проверка: диспетчер сдвигал большое
    судно вниз, «основной причал уменьшался», а третье поле оставалось пустым и
    свободным.
    """
    r = _mk(client, admin_hdr, code="НХ-2", vessel="SPILL-CASCADE", day=26,
            length_m=200, offset_m=85, spill=front["НХ-3"])
    assert r.status_code == 201, r.text
    # 175 м хвоста: 130 м на НХ-3 и 45 м на НХ-4
    clash = _mk(client, admin_hdr, code="НХ-4", vessel="SPILL-CASCADE-2", day=26,
                length_m=10, offset_m=40)
    assert clash.status_code == 409, "45 м чужого корпуса на НХ-4 не считаются занятыми"
    # ругань обязана называть причал, где корпуса встретились, а не тот, что
    # диспетчер записал в поле «выходит на»: здесь это уже НХ-4
    assert "НХ-4" in clash.text and "SPILL-CASCADE" in clash.text, clash.text
    # сразу за ними — свободно, но с зазором в 10 м (корпус cascade-а лежит
    # здесь на 0–45 м, значит вставать можно с 55-го метра)
    free = _mk(client, admin_hdr, code="НХ-4", vessel="SPILL-CASCADE-3", day=26,
               length_m=10, offset_m=55)
    assert free.status_code == 201, free.text
    # и нос cascade-а не трогал НХ-1: там свои 110 м свободны
    bow = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-CASCADE-4", day=26,
              length_m=10, offset_m=0)
    assert bow.status_code == 201, bow.text


def test_bow_overhang_cascades_upwards(client, admin_hdr, front):
    """Носовой нахлёст так же идёт вверх по фронту: конец соседа, затем предыдущего."""
    # НХ-3 = 130 м, судно 200 м на −150 м: нос 150 м = 110 м НХ-2 + 40 м НХ-1
    r = _mk(client, admin_hdr, code="НХ-3", vessel="SPILL-BOWCHAIN", day=27,
            length_m=200, offset_m=-150, spill=front["НХ-2"])
    assert r.status_code == 201, r.text
    clash = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-BOWCHAIN-2", day=27,
                length_m=10, offset_m=70)
    assert clash.status_code == 409, "нос не доехал до НХ-1"
    free = _mk(client, admin_hdr, code="НХ-1", vessel="SPILL-BOWCHAIN-3", day=27,
               length_m=10, offset_m=20)
    assert free.status_code == 201, free.text


def test_left_at_2200_frees_the_berth_for_the_one_arriving_at_2200(client, admin_hdr, front):
    """Полуоткрытый интервал: ушло в 22:00 — с 22:00 причал и метры свободны.

    Диспетчер не мог придвинуть следующее судно вплотную: «старое заканчивает в
    22:00, следующее в 22:00 начинает разгрузку» считалось конфликтом. Метры всегда
    считали впритык нормально (0–60 и 60–120 м — не пересечение), да и полоса
    кончается ровно в 22:00: на плане два судна стоят вплотную, а кабинет на это
    отвечал 409. Время не имеет права быть злее метров.
    """
    a = _mk(client, admin_hdr, code="НХ-1", vessel="TOUCH-A", length_m=60, offset_m=0,
            starts_at="2026-06-28T00:00:00", ends_at="2026-06-28T22:00:00")
    assert a.status_code == 201, a.text
    b = _mk(client, admin_hdr, code="НХ-1", vessel="TOUCH-B", length_m=60, offset_m=0,
            starts_at="2026-06-28T22:00:00", ends_at="2026-06-29T06:00:00")
    assert b.status_code == 201, f"впритык по времени не пустило: {b.status_code} {b.text}"
    # минута внахлёст — уже настоящая стоянка в одних метрах
    c = _mk(client, admin_hdr, code="НХ-1", vessel="TOUCH-C", length_m=60, offset_m=0,
            starts_at="2026-06-28T21:59:00", ends_at="2026-06-29T06:00:00")
    assert c.status_code == 409, "минута внахлёст не считается конфликтом"
    assert "TOUCH-A" in c.text, c.text


def test_placement_that_touches_on_both_sides_is_free(client, admin_hdr, front):
    """Судно между двумя соседями, у которых его начало и конец — не конфликт.

    Проверка на «первое свободное место» и обычная проверка конфликта обязаны
    отпускать вставку между двумя стоящими вплотную суднами: иначе диспетчер
    никогда не поставит судно в разрыв, образованный чужими точными уходами.
    """
    for vessel, start, end in (("BOTH-A", "2026-06-29T00:00:00", "2026-06-29T06:00:00"),
                               ("BOTH-B", "2026-06-29T12:00:00", "2026-06-29T18:00:00")):
        assert _mk(client, admin_hdr, code="НХ-2", vessel=vessel, length_m=50, offset_m=0,
                   starts_at=start, ends_at=end).status_code == 201
    mid = _mk(client, admin_hdr, code="НХ-2", vessel="BOTH-MID", length_m=50, offset_m=0,
              starts_at="2026-06-29T06:00:00", ends_at="2026-06-29T12:00:00")
    assert mid.status_code == 201, f"разрыв между двумя суднами не найден: {mid.text}"
