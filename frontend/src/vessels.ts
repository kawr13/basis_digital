/**
 * Расчёт для сетки «Постановка судов».
 *
 * Сутки разбиты на 4 слота по 6 часов — ровно как в бумажном плане причалов.
 * Всё позиционирование полос идёт в слотах, поэтому сетка одинаково выглядит
 * и на неделю, и на три месяца.
 */
import type { Placement } from "./api";

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

/** Сколько слотов занимает постановка: границы включительные. */
export const slotsCount = (p: Placement) =>
  Math.max(1, Math.round((parseLocal(p.ends_at).getTime() - parseLocal(p.starts_at).getTime()) / SLOT_MS) + 1);

export type Bar = { left: number; width: number; clippedStart: boolean; clippedEnd: boolean };

/** Позиция полосы в сетке за период; выходящее за края обрезается. */
export function barGeometry(p: Placement, periodStart: string, totalSlots: number): Bar | null {
  const origin = new Date(`${periodStart}T00:00:00`).getTime();
  const firstSlot = Math.floor((parseLocal(p.starts_at).getTime() - origin) / SLOT_MS);
  const span = slotsCount(p);
  const left = Math.max(0, firstSlot);
  const right = Math.min(totalSlots, firstSlot + span);
  if (right <= left) return null;
  return { left, width: right - left, clippedStart: firstSlot < 0, clippedEnd: firstSlot + span > totalSlots };
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

/** Одна строка подписи полосы: «CROWN SAPPHIRE 5 500».
 *  Длину добавляем: теперь полоса — это ещё и метраж судна, а не только время. */
export function barText(p: Placement): string {
  return [p.vessel, p.volume, p.length_m ? `${p.length_m} м` : null].filter(Boolean).join(" ");
}

/* ------------------------------- шкала длины (сверху вниз) ------------------------- */

/** Одна ячейка вертикальной шкалы = 10 метров длины причала (100 м = 10 ячеек). */
export const M_PER_CELL = 10;

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

/** Сдвиг времени на N слотов (план перетаскивают по горизонтали). */
export function addSlots(d: Date, slots: number): Date {
  return new Date(d.getTime() + slots * SLOT_MS);
}

/** Date → «2026-05-10T06:00:00» — формат, который понимает бэкенд. */
export function isoLocal(d: Date): string {
  return `${ymd(d)}T${String(d.getHours()).padStart(2, "0")}:00:00`;
}

export function periodLabel(from: string, to: string): string {
  return `${new Date(`${from}T00:00:00`).toLocaleDateString("ru-RU")} — ${new Date(
    `${to}T00:00:00`,
  ).toLocaleDateString("ru-RU")}`;
}
