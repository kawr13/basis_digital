"""Сверка стрелок «сетка ↔ PDF»: ни одна стрелка экрана не должна теряться на бумаге.

Зачем. Экран рисует стрелки по всему периоду, а PDF печатает только те сутки и
те причалы, что сейчас в кадре сетки. Исторически это стоило стрелок: конец
цепочки, уехавший за край кадра или за фильтр причалов, исчезал с бумаги молча,
а номера «шаг N→M» на листе начинали расходиться с экраном, потому что PDF
перенумеровывал цепочку по тому, что влезло в лист.

Что проверяет. Для каждого окна и набора причалов:

  1) номера шагов — как их считает экран (`chainSteps` по /api/vessels/placements);
  2) «напечатано» — постановка пересекает напечатанные сутки (чип «Ожидания»
     печатается всегда) и её причал попадает в транзитивное замыкание фильтра;
  3) если хотя бы один конец связи напечатан, в PDF обязана встречаться подпись
     «шаг N→M» с ЭТИМИ номерами — сама стрелка или огрызок «вне кадра».

Запуск (бэкенд должен работать, база — с цепочкой перешвартовок):

    API=http://127.0.0.1:8000 LOGIN=admin PASS=secret python tools/pdf_arrow_parity.py

Выходит нулём, если расхождений нет, и единицей, если хотя бы один случай спорный.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

try:  # рендер не нужен, хватает текстового слоя
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - окружение без pypdf
    PdfReader = None  # type: ignore[assignment]

API = os.environ.get("API", "http://127.0.0.1:8000")
LOGIN = os.environ.get("LOGIN", "admin")
PASSWORD = os.environ.get("PASS", "")
# карман «Ожидание» — не причал, его чипы печатаются всегда
PARK_CODES = {"ОЖИДАНИЕ"}

# (что проверяем, период, сутки кадра, причалы; None = все)
CASES = [
    ("весь период, все причалы", "2026-10-25", "2026-11-15", 0, 21, None),
    ("весь период, только РП-4", "2026-10-25", "2026-11-15", 0, 21, "4"),
    ("весь период, только РП-2", "2026-10-25", "2026-11-15", 0, 21, "2"),
    ("весь период, РП-3 + РП-4", "2026-10-25", "2026-11-15", 0, 21, "3,4"),
    ("кадр 05.11 — 15.11", "2026-10-25", "2026-11-15", 11, 21, None),
    ("кадр 01.11 — 04.11", "2026-10-25", "2026-11-15", 7, 10, None),
    ("кадр 02.11 — 03.11", "2026-10-25", "2026-11-15", 8, 9, None),
    ("одни сутки 15.11", "2026-10-25", "2026-11-15", 21, 21, None),
    ("кадр как во фронтенде", "2026-10-26", "2026-11-25", 0, 16, "1,2,3,4"),
]


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def _get(url: str, token: str) -> bytes:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def chain_steps(rows: list[dict]) -> dict[int, int]:
    """Номера шагов, как их видит экран: корень — без связи или с битой ссылкой."""
    by_id = {p["id"]: p for p in rows}
    kids: dict[int, list[int]] = {}
    roots: list[int] = []
    for p in rows:
        src = p.get("moved_from_id")
        if src and src in by_id:
            kids.setdefault(src, []).append(p["id"])
        else:
            roots.append(p["id"])
    step: dict[int, int] = {}
    order = sorted(roots)
    while order:
        cur = order.pop(0)
        src = by_id[cur].get("moved_from_id")
        step[cur] = 1 + (step.get(src, 0) if src in by_id else 0)
        order += sorted(kids.get(cur, []), key=lambda i: (by_id[i]["starts_at"], i))
    return step


def links(rows: list[dict], step: dict[int, int]) -> list[tuple[int, int, tuple[int, int]]]:
    """Непустые связи цепочки: (оригинал, копия, (шаг оригинала, шаг копии))."""
    by_id = {p["id"]: p for p in rows}
    return [(p["moved_from_id"], p["id"], (step[p["moved_from_id"]], step[p["id"]]))
            for p in rows
            if p.get("moved_from_id") in by_id and not p.get("committed")]


def closure(rows: list[dict], selected: set[int]) -> set[int]:
    """Транзитивное замыкание фильтра причалов вдоль цепочки — так обязан
    досчитывать и PDF: половина связи не имеет права печататься без второй."""
    sel = set(selected)
    for _ in range(len(rows) + 2):
        grown = set(sel)
        for p in rows:
            src = next((q for q in rows if q["id"] == p.get("moved_from_id")), None)
            if src is None:
                continue
            if p["berth_id"] in sel or src["berth_id"] in sel:
                grown |= {p["berth_id"], src["berth_id"]}
        if grown == sel:
            break
        sel = grown
    return sel


def pdf_pairs(pdf_bytes: bytes) -> tuple[set[tuple[int, int]], int]:
    if PdfReader is None:
        raise SystemExit("нужен pypdf: python -m pip install pypdf")
    reader = PdfReader(__import__("io").BytesIO(pdf_bytes))
    text = " ".join((page.extract_text() or "") for page in reader.pages)
    pairs = {tuple(map(int, m)) for m in re.findall(r"шаг\s*(\d+)[→\-](\d+)", text)}
    return pairs, len(reader.pages)


def main() -> int:
    token = _post(f"{API}/api/auth/login", {"login": LOGIN, "password": PASSWORD})["access_token"]
    bad = 0
    for name, d1, d2, day1, day2, berths in CASES:
        rows = json.loads(_get(f"{API}/api/vessels/placements?from={d1}&to={d2}", token).decode())["placements"]
        if not rows:
            print(f"    ? в базе нет постановок за {d1}..{d2} — проверка не о чём")
            return 2
        park = {p["berth_id"] for p in rows} & {b["id"] for b in json.loads(
            _get(f"{API}/api/vessels/berths", token).decode()) if b["code"] in PARK_CODES}
        step = chain_steps(rows)
        frm = date.fromisoformat(d1)
        lo, hi = frm + timedelta(days=day1), frm + timedelta(days=day2)

        by_id = {p["id"]: p for p in rows}
        # «Ожидание» — очередь на ПЕРВЫЕ сутки кадра: чип, чьё плановое
        # перемещение началось раньше первого напечатанного дня, с листа
        # снимается вместе со своими стрелками (ровно так же рисовальщик).
        # Последними сутками руководиться нельзя — так лист терял бы всю
        # перешвартовку, когда цепочка целиком лежит внутри кадра.
        moved_from = {p["moved_from_id"]: p for p in rows
                      if p.get("moved_from_id") and not p.get("committed")}
        stale = {p["id"] for p in rows
                 if p["berth_id"] in park
                 and (nxt := moved_from.get(p["id"])) is not None
                 and date.fromisoformat(nxt["starts_at"][:10]) < lo}

        def printed(p: dict) -> bool:
            if p["berth_id"] in park:  # чип «Ожидания», если он ещё ждёт
                return p["id"] not in stale
            return (date.fromisoformat(p["starts_at"][:10]) <= hi
                    and date.fromisoformat(p["ends_at"][:10]) >= lo)

        on_paper = {p["id"] for p in rows if printed(p)}
        if berths:
            sel = closure(rows, {int(x) for x in berths.split(",")} | park)
            on_paper &= {p["id"] for p in rows if p["berth_id"] in sel}

        def on_berth(pid: int) -> bool:
            """Напечатанная ПОЛОСА связи. Карман «Ожидание» не считается: его
            чипы печатают на каждом листе независимо от кадра, и стрелка от
            чипа в не напечатанное место смотрит в пустоту — её требуют
            только тогда, когда в кадре есть хотя бы одна полоса связи."""
            return pid in on_paper and by_id[pid]["berth_id"] not in park

        want = {pair for src_id, dst_id, pair in links(rows, step)
                if src_id not in stale and dst_id not in stale
                and (on_berth(src_id) or on_berth(dst_id))}
        got, sheets = pdf_pairs(_get(
            f"{API}/api/vessels/plan.pdf?from={d1}&to={d2}&day_from={day1}&day_to={day2}"
            + (f"&berth_ids={berths}" if berths else ""), token))
        miss, extra = sorted(want - got), sorted(got - want)
        bad += bool(miss or extra)
        print(f"{'БАГ' if miss or extra else 'OK '} {name}: ждём {sorted(want)} | PDF {sorted(got)}"
              f" | листов {sheets}"
              + (f" | НЕТ В PDF {miss}" if miss else "")
              + (f" | ЛИШНЕЕ {extra}" if extra else ""))
    print(f"\nрасхождений: {bad} из {len(CASES)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
