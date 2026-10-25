/**
 * Форматирование чисел и дат для всего кабинета.
 *
 * ПОЧЕМУ ЗДЕСЬ КЭШ. Профилировщик входа в «Статистику» (замер `tools/ui_perf.mjs`,
 * TASKS.md п.8) показал, что самый горячий лист страницы — не графики, а
 * `toLocaleString`/`toLocaleDateString` с объектом опций: каждый такой вызов
 * создаёт новый `Intl.*Format` (в V8 это десятки микросекунд), а на одной
 * отрисовке «Статистики» вызовов тысячи — подписи осей, строки тултипов,
 * `aria-label` каждой колонки, ячейки таблиц, `hint`-подписи плашек. 233 мс из
 * 351 мс длинных задач приходилось на `nf`. Форматтеры держим по одному на
 * набор опций, подписи дат — по одному на строку: вывод тот же до символа,
 * locale и опции те же.
 */

const LOCALE = "ru-RU";

const numFormatters = new Map<number, Intl.NumberFormat>();

/** Один `Intl.NumberFormat` на точность вместо нового на каждый вызов. */
const numberFormat = (digits: number) => {
  let f = numFormatters.get(digits);
  if (!f) {
    f = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits });
    numFormatters.set(digits, f);
  }
  return f;
};

export const nf = (v: number, digits = 0) => numberFormat(digits).format(v);

export const pct = (v: number, digits = 0) => `${nf(v * 100, digits)}%`;

export const signed = (v: number) => `${v > 0 ? "+" : ""}${nf(v, 1)}%`;

const dateFormatters = new Map<string, Intl.DateTimeFormat>();

const dateFormat = (key: string, opts?: Intl.DateTimeFormatOptions) => {
  let f = dateFormatters.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat(LOCALE, opts);
    dateFormatters.set(key, f);
  }
  return f;
};

/**
 * Подписи дат пересоздаются на каждый рендер каждого графика (одна и та же дата
 * встречается в десятках мест периода), поэтому держим и результат. Ключ —
 * формат + ISO-строка, период ограничен `dispatcher.max_period_days`, но
 * страховка от роста памяти всё равно есть.
 */
const labelCache = new Map<string, string>();

const cached = (key: string, make: () => string) => {
  const hit = labelCache.get(key);
  if (hit !== undefined) return hit;
  const value = make();
  if (labelCache.size > 4000) labelCache.clear();
  labelCache.set(key, value);
  return value;
};

export function asOf(iso: string | null): string {
  if (!iso) return "нет данных";
  return cached(`asof|${iso}`, () =>
    dateFormat("asof", { dateStyle: "short", timeStyle: "short" }).format(new Date(iso.endsWith("Z") ? iso : `${iso}Z`)),
  );
}

export function dayLabel(iso: string): string {
  return cached(`day|${iso}`, () =>
    dateFormat("day", { day: "2-digit", month: "short" }).format(new Date(`${iso}T00:00:00`)),
  );
}

/** «12 октября 2026» — заголовки панелей детализации. */
export function dayFull(iso: string): string {
  return cached(`dayfull|${iso}`, () =>
    dateFormat("dayfull", { day: "numeric", month: "long", year: "numeric" }).format(new Date(`${iso}T00:00:00`)),
  );
}

/** «12.10.2026» — границы периода. */
export function dmy(iso: string): string {
  return cached(`dmy|${iso}`, () => dateFormat("dmy").format(new Date(`${iso}T00:00:00`)));
}

/**
 * «07.10, 14:30» — дата с временем в таблицах подхода судов. Раньше каждая
 * строка звала `toLocaleString` со своими опциями, а строк в таблице до 40 и
 * перерисовывается она вместе со всей страницей.
 */
export function dmyhm(iso: string | null | undefined): string {
  if (!iso) return "";
  return cached(`dmyhm|${iso}`, () =>
    dateFormat("dmyhm", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(iso)),
  );
}

export const roleLabel: Record<string, string> = {
  admin: "Администратор",
  top: "Топ-менеджмент",
  manager: "Менеджер",
};
