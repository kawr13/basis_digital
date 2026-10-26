"""PDF плана постановки судов — ровно тот кусок, который виден на экране.

Печать браузера для этой задачи не подошла: она печатает страницу кабинета, а
диспетчеру нужен файл с конкретным куском плана — те сутки и причалы, что сейчас
в окне сетки. Поэтому PDF рисуем сами, векторно: текст остаётся текстом
(кириллица, поиск, копирование), линии — линиями, а не пиксельной картинкой.

Что попадает в лист решает фронтенд: он знает, что в кадре, и присылает `day_from`
/ `day_to` (сутки окна) и `berth_ids` (причалы окна). Сервер только рисует.

Лист сжат по высоте, а не разрезан: шапка суток печатается ОДНА на лист, строка
метража уплотнена, зазор между блоками маленький, а метровая шкала имеет право
сжиматься вплоть до CELL_H_MIN. Типовое окно из шести причалов поэтому лежит на
одном листе, и стрелки перешвартовок вовсе не пересекают границу листа. Если не
влезло и при поле — план распадается на листы с теми же огрызками, что и раньше.

Стрелки перешвартовок через границу листа: лист разрезан по причалам и суткам,
и пара «оригинал → копия» может оказаться на разных листах — или наполовину за
пределами кадра. Раскладка поэтому считается в два прохода: сначала «какой
причал на каком листе» (там же рождаются и сами листы), потом рисование —
второй проход берёт числа первого, а не считает их заново. А вот что рисовать
у каждой половины связи, решает одна функция — `_arrow_plan`: если в кадре
есть хотя бы одна ПОЛОСА связи, на бумаге обязана быть подпись «шаг N→M» с теми
же номерами, что на экране. Второй конец на другом листе → огрызок «· лист N»;
не напечатан вовсе (причал не в фильтре либо постановка вне напечатанных
суток) → «· вне кадра». Карман «Ожидание» сам по себе поводом нарисовать
стрелку не считается — его чипы печатают на каждом листе. Уходящий огрызок идёт в нижний край блока причала
(«шаг N→M → РП-6 · лист 2»), входящий входит в полосу со стороны шапки
(«шаг N→M от РП-3 · лист 1»), у чипа «Ожидания» огрызок растёт вверх от его
верхней грани.

Правило листа: ни одна линия не имеет права пересекать больше одного блока
причала — зачёркнутый сверху донизу лист не читается вовсе. Строже всего оно
выполнено там, где его нарушить легче всего: карман «Ожидания» лежит под всеми
причалами, и одна линия в него перечёркивала весь лист. Поэтому связь «полоса ↔
чип „Ожидания“» — всегда ДВА коротких огрызка, даже когда оба конца напечатаны
на одном листе: у полосы — вниз к нижнему краю ЕЁ блока с подписью «шаг N→M →
Ожидание», у чипа — вверх от его верхней грани с подписью «шаг N→M от РП-6».
Полоса ↔ полоса остаётся прямой: оба её конца напечатаны, и читается она как
есть. Подпись стрелки, которая упирается в полосу, не имеет права ложиться на
саму полосу — если слева от наконечника до края поля места не хватает, подпись
уходит над её верхней гранью.

Проверка прав та же, что у JSON-плана: без права `view` файл не отдаём.
"""
from __future__ import annotations

import io
import math
import os
from datetime import date, datetime, time, timedelta

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from sqlalchemy.orm import Session

from ..models import Berth, VesselPlacement
from .vessels import (
    PARK_CODE,
    SLOT_HOURS,
    apply_reberthing_state,
    length_cells,
    placements_in_period,
    slots_in_range,
    span_of,
)

# Палитра грузов обязана совпадать с фронтендом (frontend/src/vessels.ts):
# один груз — один цвет и на экране, и на бумаге.
PALETTE = ["#2f9bff", "#12b886", "#ffb020", "#9775fa", "#ff5c5c", "#f06595"]
NEUTRAL = "#55677d"

MM = 72 / 25.4  # точек в миллиметре

# Шрифт с кириллицей: стандартные Helvetica/Times в PDF её не рисуют — вместо
# «СВЕТЛОГОРСК» в файле остались бы пустые места.
# Первый кандидат — DejaVuSans из app/assets (свободная лицензия, см. там же
# README): в Docker-образе python:3.12-slim своих шрифтов нет, а ставить их через
# apt — значит сделать сборку зависимой от зеркала и от того, какая версия
# шрифта лежит в репозитории Debian. Системные пути дальше по списку — чтобы в
# dev файл собирался, даже если файл из app/assets не подложили.
_FONT_CANDIDATES = [
    ("DejaVu", os.path.join(os.path.dirname(__file__), "..", "assets", "DejaVuSans.ttf")),
    ("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ("Arial", "C:/Windows/Fonts/arial.ttf"),
    ("Arial", "/System/Library/Fonts/Supplemental/Arial.ttf"),
]
_FONT: str | None = None


def font_name() -> str:
    """Зарегистрировать шрифт с кириллицей (один раз на процесс)."""
    global _FONT
    if _FONT:
        return _FONT
    for name, path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
            except Exception:  # файл есть, но не читается — пробуем следующий
                continue
            _FONT = name
            return _FONT
    _FONT = "Helvetica"  # без кириллицы: лучше кривой текст, чем отсутствие файла
    return _FONT


def cargo_color(cargo: str | None) -> str:
    """Цвет по грузу — та же функция, что в фронтенде (hash % 997)."""
    key = (cargo or "").strip().upper()
    if not key:
        return NEUTRAL
    h = 0
    for ch in key:
        h = (h * 31 + ord(ch)) % 997
    return PALETTE[h % len(PALETTE)]


def bar_color(p: VesselPlacement) -> str:
    """Цвет полосы: ручной приоритетнее автораскраски по грузу."""
    return p.color or cargo_color(p.cargo)


def text_on(hex_color: str) -> str:
    """Подпись на светлой полосе тёмная, на тёмной — белая: читаемость и на печати."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#101c2c" if (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.5 else "#ffffff"


def _clip(text: str, width_pt: float, size: float) -> str:
    """Обрезать подпись по ширину ячейки, иначе текст залезет на соседнее судно."""
    f = font_name()
    if pdfmetrics.stringWidth(text, f, size) <= width_pt:
        return text
    while text and pdfmetrics.stringWidth(text + "…", f, size) > width_pt:
        text = text[:-1]
    return (text + "…") if text else ""


def _chain_steps(items: list[VesselPlacement]) -> dict[int, int]:
    """Порядковые номера строк в цепочках перешвартовок — для подписей «шаг N→M».

    Цепочка идёт по moved_from_id: корень — строка без него, дальше — копии в
    ширину (у оригинала их может быть несколько, порядок — по дате старта).
    Лист разрезан по причалам и суткам, но номера от этого меняться не имеют
    права: их считают по постановкам периода — тем же, что нумерует экран
    (`chainSteps` в frontend/src/vessels.ts). Копия, чей оригинал в лист не
    попал, обязана нести свой настоящий номер: только по нему распечатку
    сводят с экраном и между листами.
    """
    by = {p.id: p for p in items}
    kids: dict[int, list[VesselPlacement]] = {}
    for p in items:
        if p.moved_from_id and p.moved_from_id in by:
            kids.setdefault(p.moved_from_id, []).append(p)
    order: dict[int, int] = {}
    roots = [p for p in items if not (p.moved_from_id and p.moved_from_id in by)]
    roots.sort(key=lambda p: (p.starts_at, p.id))
    for root in roots:
        if root.id in order:
            continue
        seq, i, n = [root], 0, 0
        while i < len(seq):
            node = seq[i]
            i += 1
            if node.id in order:
                continue
            n += 1
            order[node.id] = n
            seq.extend(sorted(kids.get(node.id, []), key=lambda p: (p.starts_at, p.id)))
    for p in items:  # на случай цикла по moved_from_id — остаток тоже нумеруем
        if p.id not in order:
            order[p.id] = len(order) + 1
    return order


def _bar_clip(p: VesselPlacement, origin: datetime, day_from: int, day_to: int
             ) -> tuple[int, int, bool, bool] | None:
    """Слоты полосы внутри кадра: (левый, правый) либо None, если в кадр она не попала.

    Тот же расчёт, что делает `draw_block`, — по нему лист решает, напечатана ли
    полоса вообще. Расхождение означало бы либо стрелку в никуда, либо стрелку,
    которая исчезла, хотя полоса на листе стоит. Возвращает (левый, правый,
    обрезан ли левый край, обрезан ли правый).
    """
    times = slots_in_range(p.starts_at, p.effective_ends_at or p.ends_at)
    if not times:
        return None
    first_slot = int((times[0] - origin).total_seconds() // 3600 // 6)
    left = max(first_slot, day_from * len(SLOT_HOURS))
    right = min(first_slot + len(times), (day_to + 1) * len(SLOT_HOURS))
    if right <= left:
        return None
    return left, right, first_slot < left, first_slot + len(times) > right


def _berth_closure(rows: list[VesselPlacement], selected: set[int], park_id: int) -> set[int]:
    """Транзитивное замыкание фильтра причалов вдоль цепочек перешвартовок.

    Фильтр причалов присылает фронт — это то, что сейчас в кадре сетки. Один
    проход по цепочке её не домыкает: при «только РП-4» причал самого дальнего
    оригинала терялся, и лист печатал полосу без второй половины связи. Домыкаем
    в обе стороны связи и до упора: половина стрелки не имеет права печататься
    без второй, если вторую ещё можно напечатать.
    """
    keep = set(selected) | {park_id}  # «Ожидание» не причал: его чипы печатаются всегда
    by = {p.id: p for p in rows}
    for _ in range(len(rows) + 2):
        grown = set(keep)
        for p in rows:
            src = by.get(p.moved_from_id) if p.moved_from_id else None
            if src is None:
                continue
            if p.berth_id in keep or src.berth_id in keep:
                grown.update((p.berth_id, src.berth_id))
        if grown == keep:
            break
        keep = grown
    return keep


def _arrow_plan(
    links: list[tuple[int, int]],
    printed: dict[int, set[int]],
    chips: set[int],
    code_of: dict[int, str],
    steps: dict[int, int],
) -> list[tuple[str, int, int, int, str]]:
    """Что и где рисовать из каждой стрелки цепочки — одно решение на весь лист.

    Ветки «целая стрелка», «огрызок у полосы», «огрызок у чипа» были разбросаны
    по `draw_block` и `waiting_section`, и связь, у которой в кадре не осталось
    ни одной полосы, не получала ни одной ветки — стрелка молча исчезала с бумаги.
    Здесь решение принимается один раз для всех случаев, а рисовальщик исполняет
    дескриптор `(вид, лист, оригинал, копия, подпись)`.

    `links` — связи «оригинал → копия», у которых ещё есть стрелка; `printed[id]`
    — листы, на которых напечатан конец связи (полоса или чип); нет ключа — на
    бумаге конца нет. Виды `bar2bar` и `chip2chip` — целая стрелка, обе половины
    которой напечатаны на одном листе; `exit` — огрызок полосы, уходящий вниз в
    край её блока, `entry` — входящий в полосу со стороны шапки, `entry-below` —
    входящий в полосу снизу, из чипа «Ожидания» этого же листа; `chip-exit`/
    `chip-entry` — огрызок у чипа, растущий вверх от его верхней грани.

    Целой линии `bar ↔ chip` не бывает ни при каком раскладе: чип лежит в самом
    низу листа, под всеми причалами, и такая линия перерезала бы план. Вместо
    неё — два огрызка, по одному у каждого конца, и каждый подписан тем же
    «шаг N→M»: огрызок полосы смотрит на чип («→ Ожидание»), огрызок чипа — на
    причал («от РП-6»). У огрызков, уехавших на другой лист, к подписи
    добавляется «· лист N» (или «· вне кадра»), как и раньше.

    Чип без второй половины связи огрызка НЕ получает: карман «Ожидание»
    печатают на каждом листе независимо от кадра, и стрелка от чипа в не
    напечатанное место смотрит в пустоту. Полоса такого себе не позволяет —
    она стоит в кадре, и её связь с тем, чего в кадре нет, читателю сказать
    о чём-то должна.

    """
    def label(src_id: int, dst_id: int) -> str:
        """Единая подпись шага: один формат у всех стрелок и на всех листах."""
        return f"шаг {steps.get(src_id, '?')}→{steps.get(dst_id, '?')}"

    def elsewhere(other_pages: set[int]) -> str:
        """Куда смотрит огрызок: на другой лист или за пределы напечатанного."""
        return f"лист {min(other_pages) + 1}" if other_pages else "вне кадра"

    out: list[tuple[str, int, int, int, str]] = []
    for src_id, dst_id in links:
        src_pages, dst_pages = printed.get(src_id, set()), printed.get(dst_id, set())
        both = sorted(src_pages & dst_pages)
        src_chip, dst_chip = src_id in chips, dst_id in chips
        if both:
            if src_chip != dst_chip:
                # один конец — чип «Ожидания». Линия от него к полосе идёт через
                # все блоки причалов этого листа и читается как перечёркнутый
                # план, а не как «судно ушло ждать». Связь рисуется двумя
                # огрызками: каждый остаётся внутри своего блока.
                for pg in both:
                    if src_chip:  # «Ожидание» → полоса: огрызок чипа уходит вверх
                        out.append(("chip-exit", pg, src_id, dst_id,
                                    f"{label(src_id, dst_id)} → {code_of.get(dst_id, '?')}"))
                        out.append(("entry-below", pg, src_id, dst_id,
                                    f"{label(src_id, dst_id)} от {code_of.get(src_id, '?')}"))
                    else:  # полоса → «Ожидание»: огрызок полосы идёт вниз
                        out.append(("exit", pg, src_id, dst_id,
                                    f"{label(src_id, dst_id)} → {code_of.get(dst_id, '?')}"))
                        out.append(("chip-entry", pg, src_id, dst_id,
                                    f"{label(src_id, dst_id)} от {code_of.get(src_id, '?')}"))
                continue
            # обе половины — полосы (или оба чипа) на одном листе: стрелка целиком
            out += [("chip2chip" if src_chip else "bar2bar", pg, src_id, dst_id,
                     label(src_id, dst_id)) for pg in both]
            continue
        if src_pages:  # оригинал напечатан, копия — нет: огрызок «уходит»
            # Чип «Ожидания» печатается на каждом листе сам по себе, поэтому
            # стрелка из него «в никуда» — шум, а не информация: когда второй
            # конец связи не напечатан вовсе, у читателя нет ни одной полосы,
            # к которой огрызок можно примерить, и он смотрит в пустоту под
            # причалами. Если же половина связи лежит на другом листе
            # (`dst_pages` не пуст) — огрызок нужен и подписан «· лист N».
            if src_chip and not dst_pages:
                continue
            kind = "chip-exit" if src_chip else "exit"
            out += [(kind, pg, src_id, dst_id,
                     f"{label(src_id, dst_id)} → {code_of.get(dst_id, '?')} · {elsewhere(dst_pages)}")
                    for pg in sorted(src_pages)]
        if dst_pages:  # копия напечатана, оригинал — нет: огрызок «приходит»
            if dst_chip and not src_pages:  # та же причина, что выше
                continue
            kind = "chip-entry" if dst_chip else "entry"
            out += [(kind, pg, src_id, dst_id,
                     f"{label(src_id, dst_id)} от {code_of.get(src_id, '?')} · {elsewhere(src_pages)}")
                    for pg in sorted(dst_pages)]
    return out


def build_plan_pdf(
    db: Session,
    *,
    date_from: date,
    date_to: date,
    day_from: int,
    day_to: int,
    berth_ids: list[int] | None = None,
) -> bytes:
    """Собрать PDF: сутки [day_from, day_to] и причалы из окна просмотра."""
    all_berths = (
        db.query(Berth).filter(Berth.is_active.is_(True))
        .order_by(Berth.sort_order, Berth.code).all()
    )
    # «Ожидание» — не причал: на бумаге это отдельный список ждущих судов,
    # а не шкала времени. Его строки грузим всегда — даже когда карман не
    # попал в присланный фронтом список причалов: от его чипов идут стрелки
    park = next((b for b in all_berths if b.code == PARK_CODE), None)
    days = (date_to - date_from).days + 1
    day_from = max(0, min(day_from, days - 1))
    day_to = max(day_from, min(day_to, days - 1))
    window_days = list(range(day_from, day_to + 1))
    win_slots = len(window_days) * len(SLOT_HOURS)
    origin = datetime.combine(date_from, datetime.min.time())

    placements_all = (
        db.query(VesselPlacement)
        .filter(VesselPlacement.berth_id.in_([b.id for b in all_berths] or [-1]))
        .all()
    )
    # дата перестановки наступила — на бумаге судно уже «передвинуто»:
    # оригинал обрезаем на старте копии, как на экране. Печать прошлого периода —
    # состояние на последний день листа: «как тогда», с несостоявшимися стрелками
    as_of = min(datetime.now(), datetime.combine(date_to, time.max))
    apply_reberthing_state(db, placements_all, now=as_of)

    # Постановки периода — ровно тот набор, что отдаёт сетка, и именно по нему
    # экран считает номера шагов. PDF нумерует так же: по полным цепочкам, а не
    # по тому, что влезло в сутки кадра и в фильтр причалов. Иначе копия без
    # оригинала становится корнем, и там, где экран пишет «шаг 4→5», бумага писала
    # «шаг 3→4» — а номер является опознавательным знаком стрелки, по нему
    # распечатку и сводят.
    chain_rows = placements_in_period(db, date_from=date_from, date_to=date_to)
    apply_reberthing_state(db, chain_rows, now=as_of)
    chain_by = {p.id: p for p in chain_rows}  # связь без оригинала гаснет, как на экране
    park_id = park.id if park else -1

    # стрелки не обрезаем: если копия (или её оригинал) осталась на другом
    # причале — берём причал пары целиком, иначе лист показывал бы чип без
    # стрелки, а полоса без начала. Замыкание — транзитивное и в обе стороны
    # связи: цепочка из четырёх причалов печатается четырьмя, даже если в кадре один
    if berth_ids is not None:
        keep = _berth_closure(chain_rows, set(berth_ids), park_id)
        berths = [b for b in all_berths if b is not park and b.id in keep]
    else:
        berths = [b for b in all_berths if b is not park]
    keep_ids = {b.id for b in berths} | ({park.id} if park else set())
    placements = [p for p in placements_all if p.berth_id in keep_ids]


    by_berth: dict[int, list[VesselPlacement]] = {}
    for p in placements:
        by_berth.setdefault(p.berth_id, []).append(p)

    # ── раскладка: альбом A4, поля по 8 мм, лист сжат по высоте ─────────────
    # Листы печатают по одному, а стрелка, чьи концы разъехались по листам,
    # превращается в огрызки «лист N» и читается хуже. Поэтому вертикаль
    # экономится, а не переносится: одна шапка суток на лист (это шапка
    # таблицы, а не шапка причала), уплотнённая строка метража, маленький
    # зазор между блоками и право метровой шкале сжиматься до пола.
    page_w_pt, page_h_pt = landscape(A4)
    margin = 8 * MM
    left_col = 34 * MM          # колонка причала: код, осадка, метраж
    field_w = page_w_pt - 2 * margin - left_col
    slot_w = field_w / win_slots
    head_h = 26                  # шапка суток: один раз на лист, над первым блоком
    row_head = 10                # строка метража причала: шифр, длина, осадка
    block_gap = 3                # зазор между блоками причалов
    CELL_H_TOP = 10              # 10 точек на 10 м причала — ровно 1 точка на метр
    CELL_H_MIN = 3.5             # ниже подпись судна в полосе не читается
    # «Ожидание» — не причал: отдельный список ждущих судов внизу листа, в три
    # колонки; размеры чипов — общие переменные, их же читает waiting_section
    # «Ожидание» — очередь, а не список навсегда: судно ждёт причал ровно до
    # того момента, как начнётся его плановое перемещение. Кадр решает, кто в
    # списке: если печатаемые сутки захватили (или перешагнули) начало той
    # постановки, куда перемещение ведёт, судно свой причал уже получило — и на
    # этом листе оно не ждёт. Чип уходит из списка, а вместе с ним и обе
    # стрелки: тыкать огрызком в не напечатанный карман смысла нет — его не
    # напечатали не потому, что он за краем кадра, а потому что очередь уже
    # неактуальна.
    win_end = origin + timedelta(days=day_to + 1)  # последние сутки кадра (не включая)
    moved_from = {p.moved_from_id: p for p in chain_rows
                  if p.moved_from_id and not p.committed}
    waiting = sorted(by_berth.get(park.id, []), key=lambda p: p.vessel) if park else []
    stale = {p.id for p in waiting
             if (nxt := moved_from.get(p.id)) is not None and nxt.starts_at < win_end}
    waiting = [p for p in waiting if p.id not in stale]
    chip_rows = math.ceil(len(waiting) / 3)
    chip_h, chip_gap = 16, 4
    waiting_need = 14 + chip_rows * (chip_h + chip_gap) if waiting else 0
    head_y = page_h_pt - margin - 28        # рабочая строка сразу после page_head()
    avail = head_y - margin - waiting_need  # сколько точек остаётся под блоки причалов
    total_cells = sum(length_cells(b) for b in berths)
    # Хватит ли одного листа, даже когда шкалу сжали до CELL_H_MIN. Считается
    # ЗДЕСЬ, до первого прохода раскладки: оба прохода обязаны вести одни числа.
    fit = ((avail - head_h - (row_head + block_gap) * len(berths)) / total_cells
           if total_cells else CELL_H_TOP)
    # Влезает — берём целое число точек, чтобы линейка метров не спотыкалась.
    # Не влезло и в поле — всё равно печатаем максимально плотно, а лишнее
    # уходит на следующий лист: распасться план умеет ровно как раньше.
    cell_h = (min(CELL_H_TOP, max(CELL_H_MIN, math.floor(fit)))
              if fit >= CELL_H_MIN else CELL_H_MIN)
    squeezed = cell_h < CELL_H_TOP          # шкала мельче обычной — об этом сноска
    # Подписи шапки суток зависят от ширины слота: уже 7 pt — «00 06 12 18»
    # сливаются, сутку меньше 18 pt — даты наезжают друг на друга. Сетка важнее
    # подписей: часы убираем совсем, даты рисуем через одну-две.
    show_hours = slot_w >= 7
    day_step = max(1, math.ceil(18 / (len(SLOT_HOURS) * slot_w)))
    # шаг подписей линейки метров: на сжатом листе метки каждой второй отметки
    # сливаются, поэтому шаг растёт вместе с сжатием (сами отметки остаются).
    # Рост останавливается там, где подписи перестали бы попадать в поле.
    cells_max = max((length_cells(b) for b in berths), default=10)
    ruler_step = 20
    while ruler_step / 10 * cell_h < 9 and ruler_step <= cells_max * 5:
        ruler_step *= 2
    f = font_name()

    def block_need(cells: int, with_head: bool) -> float:
        """Сколько точек по вертикали съедает блок причала: строка метража + поле
        + зазор, а у первого на листе блока ещё и шапка суток (она одна на лист).

        Числа считаются один раз: по ним первый проход раскладывает причалы по
        листам, а второй их рисует. Раскладка не имеет права разойтись с
        рисованием ни на точку, иначе подпись «лист N» посмотрит не туда, где
        лежит второй конец связи.
        """
        return (head_h if with_head else 0) + row_head + cells * cell_h + block_gap

    # ── первый проход раскладки: на каком листе окажется каждый причал ──────
    # Стрелка через границу листа обязана быть осмысленной на обоих концах:
    # на листе начала — «уходит», на листе цели — «приходит». Значит, лист
    # каждой половины нужен до рисования. Здесь рождается таблица листов:
    # (причал, верх блока, шапка суток над ним) плюс вертикаль блока
    # «Ожидание». Второй проход сам ничего не решает — только исполняет эти
    # числа, поэтому лист и раскладка не могут разойтись в принципе.
    berth_page: dict[int, int] = {}  # причал → лист, на котором напечатан его блок
    pages: list[list[tuple[Berth, float, bool]]] = [[]]
    chip_at: dict[int, float] = {}   # лист → вертикаль блока «Ожидание» под ним
    _y, _head = head_y, False        # _head — шапка суток на этом листе уже есть
    for b in berths:
        need = block_need(length_cells(b), not _head)
        if _y - need < margin:
            if waiting:  # чипы допечатываются в низ дописываемого листа
                if _y - waiting_need < margin:  # не влезли — уехали на свой лист
                    pages.append([])
                    _y = head_y
                chip_at.setdefault(len(pages) - 1, _y)
            pages.append([])
            _y, _head = head_y, False
            need = block_need(length_cells(b), True)
        pages[-1].append((b, _y, not _head))
        berth_page[b.id] = len(pages) - 1
        _head = True
        _y -= need
    if waiting:  # финальный блок «Ожидание» после последнего причала
        if _y - waiting_need < margin:
            pages.append([])
            _y = head_y
        chip_at.setdefault(len(pages) - 1, _y)
    chip_pages = set(chip_at)


    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=(page_w_pt, page_h_pt))
    c.setTitle("План постановки судов")
    c.setAuthor("Basis.Indicators")
    page_no = 0  # текущий лист (с нуля); на бумаге подписывается «лист N» с единицы
    anchors: dict[int, tuple[int, float, float, float]] = {}  # id -> (лист, право, центр, лево)
    bar_rect: dict[int, tuple[int, float, float, float, float]] = {}  # (лист, x0, x1, низ, верх)
    chip_rect: dict[int, tuple[int, float, float, float, float]] = {}

    # номера для подписей «шаг N→M» — по постановкам периода, а не по тем, что в листе
    steps = _chain_steps(chain_rows)
    berth_name = {b.id: b.code for b in db.query(Berth).all()}  # шифр в подпись «→ РП-6 · лист 2»
    chips = {p.id for p in chain_rows if p.berth_id == park_id} - stale
    # как подписать конец связи: «Ожидание» — не причал, поэтому словом
    code_of = {
        p.id: ("Ожидание" if p.berth_id == park_id else berth_name.get(p.berth_id, "?"))
        for p in chain_rows
    }
    # где напечатан каждый конец связи: полоса — на листе своего причала и только
    # если она попала в сутки кадра; чип — на каждом листе, куда попал его блок
    # «Ожидание» (чипы печатаются внизу листа, а не в шкале времени)
    printed: dict[int, set[int]] = {}
    for p in placements:
        if p.berth_id == park_id:
            printed[p.id] = set() if p.id in stale else set(chip_pages)
        elif _bar_clip(p, origin, day_from, day_to):
            pg = berth_page.get(p.berth_id)
            if pg is not None:
                printed[p.id] = {pg}
    # связи те же, что рисует экран: без состояния «перешвартовка состоялась»,
    # без копии, у которой в периоде не осталось оригинала, и без планового
    # перемещения, которое в напечатанные сутки уже успело состояться
    links = [(p.moved_from_id, p.id) for p in chain_rows
             if not p.committed and p.moved_from_id and p.moved_from_id in chain_by
             and p.id not in stale and p.moved_from_id not in stale]

    # чем рисовать: огрызок — у своей полосы сразу, целую стрелку «полоса → полоса»
    # — когда обе полосы уже на листе, всё чиповое — внизу листа, где сами чипы.
    stub_bar: dict[int, list[tuple[str, int, int, str]]] = {}
    full_bar: dict[int, list[tuple[int, int, str]]] = {}
    chip_items: dict[int, list[tuple[str, int, int, str]]] = {}
    for kind, pg, src_id, dst_id, text in _arrow_plan(links, printed, chips, code_of, steps):
        if kind in ("exit", "entry", "entry-below"):
            stub_bar.setdefault(src_id if kind == "exit" else dst_id, []).append(
                (kind, src_id, dst_id, text))
        elif kind == "bar2bar":
            full_bar.setdefault(pg, []).append((src_id, dst_id, text))
        else:
            chip_items.setdefault(pg, []).append((kind, src_id, dst_id, text))

    def arrow_straight(x0: float, y0: float, x1: float, y1: float, color, dot: bool = True) -> None:
        """Прямая стрелка «из пункта А в пункт Б»: кружок на старте, линия и
        остриё вдоль самой линии.

        Ломаную «ёлочку» убрали с бумаги заодно с экраном: на сетке из нескольких
        причалов она резала чужие строки, и стрелки разных цепочек путались.
        Прямая коротко читается — начало, конец, направление; остальное дописывает
        подпись «шаг N→M …». `dot=False` — когда у стрелки нет своей полосы-источника
        (приход с другого листа), иначе над ней висел бы бесхозный кружок."
        """
        c.setStrokeColor(color)
        c.setFillColor(color)
        c.setLineWidth(1.2)
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy)
        if ln < 0.5:  # начало и конец слились — рисовать нечего
            return
        ux, uy = dx / ln, dy / ln          # куда смотрит стрелка
        px, py = -uy, ux                   # перпендикуляр: на него разводим перья
        if dot:
            c.circle(x0, y0, 1.6, stroke=0, fill=1)          # «откуда» — как на экране
        c.line(x0, y0, x1 - ux * 5.5, y1 - uy * 5.5)         # до острия не доезжаем
        head = c.beginPath()
        head.moveTo(x1, y1)
        head.lineTo(x1 - ux * 6.5 + px * 3.0, y1 - uy * 6.5 + py * 3.0)
        head.lineTo(x1 - ux * 6.5 - px * 3.0, y1 - uy * 6.5 - py * 3.0)
        head.close()
        c.drawPath(head, stroke=0, fill=1)

    def draw_arrow(a, b, color) -> tuple[float, float, bool]:
        """Оригинал → копия: от ближнего торца полосы до ближнего торца копии —
        та же прямая, что рисует экран."""
        rightward = b[3] >= a[3]
        x0 = a[1] if rightward else a[3]
        x1 = b[3] if rightward else b[1]
        arrow_straight(x0, a[2], x1, b[2], color)
        return x1, b[2], rightward  # куда ткнули наконечник — туда же ставим и подпись

    def arrow_label(text: str, x: float, y: float, color: str, align: str = "left") -> None:
        """Подпись у наконечника стрелки: цвет стрелки + белая подложка.

        Бумага — это сетка, серые выходные и полосы судов: подпись 5.8 pt без
        подложки в них тонет, а именно «лист 2» и «шаг 2→3» связывают стрелки
        через границы листов в читаемую цепочку.
        """
        c.setFont(f, 5.8)
        w = pdfmetrics.stringWidth(text, f, 5.8)
        x0 = x - w if align == "right" else x
        c.setFillColor(HexColor("#ffffff"))
        c.rect(x0 - 1.2, y - 1.4, w + 2.4, 7.8, stroke=0, fill=1)
        c.setFillColor(HexColor(color))
        c.drawString(x0, y, text)

    def arrow_label_at_bar(text: str, x: float, y: float, color: str, br,
                           rightward: bool) -> None:
        """Подпись стрелки, упирающейся в полосу, — так, чтобы не резать саму полосу.

        Обычное место — у наконечника, со стороны, откуда пришла линия. Но если
        слева от наконечника до левого края поля шкалы не хватает места под текст,
        подпись легла бы на полосу и поверх подписи самого судна в ней — именно
        так «шаг 2→3» в своё время и лёг на «2 400 т · 74 м». Тогда подпись уходит
        НАД полосой, над её верхней гранью: там чужих подписей не бывает вовсе,
        и полоса остаётся полосой. `br` — прямоугольник полосы-цели.
        """
        w = pdfmetrics.stringWidth(text, f, 5.8)
        left, right = margin + left_col, page_w_pt - margin
        if rightward:  # остриё в левый торец копии — текст влево от наконечника
            if x - 3 - w >= left:
                arrow_label(text, x - 3, y + 2, color, align="right")
                return
        elif x + 3 + w <= right:  # остриё в правый торец — текст вправо
            arrow_label(text, x + 3, y + 2, color)
            return
        elif x - 3 - w >= left:  # справа тесно, но слева место есть
            arrow_label(text, x - 3, y + 2, color, align="right")
            return
        # места по обе стороны наконечника нет: подпись идёт НАД полосой, над её
        # верхней гранью, — там чужих подписей не бывает в принципе
        arrow_label(text, min(max(br[1], left), right - w), br[4] + 1.5, color)

    def draw_exit_arrow(br, block_bottom: float, color: str, text: str, n: int = 0) -> None:
        """Огрызок «уходит»: от низа полосы к нижнему краю блока причала.

        Блок причала не разрезается между листами, поэтому копия, уехавшая на
        другой лист, оставляет здесь обрывок с подписью, куда именно («→ РП-6 ·
        лист 2» или «→ РП-6 · вне кадра»). Без этого на бумаге перешвартовка
        просто исчезала. Тем же огрызком рисуется и связь с чипом «Ожидания»
        этого же листа («шаг 2→3 → Ожидание»): длинную линию в карман себе
        позволить нельзя. `n` — номер огрызка у этой полосы: у судна может быть
        несколько копий, и их подписи не должны ложиться друг на друга.
        """
        x = (br[1] + br[2]) / 2 + 12 * n
        arrow_straight(x, br[3], x, block_bottom + 0.5, HexColor(color))
        arrow_label(text, x + 4, block_bottom + 1.5 + 8 * n, color)

    def draw_entry_arrow(br, field_top: float, color: str, text: str, n: int = 0) -> None:
        """Стрелка «пришла на лист»: сверху поля, со стороны шапки причала, в полосу.

        Парная половина «уходящей» стрелки: без неё на листе цели стоит полоса,
        откуда судно «взялось неоткуда», и план читается неверно. Приходит оно
        либо с другого листа, либо из постановки, которая в напечатанные сутки
        не попала, — об этом говорит конец подписи. `n` разводит подписи
        нескольких огрызков одной полосы.
        """
        x = (br[1] + br[2]) / 2 + 12 * n
        top = br[4]
        col = HexColor(color)
        if field_top - (top + 6) > 2:  # полоса в самом верху поля — хватит и наконечника
            arrow_straight(x, field_top - 0.5, x, top + 0.5, col, dot=False)
        else:
            # полоса прижалась к шапке — рисуем короткий огрызок над ней
            arrow_straight(x, top + 12, x, top + 0.5, col, dot=False)
        arrow_label(text, x + 4, top + 2 + 8 * n, color)

    def draw_arrival_arrow(br, block_bottom: float, color: str, text: str, n: int = 0) -> None:
        """Огрызок «пришло из Ожидания»: снизу, в нижнюю грань полосы.

        Парная половина огрызка чипа. Полоса и её чип лежат на одном листе, но
        соединять их линией нельзя: она прошла бы через все блоки причалов
        между полосой и карманом. Поэтому у полосы — свой огрызок от нижней
        грани вниз к краю блока, у чипа — свой, вверх от его верхней грани.
        Остриё смотрит в полосу: судно сюда пришло. `n` разводит подписи
        нескольких огрызков одной полосы.
        """
        x = (br[1] + br[2]) / 2 + 12 * n
        arrow_straight(x, block_bottom + 0.5, x, br[3] + 0.5, HexColor(color), dot=False)
        arrow_label(text, x + 4, block_bottom + 1.5 + 8 * n, color)

    def draw_chip_stub(cr, color: str, text: str, first_row: bool, n: int = 0,
                       arrives: bool = False) -> None:
        """Огрызок у чипа «Ожидания»: вверх от его верхней грани.

        У полосы огрызок уходит в край своего блока, но у чипа блока нет: карман
        лежит в самом низу листа, и единственное направление, где огрызок ещё
        читается, — вверх. `arrives=True` — чип есть копия связи (судно пришло
        ждать): остриё смотрит В чип, а хвост торчит вверх, иначе огрызок
        «прихода» неотличим от огрызка «ухода».

        Чипам первой строки огрызок поднимаем через всю строку заголовка, иначе
        подпись легла бы на слово «Ожидание». `n` разводит вверх подписи
        приходящего и уходящего огрызков одного чипа — иначе они сливаются
        в нечитаемое пятно.
        """
        x = (cr[1] + cr[2]) / 2 + 12 * n
        tip = cr[4] + (16 if first_row else 10) + 9 * n
        col = HexColor(color)
        if arrives:  # копия лежит в чипе: остриё в его верхнюю грань, хвост сверху
            arrow_straight(x, tip, x, cr[4] + 0.5, col, dot=False)
        else:
            arrow_straight(x, cr[4], x, tip, col)
        arrow_label(text, x + 4, tip + 1.5, color)

    def header_band(y: float) -> None:
        """Шапка листа: заголовок, окно, период."""
        c.setFont(f, 11)
        c.setFillColor(HexColor("#1b3360"))
        c.drawString(margin, y, "План постановки судов · видимая область")
        c.setFont(f, 8)
        c.setFillColor(HexColor("#42546d"))
        first = date_from + timedelta(days=day_from)
        last = date_from + timedelta(days=day_to)
        c.drawRightString(
            page_w_pt - margin, y,
            f"{first.strftime('%d.%m.%Y')} — {last.strftime('%d.%m.%Y')}"
            f"  ·  {len(window_days)} сут из {days}  ·  {len(berths)} причалов",
        )

    def draw_block(b: Berth, y_top: float) -> float:
        """Один причал: строка метража + поле шкалы. Возвращает нижний край."""
        cells = length_cells(b)
        field_h = cells * cell_h

        # строка причала: шифр, метраж, осадка — уплотнённая (10 точек вместо 16),
        # иначе шесть причалов в лист не встают
        c.setFillColor(HexColor("#f2f5fa"))
        c.setStrokeColor(HexColor("#8fa2bd"))
        c.rect(margin, y_top - row_head, left_col, row_head, stroke=1, fill=1)
        c.rect(margin + left_col, y_top - row_head, field_w, row_head, stroke=1, fill=1)
        c.setFont(f, 7)
        c.setFillColor(HexColor("#101c2c"))
        c.drawString(margin + 3, y_top - row_head + 3, b.code)
        meta = f"{b.length_m} м длины · {cells} ячеек по 10 м" if b.length_m else "метраж не задан · 100 м"
        if b.depth:
            meta = f"{meta}   осадка {str(b.depth).replace('.', ',')} м"
        c.drawString(margin + left_col + 4, y_top - row_head + 3, _clip(meta, field_w - 8, 7))
        y_top -= row_head

        # поле шкалы
        c.setFillColor(HexColor("#ffffff"))
        c.rect(margin + left_col, y_top - field_h, field_w, field_h, stroke=1, fill=1)
        c.rect(margin, y_top - field_h, left_col, field_h, stroke=1, fill=1)

        # выходные — серые полосы, как на экране
        for i, dindex in enumerate(window_days):
            d = date_from + timedelta(days=dindex)
            x = margin + left_col + i * len(SLOT_HOURS) * slot_w
            if d.weekday() >= 5:
                c.setFillColor(HexColor("#eef1f6"))
                c.rect(x, y_top - field_h, len(SLOT_HOURS) * slot_w, field_h, stroke=0, fill=1)

        # границы суток и слотов
        c.setStrokeColor(HexColor("#c3cede"))
        for i in range(len(window_days) + 1):
            x = margin + left_col + i * len(SLOT_HOURS) * slot_w
            c.line(x, y_top - field_h, x, y_top)
        c.setStrokeColor(HexColor("#dde3ec"))
        for i in range(1, len(window_days) * len(SLOT_HOURS)):
            x = margin + left_col + i * slot_w
            c.line(x, y_top - field_h, x, y_top)

        # линейка метров
        c.setFont(f, 6)
        c.setFillColor(HexColor("#566a85"))
        c.setStrokeColor(HexColor("#c3cede"))
        for m in range(10, (cells + 1) * 10, 10):
            y = y_top - (m / 10) * cell_h
            c.line(margin + left_col, y, margin + left_col + field_w, y)
            # последнюю границу (конец причала) не подписываем: подпись висела бы
            # на нижнем крае поля и при плотной ячейке залезала на строку метража;
            # на сжатом листе шаг подписей растёт (ruler_step), сами отметки — нет
            if m % ruler_step == 0 and m < cells * 10:
                c.drawRightString(margin + left_col - 3, y - 2, str(m))

        # полосы судов
        for p in by_berth.get(b.id, []):
            clip = _bar_clip(p, origin, day_from, day_to)  # то же, чем лист решает, печатать ли полосу
            if clip is None:
                continue
            left, right, cut_start, cut_end = clip
            m_from, m_to = span_of(p)
            # длина не задана — span_of отдаёт inf: рисуем до конца причала
            m_to = min(m_to, cells * 10)
            top = y_top - (m_from / 10) * cell_h
            bottom = y_top - (m_to / 10) * cell_h
            x = margin + left_col + (left - day_from * len(SLOT_HOURS)) * slot_w
            w = (right - left) * slot_w - 1.5
            h = max(6, top - bottom - 1.5)
            c.setFillColor(HexColor(bar_color(p)))
            c.setStrokeColor(HexColor("#ffffff"))
            c.roundRect(x, bottom, w, h, 2, stroke=0, fill=1)
            if cut_start or cut_end:
                # пунктир там, где постановка уходит за границу кадра
                c.setDash(1.5, 1.5)
                c.setStrokeColor(HexColor("#ffffff"))
                if cut_start:
                    c.line(x + 1, bottom, x + 1, bottom + h)
                if cut_end:
                    c.line(x + w - 1, bottom, x + w - 1, bottom + h)
                c.setDash()
            # подпись живёт внутри полосы: на сжатом листе полоса бывает ниже
            # подписи, поэтому шрифт подбирается под полосу, а текст наружу неё
            # не выходит (снаружи полосы подписей не бывает вовсе)
            c.setFillColor(HexColor(text_on(bar_color(p))))
            if h >= 18:
                c.setFont(f, 6.5)
                c.drawString(x + 3, bottom + h - 8, _clip(p.vessel, w - 4, 6.5))
                sub = " · ".join(x2 for x2 in (p.volume, f"{p.length_m} м" if p.length_m else None) if x2)
                if sub:
                    c.setFont(f, 5.5)
                    c.drawString(x + 3, bottom + h - 15, _clip(sub, w - 4, 5.5))
            else:
                size = 6.5 if h >= 13 else 5.2
                # полоса ниже подписи — оставляем её пустой: лучше полоса без
                # шифра, чем текст, нарезавший соседние шкалы
                if h >= size + 1.5:
                    c.setFont(f, size)
                    c.drawString(x + 3, bottom + h - size - 1.2, _clip(p.vessel, w - 4, size))
            anchors[p.id] = (page_no, x + w, bottom + h / 2, x)
            bar_rect[p.id] = (page_no, x, x + w, bottom, bottom + h)
            # огрызки своей полосы («лист N» / «вне кадра» / «→ Ожидание»): рисуем
            # сразу — после showPage на лист не вернуться, да и край блока знает
            # только этот блок. Огрызки одной стороны полосы разводятся общим
            # счётчиком: «уходит» и «приходит снизу» живут в одном месте — под полосой.
            bottom_edge = y_top - field_h  # нижний край блока этого причала
            seen: dict[str, int] = {}  # сколько огрызков этой стороны уже вышло
            for kind, src_id, dst_id, text in stub_bar.get(p.id, []):
                zone = "above" if kind == "entry" else "below"
                n = seen.get(zone, 0)
                seen[zone] = n + 1
                color = bar_color(chain_by[dst_id])  # цвет стрелки — цвет копии, как на экране
                if kind == "exit":
                    draw_exit_arrow(bar_rect[p.id], bottom_edge, color, text, n)
                elif kind == "entry-below":
                    draw_arrival_arrow(bar_rect[p.id], bottom_edge, color, text, n)
                else:
                    draw_entry_arrow(bar_rect[p.id], y_top, color, text, n)
        # целые стрелки «полоса → полоса» рисуются после всех блоков листа —
        # там, где обе полосы уже нарисованы (см. второй проход раскладки)

        return y_top - field_h - block_gap

    # ── рисуем: ровно то, что посчитала раскладка, лист за листом ─────────────

    def foot_note() -> None:
        """Сноска под каждым листом: листы распечатывают и по одному.

        В одну строку она не влезала уже до сжатия листа — хвост уходил за правый
        край бумаги, поэтому теперь строк две: про стрелки и про то, чем лист
        собран и почему шкала на нём может быть мельче обычной.
        """
        c.setFont(f, 6)
        c.setFillColor(HexColor("#566a85"))
        c.drawString(margin, margin - 2,
                     "Пунктир — постановка продолжается за границей кадра. Стрелка — "
                     "запланированная перестановка; «лист N» — её продолжение на другом "
                     "листе, «вне кадра» — за пределами напечатанных суток или причалов.")
        c.drawString(margin, margin - 9,
                     "Лист собран по тому, что видно в окне плана."
                     + (" Лист сжат по высоте: метровая шкала мельче обычной, чтобы "
                        "уместиться на один лист." if squeezed else ""))

    def page_head(fresh: bool = False) -> float:
        """Верх листа с шапкой; fresh=True — начать новый лист.

        Первый лист чистый сам (canvas создаётся под пустой), поэтому showPage
        нужен только на продолжении: иначе в файле был бы пустой первый лист.
        """
        nonlocal page_no
        if fresh:
            c.showPage()
            page_no += 1
        top = page_h_pt - margin - 14
        header_band(top)
        return top - 14

    # «Ожидание» — не причал: отдельный список ждущих судов внизу каждого
    # листа: листы печатают по одному, и стрелки должны вести от чипов к
    # копиям этого листа и обратно — в обоих направлениях перешвартовки

    def waiting_section(y: float) -> float:
        """Низ листа: чипы ждущих судов + стрелки между чипами и копиями этого листа.

        Место под блок резервирует раскладка (`chip_at`), а не этот рисовальщик:
        решение «влезли чипы или уехали на свой лист» принято в первом проходе,
        и здесь оно уже не имеет права пересматриваться.
        """
        if not waiting:
            return y
        chip_w = (left_col + field_w - 2 * 4) / 3
        c.setFont(f, 8)
        c.setFillColor(HexColor("#1b3360"))
        c.drawString(margin, y - 9, f"Ожидание — ждут череди: {len(waiting)}")
        y -= 14
        first_row_top = y  # верхняя грань чипов первой строки — по ней решаем, насколько поднимать огрызок
        for i, p in enumerate(waiting):
            x = margin + (i % 3) * (chip_w + chip_gap)
            cy = y - (i // 3) * (chip_h + chip_gap)
            color = bar_color(p)
            c.setFillColor(HexColor(color))
            if p.at_other_terminal:
                c.setStrokeColor(HexColor("#b3312a"))
                c.setLineWidth(1.2)
            else:
                c.setStrokeColor(HexColor("#ffffff"))
                c.setLineWidth(0.6)
            c.roundRect(x, cy - chip_h, chip_w, chip_h, 2, stroke=1, fill=1)
            c.setFillColor(HexColor(text_on(color)))
            c.setFont(f, 6.5)
            c.drawString(x + 3, cy - 6.5, _clip(p.vessel, chip_w - 6, 6.5))
            sub = " · ".join(t for t in (p.cargo, p.volume, p.note) if t)
            if sub:
                c.setFont(f, 5)
                c.drawString(x + 3, cy - 12.5, _clip(sub, chip_w - 6, 5))
            chip_rect[p.id] = (page_no, x, x + chip_w, cy - chip_h, cy)
        # всё, что рисует чип: огрызки вверх от его верхней грани — и «лист N»/
        # «вне кадра», и связь с полосой этого листа. Целой линии «полоса ↔ чип»
        # на бумаге не бывает: карман лежит под всеми причалами, и линия до него
        # перерезала бы весь план — вместо неё огрызок у каждого конца связи.
        chip_stub: dict[int, int] = {}  # чип → сколько его огрызков уже нарисовано
        for kind, src_id, dst_id, text in chip_items.get(page_no, []):
            color = bar_color(chain_by[dst_id])  # цвет стрелки — цвет копии, как на экране
            if kind == "chip2chip":  # чип → чип: над верхними гранями, чтобы не резать чипы
                a, b = chip_rect.get(src_id), chip_rect.get(dst_id)
                if not (a and b and a[0] == b[0] == page_no):
                    continue
                x0, x1 = (a[1] + a[2]) / 2, (b[1] + b[2]) / 2
                arrow_straight(x0, a[4] + 6, x1, b[4] + 6, HexColor(color))
                arrow_label(text, x1 + 4, b[4] + 7, color)
            else:  # «chip-exit» / «chip-entry»: огрызок вверх от верхней грани чипа
                cr = chip_rect.get(src_id if kind == "chip-exit" else dst_id)
                if not (cr and cr[0] == page_no):
                    continue
                key = src_id if kind == "chip-exit" else dst_id
                n = chip_stub.get(key, 0)   # у чипа может быть два огрызка: откуда и куда
                chip_stub[key] = n + 1
                draw_chip_stub(cr, color, text, cr[4] == first_row_top, n,
                               arrives=(kind == "chip-entry"))
        return y - chip_rows * (chip_h + chip_gap)

    # ── второй проход: рисуем ровно то, что посчитал первый ────────────────
    # Порядок листов задаёт раскладка, а не рисовальщик: лист, на который что-то
    # не влезло, известен заранее, поэтому подпись «лист N» смотрит туда, где
    # конец связи действительно напечатан.

    def day_header(y: float) -> None:
        """Шапка суток — ОДНА на лист, над первым его блоком: это шапка таблицы.

        Те же 26 точек над каждым причалом и отправляли шесть причалов на второй
        лист: даты окна от причала не зависят, а 6 × 26 точек — это половина
        сетки одного причала, выкинутая ради повтора одних и тех же дат.
        """
        c.setFillColor(HexColor("#f2f5fa"))
        c.setStrokeColor(HexColor("#8fa2bd"))
        c.rect(margin, y - head_h, left_col, head_h, stroke=1, fill=1)
        c.setFont(f, 7)
        c.setFillColor(HexColor("#42546d"))
        c.drawString(margin + 3, y - head_h + 12, "причал / метры")
        for i, dindex in enumerate(window_days):
            d = date_from + timedelta(days=dindex)
            x = margin + left_col + i * len(SLOT_HOURS) * slot_w
            dw = len(SLOT_HOURS) * slot_w
            c.setFillColor(HexColor("#e8eef7" if d.weekday() < 5 else "#dfe6f0"))
            c.rect(x, y - head_h, dw, head_h, stroke=1, fill=1)
            c.setFillColor(HexColor("#101c2c"))
            c.setFont(f, 7)
            if i % day_step == 0:
                c.drawCentredString(x + dw / 2, y - 10, d.strftime("%d.%m"))
            c.setFont(f, 5.5)
            c.setFillColor(HexColor("#566a85"))
            if show_hours:
                for j, h in enumerate(SLOT_HOURS):
                    c.drawCentredString(x + (j + 0.5) * slot_w, y - 19, f"{h:02d}")

    for n, blocks in enumerate(pages):
        page_head(fresh=bool(n))   # шапка листа и сноска — на каждом листе
        for b, y_top, with_head in blocks:
            if with_head:
                day_header(y_top)
            draw_block(b, y_top - (head_h if with_head else 0))
        # целые стрелки «полоса → полоса» этого листа: пара соединяется, только
        # когда обе полосы уже нарисованы
        for src_id, dst_id, text in list(full_bar.get(n, [])):
            a, dst = anchors.get(src_id), anchors.get(dst_id)
            if not (a and dst and a[0] == dst[0] == page_no):
                continue
            full_bar[n].remove((src_id, dst_id, text))
            color = bar_color(chain_by[dst_id])
            x1, y1, rightward = draw_arrow(a, dst, HexColor(color))
            arrow_label_at_bar(text, x1, y1, color, bar_rect[dst_id], rightward)
        if n in chip_at:   # низ листа: чипы + стрелки к полосам этого же листа
            waiting_section(chip_at[n])
        foot_note()
    c.save()
    return buf.getvalue()
