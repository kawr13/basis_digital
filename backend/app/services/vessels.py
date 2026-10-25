"""Логика документа «Постановка судов».

Документ ручной: его заводит диспетчер, 1С его не трогает. Правила здесь —
те же, что человек соблюдает на бумаге:

* время кратно слоту (сутки = 4 слота по 6 часов), иначе строка не совпадёт
  с сеткой плана;
* конец не раньше начала;
* судно занимает у причала отрезок длины: отметка швартовки + длина. Два судна
  не пересекаются, только если не пересекаются и по времени, и по длине — это и
  есть главный смысл плана: показать, где причал не справляется;
* судно не может встать за край причала (сверяем с его метражом).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .app_settings import role_set
from ..models import Berth, User, VesselPlacement

# Личное право пользователя на постановку судов (колонка users.vessel_right).
# Пусто = «по роли», дальше — по нарастающей: смотреть → ставить → ещё и удалять.
VESSEL_RIGHTS = ("none", "view", "edit", "manage")
_RIGHT_LEVEL = {"none": 0, "view": 1, "edit": 2, "manage": 3}


def rights_for(db: Session, user: User) -> dict[str, bool]:
    """Что разрешено пользователю: личная настройка перекрывает ролевую."""
    if user.vessel_right:
        level = _RIGHT_LEVEL.get(user.vessel_right, 0)
        return {"view": level >= 1, "edit": level >= 2, "delete": level >= 3}
    return {
        "view": user.role.value in role_set(db, "vessels.view_roles"),
        "edit": user.role.value in role_set(db, "vessels.edit_roles"),
        "delete": user.role.value in role_set(db, "vessels.delete_roles"),
    }

class BadPlan(ValueError):
    """Строка плана не проходит правила: не тот слот, конец раньше начала и т. п."""


class Conflict(ValueError):
    """На дорожке уже стоит другое судно."""


# Сутки разбиты на 4 слота — как в исходном плане (00-06, 06-12, 12-18, 18-00).
SLOT_HOURS = (0, 6, 12, 18)
SLOT = timedelta(hours=6)
# Защита от опечатки: план на 10 лет вперёд — почти наверняка ошибка ввода.
MAX_SLOTS = 4 * 366
# Вертикальная шкала плана: 10 метров длины причала на одну ячейку.
M_PER_CELL = 10
# Если метраж причала не завели, сетка всё равно обязана что-то показать.
UNKNOWN_LENGTH_M = 100
# Больше 500 м судов в наших портах не бывает — это опечатка в поле.
MAX_VESSEL_M = 500

# Причалы по умолчанию — сняты с бумажного плана «Постановка судов».
DEFAULT_BERTHS = [
    ("РП-1", 8.3, 100),
    ("РП-2", 8.3, 110),
    ("РП-3", 8.6, 130),
    ("РП-4", 8.6, 120),
    ("РП-5", 10.1, 100),
    ("РП-6", 10.1, 100),
]

# «Ожидание»: не причал, а карман для судов вне причалов — ждут черёды,
# перешвартовки, груза. Судов здесь может стоять сколько угодно одновременно:
# физически у стенки их нет, поэтому метраж и конфликты к ним не применяются.
PARK_CODE = "ОЖИДАНИЕ"
PARK_SORT_ORDER = 900


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


def berth_length(berth: Berth) -> int:
    """Длина причала в метрах; если метраж не задан — показываем 100 м."""
    return berth.length_m or UNKNOWN_LENGTH_M


def length_cells(berth: Berth) -> int:
    """Сколько ячеек по 10 м нужно причалу, чтобы влез целиком (130 м → 13)."""
    return max(1, -(-berth_length(berth) // M_PER_CELL))


def span_of(row: VesselPlacement) -> tuple[int, float]:
    """Отрезок длины, который занимает постановка: [начало, конец) в метрах.

    Длина неизвестна (NULL) — считаем, что судно перекрывает причал до самого
    конца: иначе кабинет разрешил бы вторую постановку «рядом», хотя мы не знаем,
    сколько на самом деле занято.
    """
    start = row.offset_m or 0
    return (start, float("inf")) if row.length_m is None else (start, start + row.length_m)


def find_conflict(
    db: Session,
    *,
    berth_id: int,
    starts_at: datetime,
    ends_at: datetime,
    offset_m: int = 0,
    length_m: int | None = None,
    exclude_id: int | None = None,
) -> VesselPlacement | None:
    """Постановка, которая пересекает эту и по времени, и по длине причала."""
    stmt = select(VesselPlacement).where(
        VesselPlacement.berth_id == berth_id,
        VesselPlacement.starts_at <= ends_at,
        VesselPlacement.ends_at >= starts_at,
    )
    if exclude_id is not None:
        stmt = stmt.where(VesselPlacement.id != exclude_id)
    mine_end = float("inf") if length_m is None else offset_m + length_m
    for row in db.execute(stmt.order_by(VesselPlacement.starts_at)).scalars():
        other_start, other_end = span_of(row)
        if offset_m < other_end and other_start < mine_end:
            return row
    return None


def first_free_offset(
    db: Session, *, berth_id: int, starts_at: datetime, ends_at: datetime, length_m: int
) -> int | None:
    """Первая отметка (м), где судно такой длины встаёт без пересечений. None — мест нет."""
    busy = sorted(
        (span_of(row) for row in find_all_conflicting(db, berth_id=berth_id, starts_at=starts_at, ends_at=ends_at)),
        key=lambda span: span[0],
    )
    place = 0
    for start, end in busy:
        if place + length_m <= start:
            break
        place = max(place, int(end))
    return place


def find_all_conflicting(
    db: Session, *, berth_id: int, starts_at: datetime, ends_at: datetime
) -> list[VesselPlacement]:
    stmt = select(VesselPlacement).where(
        VesselPlacement.berth_id == berth_id,
        VesselPlacement.starts_at <= ends_at,
        VesselPlacement.ends_at >= starts_at,
    )
    return list(db.execute(stmt).scalars().all())


def validate(
    db: Session,
    *,
    berth_id: int,
    starts_at: datetime,
    ends_at: datetime,
    offset_m: int = 0,
    length_m: int | None = None,
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

    offset_m = max(int(offset_m or 0), 0)
    if length_m is not None:
        if length_m <= 0:
            raise BadPlan("Длина судна должна быть больше нуля")
        if length_m > MAX_VESSEL_M:
            raise BadPlan(f"Длина судна {length_m} м — проверьте ввод: больше {MAX_VESSEL_M} м у нас не швартуются")
    elif offset_m:
        raise BadPlan("Укажите длину судна, чтобы ставить его не с начала причала")

    # «Ожидание» — карман вне причалов: сколько угодно судов в одно время,
    # без метража и конфликтов. Физически у стенки их нет.
    if berth.code != PARK_CODE:
        # вписывается ли судно в причал по метражу
        if berth.length_m:
            if length_m and length_m > berth.length_m:
                raise BadPlan(
                    f"Судно {length_m} м не встанет у причала {berth.code}: длина причала {berth.length_m} м"
                )
            need = length_m or berth.length_m
            if offset_m + need > berth.length_m:
                raise BadPlan(
                    f"Судно не влезает в причал {berth.code}: отметка {offset_m} м + {need} м "
                    f"больше длины причала {berth.length_m} м"
                )

        clash = find_conflict(
            db, berth_id=berth_id, starts_at=starts_at, ends_at=ends_at,
            offset_m=offset_m, length_m=length_m, exclude_id=exclude_id,
        )
        if clash:
            clash_start, clash_end = span_of(clash)
            mine_end = float("inf") if length_m is None else offset_m + length_m
            raise Conflict(
                f"Причал {berth.code}: уже стоит «{clash.vessel}» "
                f"({clash.starts_at:%d.%m %H:%M} — {clash.ends_at:%d.%m %H:%M}, "
                f"{_m(clash_start)}–{_m(clash_end)} м), пересечение по длине "
                f"{_m(max(offset_m, clash_start))}–{_m(min(mine_end, clash_end))} м"
            )
    return berth


def _m(value: float) -> str:
    """Метры в тексте ошибки: неопределённый конец показываем как «дальше»."""
    return "дальше" if value == float("inf") else str(int(value))


def placements_in_period(
    db: Session, *, date_from: date, date_to: date, berth_id: int | None = None
) -> list[VesselPlacement]:
    """Постановки, которые хоть одним слотом попадают в период.

    «Ожидание» — карман без привязки к дате: слоты у его строк формальность,
    поэтому суда в кармане показываются в любом периоде."""
    start = datetime.combine(date_from, datetime.min.time())
    end = datetime.combine(date_to, datetime.max.time())
    park = select(Berth.id).where(Berth.code == PARK_CODE).scalar_subquery()
    stmt = select(VesselPlacement).where(
        or_(
            (VesselPlacement.starts_at <= end) & (VesselPlacement.ends_at >= start),
            VesselPlacement.berth_id == park,
        )
    )
    if berth_id is not None:
        stmt = stmt.where(VesselPlacement.berth_id == berth_id)
    return list(
        db.execute(
            stmt.join(Berth).order_by(Berth.sort_order, VesselPlacement.offset_m, VesselPlacement.starts_at)
        )
        .scalars()
        .all()
    )


def apply_reberthing_state(db: Session, rows: list[VesselPlacement], *, now: datetime | None = None) -> None:
    """Дата перестановки наступила — перешвартовка «состоялась».

    Каждой строке навешиваются два вычисляемых поля (в БД их нет):
    * ``committed`` — копия, чья дата начала уже прошла: это обычная
      постановка, стрелка и пунктир больше не нужны — судно передвинулось;
    * ``effective_ends_at`` — для оригинала с состоявшейся копией: оригинал
      не показывают от начала копии и позже (иначе полоса «едет» за судном,
      которое по плану уже ушло на другой причал).

    Пока дата не наступила, вид плана прежний: оригинал, пунктирная копия,
    стрелка. Карман «Ожидание» не трогаем: его слоты — формальность, чип
    живёт в кармане, пока диспетчер его не уберёт.

    ``now`` — момент, относительно которого судят о состоянии. Для просмотра
    периода целиком в прошлом передаётся конец периода: диспетчер видит план
    таким, каким он был тогда — с ещё не наступившими перестановками.
    """
    now = now or datetime.now()
    park_id = db.execute(select(Berth.id).where(Berth.code == PARK_CODE)).scalar()
    for r in rows:
        r.committed = False
        r.effective_ends_at = None
    ids = [r.id for r in rows if r.id and r.berth_id != park_id]
    cut: dict[int, datetime] = {}
    if ids:
        q = (
            select(VesselPlacement.moved_from_id, func.min(VesselPlacement.starts_at))
            .where(
                VesselPlacement.moved_from_id.in_(ids),
                VesselPlacement.starts_at <= now,
                VesselPlacement.berth_id != park_id,
            )
            .group_by(VesselPlacement.moved_from_id)
        )
        cut = {int(oid): ts for oid, ts in db.execute(q)}
    for r in rows:
        if r.berth_id == park_id:
            continue
        if r.moved_from_id and r.starts_at <= now:
            r.committed = True
        earliest = cut.get(r.id)
        if earliest is not None and earliest < r.ends_at:
            r.effective_ends_at = earliest - SLOT


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


def ensure_park(db: Session) -> bool:
    """Заводит строку «Ожидание», если её нет. Идемпотентно: вызывается на каждом старте."""
    if db.execute(select(Berth.id).where(Berth.code == PARK_CODE)).first():
        return False
    db.add(Berth(code=PARK_CODE, sort_order=PARK_SORT_ORDER))
    db.commit()
    return True


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
    if p.length_m:
        bits.append(f"{p.offset_m or 0}+{p.length_m} м")
    if p.volume:
        bits.append(p.volume)
    if p.cargo:
        bits.append(p.cargo)
    return " · ".join(bits)


__all__ = [
    "SLOT", "SLOT_HOURS", "DEFAULT_BERTHS", "BadPlan", "Conflict",
    "PARK_CODE", "PARK_SORT_ORDER", "ensure_park",
    "berth_length", "berths", "find_conflict", "first_free_offset", "length_cells", "M_PER_CELL",
    "normalize", "period_of", "place_summary", "span_of",
    "placements_in_period", "rights_for", "seed_berths", "slots_count", "slots_in_range",
    "validate", "VESSEL_RIGHTS",
]
