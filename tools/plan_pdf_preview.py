"""Собрать PDF плана без браузера — чтобы смотреть сам лист, а не кабинет.

Нужен, когда правится вёрстка листа в app/services/plan_pdf.py: не каждый раз
поднимывать фронт, логиниться и нажимать «⤓ PDF». Берёт данные из той же БД,
что и бэкенд (DATABASE_URL, по умолчанию ./data/statcab.db).

Запуск из backend/:     ../tools/plan_pdf_preview.py --out /tmp/plan.pdf
Опции: --from/--to период, --day-from/--day-to окно кадра, --berths 1,2,3
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.db import SessionLocal                      # noqa: E402
from app.services import plan_pdf                    # noqa: E402


def main() -> int:
    today = date.today()
    ap = argparse.ArgumentParser(description="PDF видимой области плана постановки")
    ap.add_argument("--from", dest="date_from", default=str(today), type=date.fromisoformat)
    ap.add_argument("--to", dest="date_to", default=str(today + timedelta(days=11)),
                    type=date.fromisoformat)
    ap.add_argument("--day-from", type=int, default=0, help="первые сутки кадра (индекс)")
    ap.add_argument("--day-to", type=int, default=6, help="последние сутки кадра (индекс)")
    ap.add_argument("--berths", default="", help="причалы кадра: 1,2,3 (пусто = все)")
    ap.add_argument("--out", default="plan_preview.pdf")
    a = ap.parse_args()

    ids = [int(x) for x in a.berths.split(",") if x.strip()] or None
    with SessionLocal() as db:
        pdf = plan_pdf.build_plan_pdf(
            db, date_from=a.date_from, date_to=a.date_to,
            day_from=a.day_from, day_to=a.day_to, berth_ids=ids,
        )
    Path(a.out).write_bytes(pdf)
    print(f"{a.out}: {len(pdf)} байт, сутки {a.day_from}–{a.day_to}, причалы {a.berths or 'все'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
