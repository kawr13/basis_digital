"""Вкладка «Отчёты»: период + склады + выгрузка CSV.

Права те же, что у метрик: менеджер не получит строк по чужому складу, даже
если подставит его id в параметр `warehouses`.
"""
from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import User, Warehouse
from ..services import metrics as q
from ..services import reports as r

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _period(date_from: date | None, date_to: date | None):
    return r.clamp_period(date_from, date_to)


def _scope(db: Session, user: User, warehouses: str | None) -> list[int]:
    """Разрешённые склады пользователя ∩ то, что он попросил."""
    allowed = set(q.allowed_warehouse_ids(db, user))
    if not warehouses:
        return sorted(allowed)
    try:
        asked = {int(x) for x in warehouses.split(",") if x.strip()}
    except ValueError:
        raise HTTPException(400, "warehouses — список id через запятую")
    if not asked:
        return []
    if asked - allowed:
        raise HTTPException(403, "Нет доступа к складу(ам): " + ", ".join(map(str, sorted(asked - allowed))))
    return sorted(asked)


# ------------------------------------------------------------------ CSV ----

def _to_csv(headers: list[str], rows: list[list[object]]) -> Response:
    """CSV для русскоязычного Excel: разделитель «;», десятичный «,», BOM в начале."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([
            f"{v:.1f}".replace(".", ",") if isinstance(v, float) else v for v in row
        ])
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="report.csv"'},
    )


def _fmt_dt(value) -> str:
    return value.strftime("%d.%m.%Y %H:%M") if hasattr(value, "strftime") else str(value)


# -------------------------------------------------------------- отчёты ----

@router.get("/processing")
def processing(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    warehouses: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    start, end = _period(date_from, date_to)
    ids = _scope(db, user, warehouses)
    return {
        "period": {"from": start, "to": end},
        "totals": r.processing_totals(db, ids, start, end),
        "per_warehouse": r.per_warehouse_processing(db, ids, start, end),
        "rows": r.daily_processing(db, ids, start, end),
    }


@router.get("/processing.csv")
def processing_csv(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    warehouses: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    start, end = _period(date_from, date_to)
    ids = _scope(db, user, warehouses)
    rows = r.daily_processing(db, ids, start, end)
    return _to_csv(
        ["Дата", "Код склада", "Склад", "Принято, т", "Отгружено, т", "Обработано, т",
         "Документов", "Ср. хранение, ч"],
        [[row["bucket_date"].isoformat(), row["code"], row["warehouse"], row["inbound_t"],
          row["outbound_t"], row["processed_t"], row["docs_count"], row["avg_dwell_hours"]]
         for row in rows],
    )


@router.get("/cargo")
def cargo(
    warehouses: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = r.cargo_report(db, _scope(db, user, warehouses))
    totals = {
        "weight_t": round(sum(x["weight_t"] for x in rows), 1),
        "overdue_weight_t": round(sum(x["overdue_weight_t"] for x in rows), 1),
        "positions": len(rows),
    }
    return {"snapshot_at": rows[0]["snapshot_at"] if rows else None, "totals": totals, "rows": rows}


@router.get("/cargo.csv")
def cargo_csv(
    warehouses: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = r.cargo_report(db, _scope(db, user, warehouses))
    return _to_csv(
        ["Срез", "Код склада", "Склад", "Группа груза", "Мест", "Вес, т", "Просрочка, т", "Ср. срок, сут"],
        [[_fmt_dt(row["snapshot_at"]), row["code"], row["warehouse"], row["cargo_group"],
          row["qty"], row["weight_t"], row["overdue_weight_t"], row["avg_age_days"]]
         for row in rows],
    )


@router.get("/capacity-trend")
def capacity_trend(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    warehouses: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    start, end = _period(date_from, date_to)
    rows = r.capacity_trend(db, _scope(db, user, warehouses), start, end)
    return {"period": {"from": start, "to": end}, "rows": rows}


@router.get("/capacity-trend.csv")
def capacity_trend_csv(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    warehouses: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    start, end = _period(date_from, date_to)
    rows = r.capacity_trend(db, _scope(db, user, warehouses), start, end)
    return _to_csv(
        ["Срез", "Код склада", "Склад", "Занято", "Ёмкость", "Загрузка, %", "Единица"],
        [[_fmt_dt(row["snapshot_at"]), row["code"], row["warehouse"], row["used"],
          row["capacity"], round(row["utilization"] * 100, 1), row["unit"]] for row in rows],
    )


@router.get("/warehouses")
def report_warehouses(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Список складов для фильтра отчётов (только доступные пользователю)."""
    ids = q.allowed_warehouse_ids(db, user)
    if not ids:
        return []
    return [
        {"id": w.id, "code": w.code, "name": w.name}
        for w in db.scalars(select(Warehouse).where(Warehouse.id.in_(ids)).order_by(Warehouse.name))
    ]
