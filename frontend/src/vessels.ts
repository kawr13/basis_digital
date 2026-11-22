/**
 * Расчёт для сетки «Постановка судов».
 *
 * Сутки разбиты на 4 слота по 6 часов — ровно как в бумажном плане причалов.
 * Всё позиционирование полос идёт в слотах, поэтому сетка одинаково выглядит
 * и на неделю, и на три месяца.
 */
import type { Berth, Placement } from "./api";

export const SLOT_HOURS = [0, 6, 12, 18];
export const SLOT_MS = 6 * 60 * 60 * 1000;

/** Подпись слота: 00-06, 06-12, 12-18, 18-00. */
export const slotLabel = (hour: number) =>
  `${String(hour).padStart(2, "0")}-${String((hour + 6) % 24).padStart(2, "0")}`;

/** Локальная дата в формате YYYY-MM-DD. toISOString не годится: он сдвигает
 *  дату на час назад/вперёд у пользователей с не московским поясом. */
export function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function addDays(isoDate: string, days: number): string {
  const d = new Date(`${isoDate}T00:00:00`);
  d.setDate(d.getDate() + days);
  return ymd(d);
}

/** «2026-05-03T06:00:00» из API → Date в локальном времени (без поясов). */
export function parseLocal(value: string): Date {
  return new Date(value.length <= 19 ? value : value.slice(0, 19));
}

export function daysInRange(from: string, to: string): string[] {
  const out: string[] = [];
  let cur = from;
  let guard = 0;
  while (cur <= to && guard < 400) {
    out.push(cur);
    cur = addDays(cur, 1);
    guard += 1;
  }
  return out;
}

export const isWeekend = (isoDate: string) => {
  const day = new Date(`${isoDate}T00:00:00`).getDay();
  return day === 0 || day === 6;
};

/** Сколько слотов занимает постановка: границы включительные.

    Считаем как на бумаге: полных шестичасовых шагов от начала плюс слот, в который
    попал конец. `round` расходился с PDF (`services/vessels.py::slots_in_range`) на
    стоянках вроде «22:00 — 09:00»: экран рисовал три слота, лист — два. */
export const slotsCount = (p: Placement) =>
  Math.max(1, Math.floor((parseLocal(p.ends_at).getTime() - parseLocal(p.starts_at).getTime()) / SLOT_MS) + 1);

export type Bar = { left: number; width: number; clippedStart: boolean; clippedEnd: boolean };

/** Полоса обязана иметь минимальную ширину: нулевую стоянку нельзя ни увидеть,
 *  ни схватить. Час — это 1/6 слота, на сетке в 35 px примерно 6 px. */
const MIN_BAR_SLOTS = 1 / 6;

/** Позиция полосы в сетке за период; выходящее за края обрезается.
 *
 *  Границы — дробные слоты, а не целые: стоянка, у которой окончание указано в
 *  21:00, обязана занимать половину ячейки 18:00, а не всю ячейку целиком.
 *  Округление до слота здесь и есть та «лишняя шестичасовая стоянка», которую
 *  диспетчер видит глазами: он ставит «до 21:00», получает закрашенные 18–24 и
 *  ищет глазами ошибку вверенных ему метров. Проверка конфликта всегда считала
 *  моменты времени как есть (`starts_at <= ends_at AND ends_at >= starts_at`),
 *  так что это рисование было единственной частью кабинета, которая врёт.
 *  Лист режет полосу так же — см. `_bar_clip` в `services/plan_pdf.py`. */
export function barGeometry(p: Placement, periodStart: string, totalSlots: number): Bar | null {
  const origin = new Date(`${periodStart}T00:00:00`).getTime();
  const first = (parseLocal(p.starts_at).getTime() - origin) / SLOT_MS;
  const last = Math.max(
    first + MIN_BAR_SLOTS,
    (parseLocal(p.ends_at).getTime() - origin) / SLOT_MS,
  );
  const left = Math.max(0, first);
  const right = Math.min(totalSlots, last);
  if (right <= left) return null;
  return { left, width: right - left, clippedStart: first < 0, clippedEnd: last > totalSlots };
}

/**
 * Действительная постановка для рисования: когда дата перестановки наступила,
 * оригинал не показывают от начала копии; если копия началась раньше оригинала —
 * оригинал исчезает вовсе (судно уже передвинули по стрелке).
 */
export function displayed(p: Placement): Placement | null {
  if (!p.effective_ends_at) return p;
  if (parseLocal(p.effective_ends_at).getTime() < parseLocal(p.starts_at).getTime()) return null;
  return { ...p, ends_at: p.effective_ends_at };
}

// Яркая палитра: насыщенные цвета различаются и по тону, и по светлоте
// (различимы и для дальтоников). Подпись на полосе адаптируется — textColorOn.
export const PALETTE = ["#2f9bff", "#12b886", "#ffb020", "#9775fa", "#ff5c5c", "#f06595"];

/** Цвет по грузу: одинаковый груз — одна и та же полоса на любом экране. */
export function cargoColor(cargo: string | null): string {
  const key = (cargo || "").trim().toUpperCase();
  if (!key) return "#55677d";
  let hash = 0;
  for (const ch of key) hash = (hash * 31 + ch.charCodeAt(0)) % 997;
  return PALETTE[hash % PALETTE.length];
}

/** Цвет полосы: ручной цвет важнее автораскраски по грузу. */
export function barColor(p: { cargo: string | null; color?: string | null }): string {
  return p.color || cargoColor(p.cargo);
}

/** Подпись на светлой полосе — тёмная, на тёмной — белая: диспетчер может выбрать любой цвет. */
export function textColorOn(hex: string): string {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) return "#ffffff";
  const [r, g, b] = [1, 2, 3].map((i) => parseInt(m[i], 16));
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.5 ? "#101c2c" : "#ffffff";
}

/**
 * Краска стрелки по цвету полосы: тот же оттенок, но затемнённый до читаемого.
 *
 * Палитра светлая (у `#ffb020` контраст к белому 1.9:1), а стрелка живёт поверх
 * сетки, серых выходных колонок и залитых полос судов: синяя линия над синей
 * полосой исчезала. Другой палитрой стрелок не делаем — по оттенку человек
 * читает, чья это цепочка. Поэтому оттенок не трогаем, а светлоту убавляем
 * ровно до порога читаемости: 4.5:1 к белому (WCAG AA для подписи). Светлоту
 * HSL для этого брать нельзя (она не перцептивная: у янтарного и бирюзового
 * она одинаковая, а читаемость разная), поэтому крутим её, пока контраст не
 * станет достаточным, но не ниже 16% — там уже чёрный и все цепочки
 * одинаковые. Тот же расчёт на листе — `_ink` в `plan_pdf.py`.
 */
export function arrowInk(hex: string): string {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) return hex;
  const [r, g, b] = [1, 2, 3].map((i) => parseInt(m[i], 16) / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  let h = 0;
  if (d) {
    h = 60 * (max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4);
    if (h < 0) h += 360;
  }
  // оттенок и размах цвета при новой светлоте: d — это «сколько краски»,
    // поэтому затемнённый янтарный остаётся янтарным, а не грязным
  const at = (l: number) => {
    const s = d ? Math.min(d / (1 - Math.abs(2 * l - 1)), 1) : 0;
    const cc = (1 - Math.abs(2 * l - 1)) * s;
    const x = cc * (1 - Math.abs(((h / 60) % 2) - 1));
    const mm = l - cc / 2;
    const [r1, g1, b1] = h < 60 ? [cc, x, 0] : h < 120 ? [x, cc, 0] : h < 180 ? [0, cc, x]
      : h < 240 ? [0, x, cc] : h < 300 ? [x, 0, cc] : [cc, 0, x];
    return [r1 + mm, g1 + mm, b1 + mm];
  };
  const onWhite = (rgb: number[]) => {
    const f = (v: number) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
    const lum = 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
    return 1.05 / (lum + 0.05);
  };
  let l = Math.min((max + min) / 2, 0.6);
  let rgb = at(l);
  for (let i = 0; i < 24 && l > 0.16 && onWhite(rgb) < 4.5; i++) {
    l = Math.max(l - 0.03, 0.16);
    rgb = at(l);
  }
  const to = (v: number) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, "0");
  return `#${to(rgb[0])}${to(rgb[1])}${to(rgb[2])}`;
}

/** Одна строка подписи полосы: «CROWN SAPPHIRE 5 500».
 *  Длину добавляем: теперь полоса — это ещё и метраж судна, а не только время. */
export function barText(p: Placement): string {
  return [p.vessel, p.volume, p.length_m ? `${p.length_m} м` : null].filter(Boolean).join(" ");
}

/* ------------------------------- шкала длины (сверху вниз) ------------------------- */

/** Строка ожидания: карман на произвольное число судов, а не причал с метрами.
 *  На него нельзя «выйти корпусом» и он не считается соседом — метров у него нет. */
export const PARK_CODE = "ОЖИДАНИЕ";

/** Одна ячейка вертикальной шкалы = 10 метров длины причала (100 м = 10 ячеек). */
export const M_PER_CELL = 10;

/** Причал без метража в справочнике рисуется и считается как 100 м
 *  (`services/vessels.py::berth_length`): нахлёст обязан делиться на соседи по
 *  тем же метрам, что рисует сетка, иначе экран и лист разойдутся. */
export const UNKNOWN_BERTH_M = 100;

/** Отрезок длины, который занимает постановка. `to === null` — до конца причала
 *  (длину не задали, занимаем консервативно). */
export function spanM(p: Placement): { from: number; to: number | null } {
  const from = p.offset_m ?? 0;
  return p.length_m == null ? { from, to: null } : { from, to: from + p.length_m };
}

/** Пиксельная позиция полосы по шкале длины: метры → px. */
export function lengthGeometry(p: Placement, cellH: number): { top: number; height: number } {
  const { from, to } = spanM(p);
  const top = (from / M_PER_CELL) * cellH;
  const bottom = to === null ? Number.POSITIVE_INFINITY : (to / M_PER_CELL) * cellH;
  return { top, height: bottom - top };
}

/** Метры в подпись: «0–60 м» или «60 м — до конца причала». */
export function lengthLabel(p: Placement): string {
  const { from, to } = spanM(p);
  return to === null ? `${from} м — до конца причала` : `${from}–${to} м`;
}

/** Подписи линейки причала: 10, 20, … метров. */
export function rulerMarks(cells: number): number[] {
  return Array.from({ length: cells }, (_, i) => (i + 1) * M_PER_CELL);
}

/** Час — шаг ручной правки срока. Слот в 6 часов для этого слишком крупный:
 *  стоянку сдвигают и на «плюс час», и на «до 15:00», и cells-сетка тут не указ. */
export const HOUR_MS = 3_600_000;

/** Пикселей в одном часе при данной ширине слота. Одно место, а не две копии:
 *  делить ширину слота на `SLOT_HOURS.length` (4 — слотов в сутках) вместо 6 —
 *  часов в слоте, и курсор начинает читаться в полтора раза правее, чем он есть. */
export const pxPerHour = (slotWidth: number): number => slotWidth / (SLOT_MS / HOUR_MS);

/** Сдвиг на N часов. Судно передвигают часом, а не слотом: у соседа, который
 *  кончается в 22:00, следующее может начаться ровно в 22:00, и шаг в 6 часов
 *  к такой позиции не приводит никогда — полосу нельзя придвинуть рукой. */
export const addHours = (d: Date, hours: number): Date => new Date(d.getTime() + hours * HOUR_MS);

/** Время под курсором на шкале плана: округление до часа, не до слота. */
export function timeAtX(
  clientX: number,
  scaleLeft: number,
  pxPerHour: number,
  periodStart: string,
): Date {
  const origin = new Date(`${periodStart}T00:00:00`).getTime();
  const hours = Math.round((clientX - scaleLeft) / pxPerHour);
  return new Date(origin + hours * HOUR_MS);
}

/** Сдвиг времени на N слотов (план перетаскивают по горизонтали). */
export function addSlots(d: Date, slots: number): Date {
  return new Date(d.getTime() + slots * SLOT_MS);
}

/** Date → «2026-05-10T06:00:00» — формат, который понимает бэкенд. */
export function isoLocal(d: Date): string {
  // Минуты пишём открыто: стоянка «22:00 — 06:30» не имеет права превращаться в
  // «22:00 — 06:00» по дороге на бэкенд только потому, что когда-то план умел
  // ставить суда исключительно по началам шестичасовых слотов.
  const p = (n: number) => String(n).padStart(2, "0");
  return `${ymd(d)}T${p(d.getHours())}:${p(d.getMinutes())}:00`;
}

export function periodLabel(from: string, to: string): string {
  return `${new Date(`${from}T00:00:00`).toLocaleDateString("ru-RU")} — ${new Date(
    `${to}T00:00:00`,
  ).toLocaleDateString("ru-RU")}`;
}

/* ----------------------- цепочки перешвартовок ------------------------ */

/**
 * Номера строк в цепочках перешвартовок — та самая «шаг N» из подписей стрелок.
 *
 * Смысл обязан совпадать с `_chain_steps` из backend/app/services/plan_pdf.py:
 * лист печатает бэкенд, экран рисует браузер, а подписи у стрелок обязаны
 * сходиться — иначе диспетчер сводит стрелки на распечатке по одним номерам,
 * а на экране видит другие. Правим одну — правь и вторую.
 *
 * Корень — постановка без `moved_from_id` либо с ссылкой на строку, которой нет
 * в выборке: период вырезают окном, и «висячая» ссылка — это тоже начало
 * цепочки, а не потеря. Дальше обход в ширину по копиям, копии одного оригинала
 * упорядочены по starts_at, поэтому номера не зависят от порядка строк в ответе
 * API. На линейной цепочке счётчик обхода = глубина + 1: «шаг 1» — корень,
 * «шаг 2» — первая перестановка. Счётчик именно обхода (а не глубина): если от
 * оригинала пошли две копии, вторая перестановка всё равно получит свой номер,
 * и стрелки не начнут конкурировать за «шаг 2».
 */
export function chainSteps(placements: Placement[]): Map<number, number> {
  const byId = new Map(placements.map((p) => [p.id, p]));
  const kids = new Map<number, Placement[]>();
  for (const p of placements) {
    if (p.moved_from_id != null && byId.has(p.moved_from_id)) {
      kids.set(p.moved_from_id, [...(kids.get(p.moved_from_id) ?? []), p]);
    }
  }
  // тот же порядок, что в Python: сначала старт, затем id — иначе две копии,
  // начатые в один слот, нумеровались бы по случайному порядку строк
  const cmp = (a: Placement, b: Placement) =>
    parseLocal(a.starts_at).getTime() - parseLocal(b.starts_at).getTime() || a.id - b.id;
  const order = new Map<number, number>();
  const roots = placements
    .filter((p) => !(p.moved_from_id != null && byId.has(p.moved_from_id)))
    .slice()
    .sort(cmp);
  for (const root of roots) {
    if (order.has(root.id)) continue;
    const seq: Placement[] = [root];
    let i = 0;
    let n = 0;
    while (i < seq.length) {
      const node = seq[i];
      i += 1;
      if (order.has(node.id)) continue;
      n += 1;
      order.set(node.id, n);
      seq.push(...(kids.get(node.id) ?? []).slice().sort(cmp));
    }
  }
  // на случай цикла по moved_from_id — осиротевшие строки тоже нумеруем,
  // иначе у стрелки не будет подписи вообще
  for (const p of placements) if (!order.has(p.id)) order.set(p.id, order.size + 1);
  return order;
}

/**
 * «шаг 1→2» — тот же формат, что печатает PDF (`step_label` в plan_pdf.py).
 * Одно слово на двух носителях: экран и лист должны описывать перестановку
 * одинаковыми буквами, иначе «шаг 2» на бумаге и на экране — разные стрелки.
 */
export function stepLabel(steps: Map<number, number>, srcId: number, dstId: number): string {
  return `шаг ${steps.get(srcId) ?? "?"}→${steps.get(dstId) ?? "?"}`;
}

/** «2026-11-02T12:00:00» → «02.11 12:00» — короткое время старта в подпись стрелки. */
export function shortStart(value: string): string {
  const d = parseLocal(value);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getDate())}.${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/**
 * Полная подпись у наконечника стрелки: «шаг 1→2 · РП-6 · 02.11 12:00».
 * Шифр причала и время старта цели — потому что именно их диспетчер ищет
 * глазами: куда уедет судно и когда. «Ожидание» — не причал, а карман, поэтому
 * код виртуального причала сюда приходит уже человеческим словом.
 */
export function arrowCaption(
  steps: Map<number, number>,
  srcId: number,
  dstId: number,
  destCode: string,
  destStartsAt: string,
): string {
  return `${stepLabel(steps, srcId, dstId)} · ${destCode} · ${shortStart(destStartsAt)}`;
}

/**
 * Непустые связи цепочек: «оригинал → копия», у которой ещё есть стрелка
 * (состоявшаяся перестановка стрелки не имеет). По этим связям рисуются
 * стрелки, и той же функцией проверка сверяет их число с данными.
 */
export function chainLinks(placements: Placement[]): { srcId: number; dstId: number }[] {
  const ids = new Set(placements.map((p) => p.id));
  return placements
    .filter((p) => !p.committed && p.moved_from_id != null && ids.has(p.moved_from_id))
    .map((p) => ({ srcId: p.moved_from_id as number, dstId: p.id }));
}

/* ============================ нахлёст на соседний причал ============================
   Судно длиннее своего причала — диспетчер обязан показать это честно: полоса
   доезжает до конца своего блока и продолжается пунктиром в блоке соседа.
   Направление задаёт фронт (sort_order), а не «какой причал выбрали»: хвост
   физически лезет к следующему по списку, нос — к предыдущему.            */

/** «bow» — корпус лезет раньше начала причала, «stern» — за его конец. */
export type SpillSide = "bow" | "stern";

/** Куда корпусу не хватает метров причала. null — стоит целиком у своего причала.
 *  Без длины судна считать нечего: нахлёст существует только при известной длине. */
export function spillSide(
  lengthM: number | null,
  offsetM: number,
  berthLengthM: number | null,
): SpillSide | null {
  if (lengthM == null || !berthLengthM) return null;
  if (offsetM < 0) return "bow";
  return offsetM + lengthM > berthLengthM ? "stern" : null;
}

/** Сколько метров корпуса уходит за границу причала (0 — нахлёста нет). */
export function spillMeters(
  lengthM: number | null,
  offsetM: number,
  berthLengthM: number | null,
): number {
  if (lengthM == null || !berthLengthM) return 0;
  return Math.max(0, -offsetM, offsetM + lengthM - berthLengthM);
}

/** Сосед, на который лезет корпус. Нужен перетаскиванию: полоса летит под
 *  курсор, и выбирать причал нахлёста вручную там некому — направление берём
 *  с фронта (`sort_order`): хвост к следующему причалу списка, нос к предыдущему.
 *  Причал «Ожидание» соседом не считается: это карман, стенки с метрами у него нет. */
export function spillNeighbour(
  berths: Berth[],
  berth: Berth | undefined,
  side: SpillSide | null,
): Berth | null {
  if (!berth || !side) return null;
  const front = frontOf(berths);
  const i = front.findIndex((b) => b.id === berth.id);
  if (i === -1) return null;
  return (side === "bow" ? front[i - 1] : front[i + 1]) ?? null;
}

/** Причалы, которые вообще могут принять корпус: живой фронт без «Ожидания».
 *  Порядок — `sort_order`, он же порядок вывода блоков. */
export function frontOf(berths: Berth[]): Berth[] {
  return berths
    .filter((b) => b.code !== PARK_CODE && b.is_active)
    .sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
}

/** Что предложить в окне постановки. Соседей показываем подписанными («выше по
 *  фронту» / «ниже по фронту»), но список им не ограничен: `sort_order` — это
 *  порядок блоков на экране, а не порядок участков у воды, и диспетчер видит
 *  стенку лучше, чем порядок сортировки в справочнике. */
export function spillChoices(
  berths: Berth[],
  berth: Berth | undefined,
): { prev: Berth | null; next: Berth | null; others: Berth[] } {
  const front = frontOf(berths);
  const i = berth ? front.findIndex((b) => b.id === berth.id) : -1;
  const prev = i > 0 ? front[i - 1] : null;
  const next = i >= 0 && i < front.length - 1 ? front[i + 1] : null;
  // «другие» — это ровно те, кто не показан сверху: иначе один и тот же причал
  // встречался в списке дважды и выбор «РП-3» из разных групп означал разное
  const shown = new Set([berth?.id, prev?.id, next?.id].filter((x): x is number => x != null));
  return { prev, next, others: front.filter((b) => !shown.has(b.id)) };
}

/** Сегменты чужого корпуса на соседних причалах: [(причал, от, до, сторона)].
 *
 *  Корпус — непрерывный отрезок стенки: сколько метров вылезло за свой причал,
 *  столько и лежит дальше, весь сосед, затем следующий, пока метраж не кончится.
 *  Раньше нахлёст обрезался по длине соседа: у «МАРИНЫ» (200 м) на причале 110 м
 *  хвост в 175 м показывался как 130, а 45 м исчезали с экрана — диспетчер видел
 *  «судно уменьшилось», а третье поле оставалось пустым и свободным.
 *
 *  Причал из `spill_berth_id` — слово диспетчера, КАКОЙ причал принимает корпус,
 *  и с него начинается отсчёт; остаток переезжает на следующего. Зеркалит
 *  `services/vessels.py::spill_segments` — экран, проверка и лист обязаны видеть
 *  занятые метры одинаково. */
export function spillSegments(
  p: Pick<Placement, "berth_id" | "offset_m" | "length_m" | "spill_berth_id">,
  berths: Berth[],
): { berthId: number; from: number; to: number; side: SpillSide }[] {
  const own = berths.find((b) => b.id === p.berth_id);
  const len = p.length_m;
  if (!own?.length_m || len == null) return [];
  const front = frontOf(berths);
  const here = front.findIndex((b) => b.id === own.id);
  if (here === -1) return [];
  const off = p.offset_m ?? 0;
  const back = Math.max(-off, 0);
  const fwd = Math.max(off + len - own.length_m, 0);
  // Кому из сторон достается названный причал: если корпус лезет в обе стороны —
  // названный читается по фронту (выше своего это нос, ниже — хвост); если сторона
  // одна, то назван именно она, каким бы причал ни стоял в списке.
  const namedIdx = p.spill_berth_id == null ? -1 : front.findIndex((b) => b.id === p.spill_berth_id);
  const named: SpillSide | null =
    namedIdx === -1 || namedIdx === here ? null
    : back && fwd ? (namedIdx < here ? "bow" : "stern")
    : back ? "bow" : "stern";
  const out: { berthId: number; from: number; to: number; side: SpillSide }[] = [];
  for (const [side, meters, step] of [["bow", back, -1], ["stern", fwd, 1]] as const) {
    if (!meters) continue;
    let rest = meters;
    for (let j = side === named ? namedIdx : here + step; j >= 0 && j < front.length && rest > 0; j += step) {
      const b = front[j];
      // метраж без значения считается как на сетке —Unknown, иначе экран и лист
      // разойдутся на том, сколько остаток корпуса лежит на этом причале
      const lenM = b.length_m ?? UNKNOWN_BERTH_M;
      if (b.id === own.id || lenM <= 0) continue;
      const take = Math.min(rest, lenM);
      out.push(
        side === "bow"
          ? { berthId: b.id, from: lenM - take, to: lenM, side }
          : { berthId: b.id, from: 0, to: take, side },
      );
      rest -= take;
    }
  }
  return out;
}

/** Метры причала, которые чужое судно заняло своим корпусом: «судно p стоит
 *  хвостом (носом) на этом причале от `from` до `to`». Фронт обязан показывать их
 *  занятыми — иначе два борта встанут в одни метры, потому что у каждого «свои»
 *  были свободны. */
export function foreignSpills(
  placements: Placement[],
  berths: Berth[],
  berthId: number,
): { p: Placement; from: number; to: number | null; side: SpillSide }[] {
  const all: { p: Placement; from: number; to: number | null; side: SpillSide }[] = [];
  for (const p of placements) {
    for (const seg of spillSegments(p, berths)) {
      if (seg.berthId === berthId) all.push({ p, from: seg.from, to: seg.to, side: seg.side });
    }
  }
  return all;
}
