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

// Палитра подобрана по WCAG: белый текст на любой полосе ≥ 5.3:1 (AA с запасом),
// цвета различимы и для дальтоников (разная светлота, не только оттенок).
const PALETTE = ["#2b6a99", "#17705c", "#9a5c0b", "#7a4fa0", "#a03f37", "#4a6b1f"];

/** Цвет по грузу: одинаковый груз — одна и та же полоса на любом экране. */
export function cargoColor(cargo: string | null): string {
  const key = (cargo || "").trim().toUpperCase();
  if (!key) return "#55677d";
  let hash = 0;
  for (const ch of key) hash = (hash * 31 + ch.charCodeAt(0)) % 997;
  return PALETTE[hash % PALETTE.length];
}

/** Одна строка подписи полосы: «CROWN SAPPHIRE 5 500». */
export function barText(p: Placement): string {
  return [p.vessel, p.volume].filter(Boolean).join(" ");
}

export function periodLabel(from: string, to: string): string {
  return `${new Date(`${from}T00:00:00`).toLocaleDateString("ru-RU")} — ${new Date(
    `${to}T00:00:00`,
  ).toLocaleDateString("ru-RU")}`;
}
