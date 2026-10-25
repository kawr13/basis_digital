"""PDF плана постановки судов — ровно тот кусок, который виден на экране.

Печать браузера для этой задачи не подошла: она печатает страницу кабинета, а
диспетчеру нужен файл с конкретным куском плана — те сутки и причалы, что сейчас
в окне сетки. Поэтому PDF рисуем сами, векторно: текст остаётся текстом
(кириллица, поиск, копирование), линии — линиями, а не пиксельной картинкой.

Что попадает в лист решает фронтенд: он знает, что в кадре, и присылает `day_from`
/ `day_to` (сутки окна) и `berth_ids` (причалы окна). Сервер только рисует.

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
    apply_reberthing_state(db, placements_all, now=min(datetime.now(), datetime.combine(date_to, time.max)))

    # стрелки не обрезаем: если копия (или её оригинал) осталась на другом
    # причале — берём причал пары целиком, иначе лист показывал бы чип без
    # стрелки, а полоса без начала
    if berth_ids is not None:
        keep = set(berth_ids)
        by_id = {p.id: p for p in placements_all}
        park_id = park.id if park else -1
        for p in placements_all:
            if p.moved_from_id and not p.committed and p.moved_from_id in by_id:
                q = by_id[p.moved_from_id]
                # чип «Ожидания» рисуем всегда — значит и причал его копии обязан
                # быть на листе, иначе стрелка повиснет без конца
                if p.berth_id in keep or q.berth_id in keep or q.berth_id == park_id:
                    keep.update((p.berth_id, q.berth_id))
        berths = [b for b in all_berths if b is not park and b.id in keep]
    else:
        berths = [b for b in all_berths if b is not park]
    keep_ids = {b.id for b in berths} | ({park.id} if park else set())
    placements = [p for p in placements_all if p.berth_id in keep_ids]


    by_berth: dict[int, list[VesselPlacement]] = {}
    for p in placements:
        by_berth.setdefault(p.berth_id, []).append(p)

    # ── раскладка: альбом A4, полей по 8 мм ─────────────────────────────────
    page_w_pt, page_h_pt = landscape(A4)
    margin = 8 * MM
    left_col = 34 * MM          # колонка причала: код, осадка, метраж
    field_w = page_w_pt - 2 * margin - left_col
    slot_w = field_w / win_slots
    row_head = 16                # строка с метражом причала
    head_h = 26                  # шапка: дата + слоты
    # 10 точек на 10 м причала — ровно 1 точка на метр, шкала читается без
    # пересчёта. Но если сетка всех причалов вместе с низом листа влезает и
    # при меньшей ячейке — сжимаем: листы печатают целиком, а не разрезают.
    # Ниже 6 pt не идём: полосы короче 13 м остались бы без подписи.
    waiting = sorted(by_berth.get(park.id, []), key=lambda p: p.vessel) if park else []
    waiting_h = 14 + math.ceil(len(waiting) / 3) * 20 if waiting else 0
    avail = page_h_pt - 2 * margin - 30 - waiting_h - 10
    total_cells = sum(length_cells(b) for b in berths)
    fit = ((avail - (head_h + row_head + 6) * len(berths)) / total_cells) if total_cells else 10
    cell_h = max(6, min(10, math.floor(fit)))
    # Подписи шапки суток зависят от ширины слота: уже 7 pt — «00 06 12 18»
    # сливаются, сутку меньше 18 pt — даты наезжают друг на друга. Сетка важнее
    # подписей: часы убираем совсем, даты рисуем через одну-две.
    show_hours = slot_w >= 7
    day_step = max(1, math.ceil(18 / (len(SLOT_HOURS) * slot_w)))
    row_head = 16                # строка с метражом причала
    head_h = 26                  # шапка: дата + слоты
    f = font_name()

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=(page_w_pt, page_h_pt))
    c.setTitle("План постановки судов")
    c.setAuthor("Basis.Indicators")
    page_no = 0  # текущий лист: стрелки рисуем только внутри одного листа
    anchors: dict[int, tuple[int, float, float, float]] = {}  # id -> (лист, право, центр, лево)
    bar_rect: dict[int, tuple[int, float, float, float, float]] = {}  # (лист, x0, x1, низ, верх)
    chip_rect: dict[int, tuple[int, float, float, float, float]] = {}

    copies_by_src: dict[int, list[VesselPlacement]] = {}
    for p in placements:
        if p.moved_from_id:
            copies_by_src.setdefault(p.moved_from_id, []).append(p)
    drawn_arrows: set[int] = set()  # id копии: стрелка рисуется ровно один раз

    def draw_arrow(a, b, color) -> None:
        """Та же «ёлочка», что на экране: к копии подходим с её ближней стороны."""
        c.setStrokeColor(color)
        c.setFillColor(color)
        c.setLineWidth(1.2)
        rightward = b[3] >= a[3]
        x0 = a[1] if rightward else a[3]
        x1 = b[3] if rightward else b[1]
        y0, y1 = a[2], b[2]
        mid = (x0 + x1) / 2
        d = 1 if rightward else -1
        c.line(x0, y0, mid, y0)
        c.line(mid, y0, mid, y1)
        c.line(mid, y1, x1 - 3 * d, y1)
        head = c.beginPath()
        head.moveTo(x1, y1)
        head.lineTo(x1 - 5.5 * d, y1 + 3)
        head.lineTo(x1 - 5.5 * d, y1 - 3)
        head.close()
        c.drawPath(head, stroke=0, fill=1)

    def draw_chip_arrow(cr, br, color) -> None:
        """Стрелка «из буфера»: вверх от верхней грани чипа. Горизонтальная
        «ёлочка» от края чипа прилипла бы к торцу полосы: чип — треть листа."""
        c.setStrokeColor(color)
        c.setFillColor(color)
        c.setLineWidth(1.2)
        x0 = (cr[1] + cr[2]) / 2
        y0 = cr[4]
        by = (br[3] + br[4]) / 2
        if br[1] - 2 <= x0 <= br[2] + 2:
            # полоса прямо над чипом: стрелка вверх в её нижний край
            c.line(x0, y0, x0, br[3])
            head = c.beginPath()
            head.moveTo(x0, br[3])
            head.lineTo(x0 - 3, br[3] + 5.5)
            head.lineTo(x0 + 3, br[3] + 5.5)
            head.close()
            c.drawPath(head, stroke=0, fill=1)
        else:
            rightward = x0 < br[1]  # чип слева от полосы — заходим в левый торец
            d = 1 if rightward else -1
            xe = br[1] if rightward else br[2]
            c.line(x0, y0, x0, by)
            c.line(x0, by, xe - 3 * d, by)
            head = c.beginPath()
            head.moveTo(xe, by)
            head.lineTo(xe - 5.5 * d, by + 3)
            head.lineTo(xe - 5.5 * d, by - 3)
            head.close()
            c.drawPath(head, stroke=0, fill=1)

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

        # строка причала
        c.setFillColor(HexColor("#f2f5fa"))
        c.setStrokeColor(HexColor("#8fa2bd"))
        c.rect(margin, y_top - row_head, left_col, row_head, stroke=1, fill=1)
        c.rect(margin + left_col, y_top - row_head, field_w, row_head, stroke=1, fill=1)
        c.setFont(f, 8)
        c.setFillColor(HexColor("#101c2c"))
        c.drawString(margin + 3, y_top - row_head + 5, b.code)
        meta = f"{b.length_m} м длины · {cells} ячеек по 10 м" if b.length_m else "метраж не задан · 100 м"
        if b.depth:
            meta = f"{meta}   осадка {str(b.depth).replace('.', ',')} м"
        c.drawString(margin + left_col + 4, y_top - row_head + 5, _clip(meta, field_w - 8, 8))
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
            # на нижнем крае поля и при плотной ячейке залезала на шапку следующего
            if m % 20 == 0 and m < cells * 10:
                c.drawRightString(margin + left_col - 3, y - 2, str(m))

        # полосы судов
        for p in by_berth.get(b.id, []):
            end_at = p.effective_ends_at or p.ends_at
            times = slots_in_range(p.starts_at, end_at)
            if not times:
                continue
            first_slot = int((times[0] - origin).total_seconds() // 3600 // 6)
            left = max(first_slot, day_from * len(SLOT_HOURS))
            right = min(first_slot + len(times), (day_to + 1) * len(SLOT_HOURS))
            if right <= left:
                continue
            cut_start = first_slot < left
            cut_end = first_slot + len(times) > right
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
            label = _clip(p.vessel, w - 4, 6.5)
            c.setFillColor(HexColor(text_on(bar_color(p))))
            c.setFont(f, 6.5)
            if h >= 18:
                c.drawString(x + 3, bottom + h - 8, label)
                sub = " · ".join(x2 for x2 in (p.volume, f"{p.length_m} м" if p.length_m else None) if x2)
                if sub:
                    c.setFont(f, 5.5)
                    c.drawString(x + 3, bottom + h - 15, _clip(sub, w - 4, 5.5))
            elif h >= 8:
                c.drawString(x + 3, bottom + h - 7, label)
            anchors[p.id] = (page_no, x + w, bottom + h / 2, x)
            bar_rect[p.id] = (page_no, x, x + w, bottom, bottom + h)
            # стрелку рисуем сразу: после showPage на лист не вернуться —
            # пара соединяется, только когда обе полосы уже на текущем листе
            pairs: list[tuple[int, VesselPlacement]] = []
            if p.moved_from_id and not p.committed:
                pairs.append((p.moved_from_id, p))
            for q in copies_by_src.get(p.id, []):
                if not q.committed:
                    pairs.append((p.id, q))
            for src_id, cp in pairs:
                if cp.id in drawn_arrows:
                    continue
                a, b = anchors.get(src_id), anchors.get(cp.id)
                if a and b and a[0] == b[0] == page_no:
                    drawn_arrows.add(cp.id)
                    draw_arrow(a, b, HexColor(bar_color(cp)))

        return y_top - field_h - 6

    # ── рисуем: сколько причалов влезает в лист, остальное — на следующий ──────

    def foot_note() -> None:
        """Сноска под каждым листом: листы распечатывают и по одному."""
        c.setFont(f, 6)
        c.setFillColor(HexColor("#566a85"))
        c.drawString(margin, margin - 2,
                     "Пунктир — постановка продолжается за границей кадра. Стрелка — "
                     "запланированная перестановка. Лист собран по тому, что видно в окне плана.")

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
    # листа: листы печатают по одному, и стрелки от чипов должны вести к
    # копиям перестановок на этом же листе

    def waiting_section(y: float) -> float:
        """Низ листа: чипы ждущих судов + стрелки от чипов к копиям этого листа."""
        if not waiting:
            return y
        chip_w = (left_col + field_w - 2 * 4) / 3
        chip_h, gap = 16, 4
        rows = math.ceil(len(waiting) / 3)
        if y - (14 + rows * (chip_h + gap)) < margin:
            foot_note()
            y = page_head(fresh=True)
        c.setFont(f, 8)
        c.setFillColor(HexColor("#1b3360"))
        c.drawString(margin, y - 9, f"Ожидание — ждут череди: {len(waiting)}")
        y -= 14
        for i, p in enumerate(waiting):
            x = margin + (i % 3) * (chip_w + gap)
            cy = y - (i // 3) * (chip_h + gap)
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
        # стрелки «из буфера»: вверх от чипа к копии; рисуем только когда
        # копия попала на тот же лист, что и чип
        for q in placements:
            if not q.moved_from_id or q.committed or q.id in drawn_arrows:
                continue
            a, b = chip_rect.get(q.moved_from_id), bar_rect.get(q.id)
            if a and b and a[0] == b[0] == page_no:
                drawn_arrows.add(q.id)
                draw_chip_arrow(a, b, HexColor(bar_color(q)))
        return y - rows * (chip_h + gap)

    y = page_head()
    for b in berths:
        # запас считаем вместе с шапкой суток: без неё блок «влезал» в остаток
        # страницы и упирался сеткой в нижнее поле
        need = head_h + row_head + length_cells(b) * cell_h + 6
        if y - need < margin:
            y = waiting_section(y)  # низ допечатываемого листа: чипы + стрелки
            foot_note()
            y = page_head(fresh=True)
        # шапка суток — своя у каждого причала: лист с одним причалом читают
        # отдельно от остальных
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
        y -= head_h
        y = draw_block(b, y)

    y = waiting_section(y)
    foot_note()
    c.save()
    return buf.getvalue()
