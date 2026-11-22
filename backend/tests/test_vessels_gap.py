"""Минимальный зазор между бортами судов у одной стенки.

Раньше единственным запретом было «не пересекайся», и диспетчер ставил суда борт
в борт: на плане красиво, а в порту выясняется, что растрапы заводить негде.
Заказчик (11.11) потребовал минимум 10 метров между соседними корпусами.

Зазор меряется вдоль причала между сегментами корпусов. Полотна между причалами
(дамба, слип) не считаются: там, где кончается один причал, начинается другой, и
«расстояния» между ними в данных нет — иначе половина плана встала бы из-за
расстояния, которого никто не мерил.

Фронт свой (ГП-*), даты свои (июль): тестовая база на время прогона общая, и
тесты на просмотр причалов вправе снять РП-1 с линии — на наши метры это влиять
не должно.
"""
from __future__ import annotations

import pytest

from .test_vessels import _berth_id

# код: длина стенки, м
FRONT = [("ГП-1", 100), ("ГП-2", 110)]


@pytest.fixture(scope="module")
def front(client, admin_hdr) -> dict[str, dict]:
    ids: dict[str, dict] = {}
    for i, (code, length_m) in enumerate(FRONT):
        r = client.post("/api/vessels/berths",
                        json={"code": code, "length_m": length_m, "sort_order": 700 + i},
                        headers=admin_hdr)
        assert r.status_code in (201, 409), r.text
        ids[code] = next(b for b in client.get("/api/vessels/berths", headers=admin_hdr).json()
                         if b["code"] == code)
    return ids


def _mk(client, hdr, *, code="ГП-1", vessel="GAP SHIP", day=11, length_m=None,
        offset_m=0, spill=None, **kw):
    body = {
        "berth_id": _berth_id(client, hdr, code),
        "vessel": vessel,
        "starts_at": f"2026-07-{day:02d}T00:00:00",
        "ends_at": f"2026-07-{day:02d}T12:00:00",
        "length_m": length_m,
        "offset_m": offset_m,
        **kw,
    }
    if spill is not None:
        body["spill_berth_id"] = spill
    return client.post("/api/vessels/placements", json=body, headers=hdr)


def test_vessels_must_stand_ten_meters_apart(client, admin_hdr, front):
    """0 м и 5 м между бортами — отказ; ровно 10 м — уже можно.

    ГП-1 = 100 м, первое судно занимает 50–100 м.
    """
    assert _mk(client, admin_hdr, vessel="GAP-A", length_m=50, offset_m=50).status_code == 201

    # 40–50: формально не пересекаются, но встали борт в борт
    r = _mk(client, admin_hdr, vessel="GAP-B0", length_m=10, offset_m=40)
    assert r.status_code == 409, r.text
    assert "между бортами 0 м" in r.json()["detail"], r.text

    # 35–45: между бортами 5 м — всё ещё нельзя
    r = _mk(client, admin_hdr, vessel="GAP-B5", length_m=10, offset_m=35)
    assert r.status_code == 409, r.text
    assert "между бортами 5 м" in r.json()["detail"], r.text

    # 30–40: ровно 10 м — требуемый зазор выдержан
    ok = _mk(client, admin_hdr, vessel="GAP-B10", length_m=10, offset_m=30)
    assert ok.status_code == 201, ok.text


def test_gap_rule_does_not_reach_across_a_berth_boundary(client, admin_hdr, front):
    """Конец одного причала и начало соседнего — не «соседние борта».

    Иначе корма на 95-м метре ГП-1 запрещала бы нос на нуле ГП-2, и план
    распирало бы отказами там, где метры двух судов рядом не лежат.
    """
    assert _mk(client, admin_hdr, code="ГП-1", vessel="EDGE-A", day=12,
               length_m=30, offset_m=70).status_code == 201
    ok = _mk(client, admin_hdr, code="ГП-2", vessel="EDGE-B", day=12,
             length_m=30, offset_m=0)
    assert ok.status_code == 201, ok.text


def test_gap_is_measured_on_the_berth_that_takes_the_spill(client, admin_hdr, front):
    """Чужой хвост, лёгший на мой причал, тоже обязан держать 10 м.

    Для диспетчера разницы нет: у стенки стоит корпус, и в пяти метрах от него
    вставать нельзя — свой это корпус или свисающий с соседа.
    """
    # корпус 0–105 м: свои 100 м ГП-1 и 5 м хвостом на ГП-2
    ok = _mk(client, admin_hdr, code="ГП-1", vessel="SPG-A", day=13, length_m=105,
             offset_m=0, spill=front["ГП-2"]["id"])
    assert ok.status_code == 201, ok.text
    assert ok.json()["spill_berth_id"] == front["ГП-2"]["id"], "нахлёст не записали"

    # 10–20 м на ГП-2: до чужого хвоста 5 м
    r = _mk(client, admin_hdr, code="ГП-2", vessel="SPG-B", day=13, length_m=10, offset_m=10)
    assert r.status_code == 409, r.text
    assert "SPG-A" in r.json()["detail"], r.text

    # 15–25 м: зазор ровно 10 м
    ok = _mk(client, admin_hdr, code="ГП-2", vessel="SPG-C", day=13, length_m=10, offset_m=15)
    assert ok.status_code == 201, ok.text


def test_free_offset_never_suggests_a_spot_inside_the_gap(client, admin_hdr, front):
    """Подсказка свободного места не должна отправлять диспетчера на 409.

    Первое судно занимает 0–50 м. Подсказка для корпуса в 40 м — не 50-й метр
    (борт в борт), а 60-й: 50 плюс 10 м зазора.
    """
    assert _mk(client, admin_hdr, vessel="OFF-A", day=14,
               length_m=50, offset_m=0).status_code == 201
    r = client.get("/api/vessels/free-offset",
                   params={"berth_id": front["ГП-1"]["id"],
                           "starts_at": "2026-07-14T00:00:00",
                           "ends_at": "2026-07-14T12:00:00", "length_m": 40},
                   headers=admin_hdr)
    assert r.status_code == 200, r.text
    assert r.json()["offset_m"] == 60, r.text
