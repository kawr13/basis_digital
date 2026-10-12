export const nf = (v: number, digits = 0) =>
  v.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const pct = (v: number, digits = 0) => `${nf(v * 100, digits)}%`;

export const signed = (v: number) => `${v > 0 ? "+" : ""}${nf(v, 1)}%`;

export function asOf(iso: string | null): string {
  if (!iso) return "нет данных";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

export function dayLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "short" });
}

/** «12 октября 2026» — заголовки панелей детализации. */
export function dayFull(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

/** «12.10.2026» — границы периода. */
export function dmy(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("ru-RU");
}

export const roleLabel: Record<string, string> = {
  admin: "Администратор",
  top: "Топ-менеджмент",
  manager: "Менеджер",
};
