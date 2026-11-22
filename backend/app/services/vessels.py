"""Логика документа «Постановка судов».

Документ ручной: его заводит диспетчер, 1С его не трогает. Правила здесь —
те же, что человек соблюдает на бумаге:

* сутки разбиты на 4 слота по 6 часов — это шкала плана, а не правило ввода:
  судно становится и уходит в любой момент (22:00, 06:30), слоты лишь то, чем
  этот срок меряют на бумаге и на экране;
* конец не раньше начала;
* судно занимает у причала отрезок длины: отметка швартовки + длина. Два судна
  не пересекаются, только если не пересекаются и по времени, и по длине — это и
  есть главный смысл плана: показать, где причал не справляется;
* судно не может встать «в воздухе»: его отрезок длины сверяется с метражом
  причала, а если корпус лезет за габарит — постановка обязана назвать причал,
  на который она выходит (`spill_berth_id`), и занятые метры считаются уже на
  двух причалах. Запретить нахлёст означало бы запретить план: в живом
  графике суда заходят на соседние причала каждый день;
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
# Минимальный зазор между бортами двух судов у одной стенки (заказчик, 11.11:
# «между суднами должно быть минимум расстояние 10 метров»). Меряется вдоль
# причала между соседними сегментами корпусов — в том числе там, где чужой
# корпус лёг на мои метры нахлёстом. Полотна между причалами (дамба, слип) не
# считаются: расстояние есть только там, где у обоих судов лежат метры одного
# причала.
MIN_GAP_M = 10

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


def neighbours(db: Session, berth: Berth) -> tuple[Berth | None, Berth | None]:
    """Соседи причала по фронту: (выше, ниже) — с меньшим и большим sort_order.

    sort_order — это порядок участков вдоль стенки, поэтому «соседний причал"
    считается именно по нему, а не по тому, что стоит рядом в списке на экране.
    Карман «Ожидание» соседом не бывает: метража у него нет и метры он отдать
    не может. Снятый с линии причал тоже выпадает: он не участвует в плане.
    """
    row_list = list(
        db.execute(
            select(Berth)
            .where(Berth.is_active.is_(True), Berth.code != PARK_CODE)
            .order_by(Berth.sort_order, Berth.id)
        )
        .scalars()
        .all()
    )
    for i, item in enumerate(row_list):
        if item.id == berth.id:
            return (
                row_list[i - 1] if i else None,
                row_list[i + 1] if i + 1 < len(row_list) else None,
            )
    return (None, None)


def overhang(offset_m: int, length_m: int | None, own_len: int) -> tuple[int, int]:
    """Сколько метров судно вылезает за габарит причала: (назад, вперёд).

    «вперёд» — хвост полез за конец причала; «назад» — нос встал раньше его
    начала, то есть отметка швартовки отрицательная. Длина неизвестна — лезущий
    хвост это «до конца причала и дальше», а сколько именно — неизвестно, поэтому
    таким суднам нахлёст запрещён целиком (см. validate).
    """
    start = offset_m or 0
    if length_m is None:
        return (max(-start, 0), 0)
    return (max(-start, 0), max(start + length_m - own_len, 0))


def front_pairs(
    db: Session, cache: dict[int, tuple[int, int]] | None = None
) -> list[tuple[int, int]]:
    """Живые причала фронта как [(id, метраж)] в порядке стенки.

    Порядок `sort_order` — это то, как участки идут у воды (экспозиция), поэтому
    именно он решает, куда ложатся метры корпуса, вылезшего за габарит. Карман
    «Ожидание» и снятые с линии причалы выпадает: метров, на которые можно лечь,
    у них нет.
    """
    store = {} if cache is None else cache
    if "front" not in store:
        rows = db.execute(
            select(Berth)
            .where(Berth.is_active.is_(True), Berth.code != PARK_CODE)
            .order_by(Berth.sort_order, Berth.id)
        ).scalars().all()
        store["front"] = [(b.id, berth_length(b)) for b in rows]
    return store["front"]


def spill_segments(
    *, offset_m: int = 0, length_m: int | None, own_id: int, own_len: int,
    spill_berth_id: int | None = None,
    front: list[tuple[int, int]] | None = None,
) -> list[tuple[int, float, float, str]]:
    """Сегменты нахлёста: [(причал, от, до, «нос»/«хвост»)] вдоль фронта.

    Корпус — непрерывный отрезок стенки. Сколько метров вылезло за свой причал,
    столько и лежит дальше: весь сосед, затем следующий, пока метраж не кончится.
    Раньше нахлёст целиком отдавался одному причалу: 175 м хвоста у соседа в 130 м
    показывались как 130, а 45 м корпуса исчезали с плана — и на них свободно
    вставало другое судно.

    Причал из `spill_berth_id` — слово диспетчера о том, КАКОЙ причал принимает
    корпус (он же задаёт, с какого начинать), но не отменяет метраж: остаток
    переезжает на следующего. Если причал не назван, отсчёт от соседа. Нос,
    вставший раньше начала шкалы, ложится на КОНЕЦ чужой стенки и идёт выше по
    фронту, хвост за концом — с НАЧАЛА и ниже по фронту.
    """
    back, fwd = overhang(offset_m, length_m, own_len)
    out: list[tuple[int, float, float, str]] = []
    if not (back or fwd) or not front:
        return out
    ids = [bid for bid, _ in front]
    if own_id not in ids:
        return out
    here = ids.index(own_id)
    # Комy из сторон достается названный причал. Если корпус лезет в обе стороны,
    # названный читается по фронту (выше своего — нос, ниже — хвост); если сторона
    # одна, то назван именно она, каким бы причал ни стоял в списке: между причалами
    # бывают дамба и слип, и слово диспетчера про воду важнее порядка строк.
    named = None
    if spill_berth_id in ids and spill_berth_id != own_id:
        k = ids.index(spill_berth_id)
        named = ("bow" if k < here else "stern") if (back and fwd) else ("bow" if back else "stern")
    for side, meters, step in (("bow", back, -1), ("stern", fwd, 1)):
        if not meters:
            continue
        # названный причал — точка, с которой корпус ложится дальше по фронту
        j = ids.index(spill_berth_id) if side == named else here + step
        while 0 <= j < len(front) and meters > 0:
            bid, blen = front[j]
            j += step
            if bid == own_id or not blen:
                continue
            take = min(meters, blen)
            out.append(
                (bid, blen - take, blen, side) if side == "bow" else (bid, 0, take, side)
            )
            meters -= take
    return out


def occupancy(
    *,
    berth_id: int,
    own_len: int,
    offset_m: int = 0,
    length_m: int | None = None,
    spill_berth_id: int | None = None,
    front: list[tuple[int, int]] | None = None,
) -> list[tuple[int, int, float]]:
    """Сегменты (причал, начало, конец) в метрах, которые занимает постановка.

    Всё, что решает «кто кому мешает», обязано считать занятость только отсюда:
    иначе метры, отданные соседу, окажутся невидимыми, и на них спокойно встанет
    второе судно.
    """
    start = offset_m or 0
    end = float("inf") if length_m is None else start + length_m
    out: list[tuple[int, int, float]] = []
    own_start, own_end = max(start, 0), (float("inf") if length_m is None else min(end, own_len))
    if own_end > own_start:
        out.append((berth_id, own_start, own_end))
    for bid, m_from, m_to, _side in spill_segments(
        offset_m=start, length_m=length_m, own_id=berth_id, own_len=own_len,
        spill_berth_id=spill_berth_id, front=front,
    ):
        out.append((bid, m_from, m_to))
    return out


def _len_of(db: Session, berth_id: int | None, lengths: dict[int, int]) -> int:
    """Метраж причала с мемоизацией: сетка считает занятость для каждой строки."""
    if berth_id is None:
        return UNKNOWN_LENGTH_M
    if berth_id not in lengths:
        berth = db.get(Berth, berth_id)
        lengths[berth_id] = berth_length(berth) if berth else UNKNOWN_LENGTH_M
    return lengths[berth_id]


def segments_of_row(
    db: Session, row: VesselPlacement, lengths: dict[int, int] | None = None,
    memo: dict | None = None,
) -> list[tuple[int, int, float]]:
    """Сегменты уже сохранённой постановки, включая нахлёст на соседей."""
    cache = {} if lengths is None else lengths
    store = {} if memo is None else memo
    return occupancy(
        berth_id=row.berth_id,
        own_len=_len_of(db, row.berth_id, cache),
        offset_m=row.offset_m,
        length_m=row.length_m,
        spill_berth_id=row.spill_berth_id,
        front=front_pairs(db, store),
    )


def _candidates(
    db: Session,
    *,
    berth_ids: list[int],
    starts_at: datetime,
    ends_at: datetime,
    exclude_id: int | None = None,
) -> list[VesselPlacement]:
    """Постановки, которые обязаны участвовать в проверке метров.

    Дешевле всего было бы брать «свои причалы + соседей», но для нахлёста узкого
    запроса не существует: `spill_berth_id` говорит, где корпус НАЧИНАЕТ лежать,
    а не как далеко дотянется — 175 м хвоста через соседа в 130 м ложатся и на
    следующий причал, а ссылка при этом стоит на соседа. Без нахлёста корпус целиком
    внутри своего причала, поэтому сосед сверху молча мешать не может. Берём свои
    причалы и вообще все постановки с нахлёстом в этом окне: их в терминале единицы,
    а пропустить чужие 45 м дороже, чем прочитать лишние строки.
    """
    # Интервал полуоткрытый: судно, ушедшее в 22:00, не мешает тому, что начало
    # разгрузку в 22:00. Включительное сравнение означало, что к освободившемуся
    # причалу нельзя придвинуться впритык — ни минутой раньше, ни минутой позже,
    # а за минуту до этого придвинуться было нельзя и на бумаге: полоса кончается
    # ровно в 22:00, два судна на плане стоят вплотную, а кабинет на это отвечал
    # 409. Метры ровно так же считают полуоткрыто (`ms < oe and os_ < me`):
    # 0–60 и 60–120 м — не пересечение, и время не имеет права быть злее метров.
    stmt = select(VesselPlacement).where(
        VesselPlacement.starts_at < ends_at,
        VesselPlacement.ends_at > starts_at,
        or_(VesselPlacement.berth_id.in_(berth_ids),
            VesselPlacement.spill_berth_id.is_not(None)),
    )
    if exclude_id is not None:
        stmt = stmt.where(VesselPlacement.id != exclude_id)
    return list(db.execute(stmt.order_by(VesselPlacement.starts_at)).scalars())


def find_clash(
    db: Session,
    *,
    berth_id: int,
    starts_at: datetime,
    ends_at: datetime,
    offset_m: int = 0,
    length_m: int | None = None,
    spill_berth_id: int | None = None,
    exclude_id: int | None = None,
):
    """Первая встреча бортов: (кто мешает, причал, мои метры, его метры, зазор).

    Кандидатами считаются постановки, стоящие на любом из моих причалов, И те,
    которые на них заходят своим нахлёстом: чужие 20 м на моём причале — это
    ровно те же занятые 20 м, что и мои собственные.

    Пятый элемент — зазор между бортами вдоль причала: None, если корпуса легли
    друг на друга, и 0…MIN_GAP_M, если борта разминулись, но встали ближе, чем
    требует стенка. И то, и другое — отказ, но текст обязан быть разным: «лезет на
    соседнее судно» и «стоит слишком близко» диспетчер лечит по-разному: во
    втором случае часто достаточно отодвинуться на пару метров.
    """
    lengths: dict[int, int] = {}
    memo: dict = {}
    mine = occupancy(
        berth_id=berth_id,
        own_len=_len_of(db, berth_id, lengths),
        offset_m=offset_m,
        length_m=length_m,
        spill_berth_id=spill_berth_id,
        front=front_pairs(db, memo),
    )
    ids = sorted({bid for bid, _, _ in mine})
    if not ids:
        return None
    near = None
    for row in _candidates(db, berth_ids=ids, starts_at=starts_at, ends_at=ends_at,
                           exclude_id=exclude_id):
        theirs = segments_of_row(db, row, lengths, memo)
        for (mb, ms, me) in mine:
            for (ob, os_, oe) in theirs:
                if mb != ob:
                    continue
                if ms < oe and os_ < me:
                    return (row, mb, (ms, me), (os_, oe), None)
                # борта разминулись: сколько между ними вдоль причала. спорить
                # диспетчер будет с ближайшим соседом, поэтому из нескольких
                # «ближних» берётся самого ближнего, а не первого, кого нашла база
                gap = os_ - me if me <= os_ else ms - oe
                if gap < MIN_GAP_M and (near is None or gap < near[4]):
                    near = (row, mb, (ms, me), (os_, oe), gap)
    return near


def find_conflict(
    db: Session,
    *,
    berth_id: int,
    starts_at: datetime,
    ends_at: datetime,
    offset_m: int = 0,
    length_m: int | None = None,
    spill_berth_id: int | None = None,
    exclude_id: int | None = None,
) -> VesselPlacement | None:
    """Постановка, которая пересекает эту и по времени, и по метрам.

    Метры считаются по всем причалам постановки: и по своему, и по тому, на
    который она заходит.
    """
    clash = find_clash(
        db, berth_id=berth_id, starts_at=starts_at, ends_at=ends_at, offset_m=offset_m,
        length_m=length_m, spill_berth_id=spill_berth_id, exclude_id=exclude_id,
    )
    return clash[0] if clash else None


def first_free_offset(
    db: Session, *, berth_id: int, starts_at: datetime, ends_at: datetime, length_m: int
) -> int | None:
    """Первая отметка (м), где судно встаёт без пересечений и с зазором. None — мест нет.

    Занятыми считаются и те метры, которые зашли на этот причал чужим нахлёстом:
    предложить отметку, с которой судно влезет в борт свисающему с соседа хвосту,
    хуже, чем вообще ничего не предложить. По той же причине после каждого занятого
    куска к отметке прибавляется `MIN_GAP_M`: подсказка свободного места обязана
    предлагать то место, на котором не придётся ловить 409.
    """
    lengths: dict[int, int] = {}
    memo: dict = {}
    busy = sorted(
        ((s, e) for row in find_all_conflicting(db, berth_id=berth_id, starts_at=starts_at, ends_at=ends_at)
         for (bid, s, e) in segments_of_row(db, row, lengths, memo) if bid == berth_id),
        key=lambda span: span[0],
    )
    place = 0
    for start, end in busy:
        if place + length_m + MIN_GAP_M <= start:
            break  # влезает раньше этого судна, и до его борта ещё остаётся 10 м
        place = max(place, int(end) + MIN_GAP_M)
    return place


def find_all_conflicting(
    db: Session, *, berth_id: int, starts_at: datetime, ends_at: datetime
) -> list[VesselPlacement]:
    """Постановки, задевающие причал: стоящие на нём и любые с нахлёстом — чужой
    корпус ложится вдоль фронта далеко за названный причал, и угадать это по ссылке
    нельзя (см. `_candidates`)."""
    return _candidates(db, berth_ids=[berth_id], starts_at=starts_at, ends_at=ends_at)


def normalize_spill(
    db: Session, *, berth_id: int, offset_m: int = 0, length_m: int | None = None,
    spill_berth_id: int | None,
) -> int | None:
    """Снимает нахлёст, которого геометрия больше не требует.

    Нужно для PATCH, где поле spill_berth_id не присылали: судно переехало в
    середину причала, а ссылка на «соседа, куда оно выходит» осталась бы висеть
    и продолжала бы занимать чужие метры.
    """
    if spill_berth_id is None:
        return None
    berth = db.get(Berth, berth_id)
    if berth is None or berth.code == PARK_CODE or not berth.length_m:
        return spill_berth_id
    back, fwd = overhang(offset_m or 0, length_m, berth_length(berth))
    return spill_berth_id if (back or fwd) else None


def validate(
    db: Session,
    *,
    berth_id: int,
    starts_at: datetime,
    ends_at: datetime,
    offset_m: int = 0,
    length_m: int | None = None,
    spill_berth_id: int | None = None,
    exclude_id: int | None = None,
    spill_explicit: bool = True,
) -> Berth:
    """Проверяет строку плана. Бросает ValueError с человеческим текстом.

    Нахлёст на соседний причал (`spill_berth_id`) — не исключение из правил, а
    отдельная ветка: метры, отданные соседу, обязаны быть видны там, где они
    стоят, поэтому проверка конфликта идёт по обоим причалам (см. occupancy).

    spill_explicit=False — «поле не трогали» (PATCH без spill_berth_id): тогда
    нахлёст, которого геометрия больше не требует, — не ошибка, а устаревшие
    данные, и их снимает normalize_spill ещё до проверки.

    Причал нахлёста диспетчер выбирает сам, любым: порядок вывода причалов не
    равен порядку участков у воды, так что «соседство» здесь не проверяется —
    проверяются только метры (см. occupancy).
    """
    berth = db.get(Berth, berth_id)
    if not berth:
        raise BadPlan("Причал не найден")

    starts_at, ends_at = normalize(starts_at), normalize(ends_at)
    # Время не обязано совпадать с началом слота: судно ставят и снимают тогда,
    # когда оно реально пришло и ушло, — в 22:00 и в 06:30 тоже. Слот (6 часов)
    # остался только как шкала: им меряют ширину полосы и длину стоянки. Раньше
    # кабинет требовал «00:00, 06:00, 12:00 или 18:00», и диспетчер ставил судно
    # «на ближайший слот», а живое время стоянки терялось прямо в окне постановки.
    if ends_at < starts_at:
        raise BadPlan("Конец раньше начала постановки")
    if slots_count(starts_at, ends_at) > MAX_SLOTS:
        raise BadPlan("Слишком длинная постановка: максимум один год")

    # Отметка швартовки бывает и отрицательной: нос встал раньше начала шкалы
    # своего причала, то есть судно заходит на причал выше по фронту. Раньше
    # здесь стоял max(..., 0), и такой случай молча превращался в «стоит с нуля».
    offset_m = int(offset_m or 0)
    if length_m is not None:
        if length_m <= 0:
            raise BadPlan("Длина судна должна быть больше нуля")
        if length_m > MAX_VESSEL_M:
            raise BadPlan(f"Длина судна {length_m} м — проверьте ввод: больше {MAX_VESSEL_M} м у нас не швартуются")
    elif offset_m:
        raise BadPlan("Укажите длину судна, чтобы ставить его не с начала причала")

    # «Ожидание» — карман вне причалов: сколько угодно судов в одно время,
    # без метража и конфликтов. Физически у стенки их нет.
    if berth.code == PARK_CODE:
        if spill_berth_id is not None:
            raise BadPlan("В кармане «Ожидание» нет метража: нахлёст считать некуда")
        return berth

    own_len = berth_length(berth)
    back, fwd = overhang(offset_m, length_m, own_len)

    target = None
    if spill_berth_id is not None:
        target = db.get(Berth, spill_berth_id)
        if not target:
            raise BadPlan("Причал, на который выходит судно, не найден")
        if target.id == berth.id:
            raise BadPlan("Судно не может выходить на тот же причал, у которого стоит")
        if target.code == PARK_CODE:
            raise BadPlan("«Ожидание» — карман вне стенки: своих метров у него нет")
        if not target.is_active:
            raise BadPlan(f"Причал {target.code} снят с линии: свои метры он не отдаёт")
        if length_m is None:
            raise BadPlan("Нахлёст на соседний причал возможен только с известной длиной судна")
        if not berth.length_m:
            raise BadPlan(f"У причала {berth.code} не задан метраж — не от чего считать нахлёст")

    prev_b, next_b = neighbours(db, berth)

    if target is None:
        # Нахлёста нет — судно обязано влезть в свой причал целиком. Отказ обязан
        # назвать соседа: диспетчер видит, что судно заходит за габарит, и текст
        # без «куда именно» читается как поломка кабинета.
        if berth.length_m:
            need = length_m or berth.length_m
            too_long = bool(length_m) and length_m > berth.length_m
            if too_long or offset_m < 0 or offset_m + need > berth.length_m:
                where = (
                    f"нос стоит на {back} м раньше начала причала" if back and not fwd
                    else f"хвост лезет за {own_len} м на {fwd} м"
                )
                expected = prev_b if back and not fwd else next_b
                hint = (
                    f"{where} — укажите причал, на который выходит судно"
                    + (f" (по фронту за ним {expected.code})" if expected else "")
                )
                head = (
                    f"Судно {length_m} м не встанет у причала {berth.code}: "
                    f"длина причала {berth.length_m} м."
                    if too_long
                    else f"Судно не влезает в причал {berth.code}: отметка {offset_m} м + {need} м."
                )
                raise BadPlan(f"{head} {hint}")
    else:
        need = fwd or back
        if not need:
            if spill_explicit:
                raise BadPlan(
                    f"Судно стоит целиком у причала {berth.code}: причал, на который оно выходит, не нужен"
                )
            # Поле не трогали, а судно переехало в середину причала: висящий
            # нахлёст — устаревшие данные, а не намерение диспетчера.
            target = None
        # Куда уходит корпус и сколько именно метров — решает диспетчер. Кабинет
        # не может знать, что стоит у стенки дальше по фронту: между причалами
        # бывают дамба, слип и просто свободная вода, поэтому ни «нахлёст длиннее
        # соседа», ни «корпус лезет в обе стороны» здесь не запреты. Метры, которые
        # некуда записать, просто не участвуют в проверке конфликта (см. occupancy).

    clash = find_clash(
        db, berth_id=berth_id, starts_at=starts_at, ends_at=ends_at,
        offset_m=offset_m, length_m=length_m, spill_berth_id=spill_berth_id,
        exclude_id=exclude_id,
    )
    if clash:
        other, on_id, (ms, me), (os_, oe), gap = clash
        on = db.get(Berth, on_id)
        on_code = berth.code if on_id == berth.id else (on.code if on else "?")
        # Назвать надо тот причал, ГДЕ именно встретились борта: если чужой хвост
        # лежит на моём причале (или мой на чужом), текст «причал РП-3» без
        # уточнения читается как ошибка в данных.
        # Где именно встретились борта — важно назвать: «причал РП-1» при конфликте
        # на РП-2 диспетчер читает как «кабинетпутается в терминах»
        if on_id == berth.id:
            note = ""
            if other.spill_berth_id == on_id:
                from_b = db.get(Berth, other.berth_id)
                note = f" — сюда заходит его хвост с {from_b.code}" if from_b else ""
            where = f"Причал {on_code}{note}"
        else:
            where = f"Причал {on_code} — сюда выходит корпус вашего судна"
        if gap is not None:
            # Зазор — не наезд: отказ обязан говорить, на сколько метров не
            # дотянули, а не «места нет»: лечится тем же перемещением на пару метров
            raise Conflict(
                f"{where}: между бортами {int(gap)} м, а можно не ближе {MIN_GAP_M} м — "
                f"стоит «{other.vessel}» ({other.starts_at:%d.%m %H:%M} — "
                f"{other.ends_at:%d.%m %H:%M}, {_m(os_)}–{_m(oe)} м), "
                f"ваш корпус {_m(ms)}–{_m(me)} м"
            )
        # Показываем ПЕРЕСЕЧЕНИЕ, а не весь свой отрезок: диспетчеру важно, на
        # каких метрах борта встретились, а не где стоит судно целиком
        raise Conflict(
            f"{where}: уже стоит «{other.vessel}» "
            f"({other.starts_at:%d.%m %H:%M} — {other.ends_at:%d.%m %H:%M}, "
            f"{_m(os_)}–{_m(oe)} м), пересечение по длине "
            f"{_m(max(ms, os_))}–{_m(min(me, oe))} м"
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
            # on указываем явно: между vessel_placements и berths теперь два
            # внешних ключа (стоять у причала и выходить на него), и на
            # «join(Berth)» SQLAlchemy отвечает AmbiguousForeignKeysError
            stmt.join(Berth, VesselPlacement.berth_id == Berth.id).order_by(
                Berth.sort_order, VesselPlacement.offset_m, VesselPlacement.starts_at
            )
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
    "MIN_GAP_M", "normalize", "period_of", "place_summary", "span_of",
    "placements_in_period", "rights_for", "seed_berths", "slots_count", "slots_in_range",
    "validate", "VESSEL_RIGHTS",
]
