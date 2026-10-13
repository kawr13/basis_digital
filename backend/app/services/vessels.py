"""Логика документа «Постановка судов».

Документ ручной: его заводит диспетчер, 1С его не трогает. Правила здесь —
те же, что человек соблюдает на бумаге:

* время кратно слоту (сутки = 4 слота по 6 часов), иначе строка не совпадёт
  с сеткой плана;
* конец не раньше начала;
* на одной дорожке одного причала два судна не пересекаются — это и есть
  главный смысл плана, показать, где причал не справляется.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Berth, VesselPlacement

class BadPlan(ValueError):
    """Строка плана не проходит правила: не тот слот, конец раньше начала и т. п."""


class Conflict(ValueError):
    """На дорожке уже стоит другое судно."""


# Сутки разбиты на 4 слота — как в исходном плане (00-06, 06-12, 12-18, 18-00).
SLOT_HOURS = (0, 6, 12, 18)
SLOT = timedelta(hours=6)
# Защита от опечатки: план на 10 лет вперёд — почти наверняка ошибка ввода.
MAX_SLOTS = 4 * 366

# Причалы по умолчанию — сняты с бумажного плана «Постановка судов».
DEFAULT_BERTHS = [
    ("РП-1", 8.3, 100),
    ("РП-2", 8.3, 110),
    ("РП-3", 8.6, 130),
    ("РП-4", 8.6, 120),
    ("РП-5", 10.1, 100),
    ("РП-6", 10.1, 100),
]


def normalize(dt: datetime) -> datetime:
    """Секунды и микросекунды не значимы: план оперирует слотами."""
    return dt.replace(second=0, microsecond=0)


def slot_error(dt: datetime) -> str | None:
    """Сообщение об ошибке, если время не совпадает с началом слота."""
    if dt.minute != 0:
        return "Время должно быть началом слота: 00:00, 06:00, 12:00 или 18:00"
    if dt.hour not in SLOT_HOURS:
        return "Слот начинается в 00:00, 06:00, 12:00 или 18:00"
    return None


def slots_count(starts_at: datetime, ends_at: datetime) -> int:
    """Сколько слотов занимает постановка (границы включительно)."""
    return int((ends_at - starts_at) // SLOT) + 1


def slots_in_range(starts_at: datetime, ends_at: datetime) -> list[datetime]:
    cur, out = starts_at, []
    while cur <= ends_at:
        out.append(cur)
        cur += SLOT
    return out


def find_conflict(
    db: Session,
    *,
    berth_id: int,
    lane: int,
    starts_at: datetime,
    ends_at: datetime,
    exclude_id: int | None = None,
) -> VesselPlacement | None:
    """Пересекающаяся постановка на той же дорожке того же причала."""
    stmt = select(VesselPlacement).where(
        VesselPlacement.berth_id == berth_id,
        VesselPlacement.lane == lane,
        VesselPlacement.starts_at <= ends_at,
        VesselPlacement.ends_at >= starts_at,
    )
    if exclude_id is not None:
        stmt = stmt.where(VesselPlacement.id != exclude_id)
    return db.execute(stmt.order_by(VesselPlacement.starts_at)).scalars().first()


def validate(
    db: Session,
    *,
    berth_id: int,
    lane: int,
    starts_at: datetime,
    ends_at: datetime,
    exclude_id: int | None = None,
) -> Berth:
    """Проверяет строку плана. Бросает ValueError с человеческим текстом."""
    berth = db.get(Berth, berth_id)
    if not berth:
        raise BadPlan("Причал не найден")

    starts_at, ends_at = normalize(starts_at), normalize(ends_at)
    for err in (slot_error(starts_at), slot_error(ends_at)):
        if err:
            raise BadPlan(err)
    if ends_at < starts_at:
        raise BadPlan("Конец раньше начала постановки")
    if slots_count(starts_at, ends_at) > MAX_SLOTS:
        raise BadPlan("Слишком длинная постановка: максимум один год")

    if lane > max(berth.lanes, 1):
        raise BadPlan(f"У причала {berth.code} только {max(berth.lanes, 1)} дорожка(и)")

    clash = find_conflict(
        db, berth_id=berth_id, lane=lane, starts_at=starts_at, ends_at=ends_at, exclude_id=exclude_id
    )
    if clash:
        raise Conflict(
            f"Причал {berth.code}, дорожка {lane}: уже стоит «{clash.vessel}» "
            f"({clash.starts_at:%d.%m %H:%M} — {clash.ends_at:%d.%m %H:%M})"
        )
    return berth


def placements_in_period(
    db: Session, *, date_from: date, date_to: date, berth_id: int | None = None
) -> list[VesselPlacement]:
    """Постановки, которые хоть одним слотом попадают в период."""
    start = datetime.combine(date_from, datetime.min.time())
    end = datetime.combine(date_to, datetime.max.time())
    stmt = select(VesselPlacement).where(
        VesselPlacement.starts_at <= end,
        VesselPlacement.ends_at >= start,
    )
    if berth_id is not None:
        stmt = stmt.where(VesselPlacement.berth_id == berth_id)
    return list(
        db.execute(
            stmt.join(Berth).order_by(Berth.sort_order, VesselPlacement.lane, VesselPlacement.starts_at)
        )
        .scalars()
        .all()
    )


def berths(db: Session, *, only_active: bool = False) -> list[Berth]:
    stmt = select(Berth)
    if only_active:
        stmt = stmt.where(Berth.is_active.is_(True))
    return list(db.execute(stmt.order_by(Berth.sort_order, Berth.code)).scalars().all())


def seed_berths(db: Session) -> int:
    """Заводит причалы, если справочник пуст (первый запуск кабинета)."""
    if db.execute(select(Berth.id).limit(1)).first():
        return 0
    for i, (code, depth, length_m) in enumerate(DEFAULT_BERTHS):
        db.add(Berth(code=code, depth=depth, length_m=length_m, lanes=2, sort_order=i))
    db.commit()
    return len(DEFAULT_BERTHS)


def period_of(query_from: date | None, query_to: date | None, default_days: int = 31) -> tuple[date, date]:
    """Период просмотра: по умолчанию «сегодня и N дней вперёд»."""
    if query_from and query_to:
        return (query_from, query_to) if query_from <= query_to else (query_to, query_from)
    today = date.today()
    if query_from:
        return query_from, query_from + timedelta(days=default_days - 1)
    if query_to:
        return query_to - timedelta(days=default_days - 1), query_to
    return today, today + timedelta(days=default_days - 1)


def place_summary(p: VesselPlacement) -> str:
    """Одна строка человекочитаемого описания — для аудита и подсказок."""
    bits = [p.vessel]
    if p.volume:
        bits.append(p.volume)
    if p.cargo:
        bits.append(p.cargo)
    return " · ".join(bits)


__all__ = [
    "SLOT", "SLOT_HOURS", "DEFAULT_BERTHS", "BadPlan", "Conflict",
    "berths", "find_conflict", "normalize", "period_of", "place_summary",
    "placements_in_period", "seed_berths", "slots_count", "slots_in_range", "validate",
]
