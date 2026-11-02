/**
 * Общий набор для графиков страницы «Индикаторы».
 *
 * Библиотек не берём: у кабинета свои цвета (палитра БазисПорта), своё «матовое
 * стекло» тултипов и своё поведение при prefers-reduced-motion. Сюда вынесено
 * то, что повторяется в каждом графике: палитра, измерение контейнера, «красивая»
 * шкала, дорожка столбца/сплайн, карточка-тултип и легенда-пилюли.
 *
 * Как и DynamicsChart, все графики рисуются в РЕАЛЬНЫХ пикселях (viewBox =
 * фактическая ширина), поэтому текст не растягивается, когда плашку тянут мышью.
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { nf } from "../format";

export const CHART = {
  grid: "rgba(27,51,96,.12)",
  axis: "rgba(27,51,96,.25)",
  cross: "rgba(27,51,96,.25)",
  muted: "#566a85",        // ink-400 — подписи осей
  text: "#0f1b2d",         // ink-100
  track: "rgba(27,51,96,.10)",   // фон «пули» (план) и тепловых ячеек без значения
  plan: "#93a3ba",         // ink-500 — план/контекст
  navy: "#1b3360",
  sky: "#2fbaec",
  accent: "#1668a8",
  ok: "#1f7a4d",
  warn: "#7d6008",
  crit: "#b3312a",
  amber: "#c47b12",
} as const;

/**
 * Серии по умолчанию. Порядок важен: первый цвет — главная величина,
 * дальше — убывающие по визуальному весу; красный оставлен «последним»
 * (в стеках это обычно потери/выбытия, а не основной показатель).
 */
export const SERIES_COLORS = [
  "#337ab7", // brand.mid
  "#2fbaec", // brand.sky
  "#1b3360", // brand.navy
  "#c47b12", // янтарь
  "#1f7a4d", // зелёный
  "#7d6008", // оливковый
  "#66afe9", // brand.focus
  "#b3312a", // красный
] as const;

export const colorAt = (i: number) => SERIES_COLORS[i % SERIES_COLORS.length];

export const r2 = (v: number) => Math.round(v * 100) / 100;

export const reducedMotion = () =>
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;

/** Ширина контейнера: графики перерисовываются при resize плашки.

 * ПОЧЕМУ ЗДЕСЬ (замер `tools/ui_perf.mjs`, TASKS.md п.8): раньше ширину читали
 * `el.clientWidth` и в момент маунта, и в каждом колбэке ResizeObserver. Чтение
 * `clientWidth` — это принудительная раскладка ВСЕЙ страницы, а наблюдатель
 * срабатывает на каждое изменение размера плашки, то есть на каждой перерисовке
 * графика: 22 графика × принудительный layout = 69 мс главного потока. Из записи
 * наблюдателя ширину берём бесплатно — браузер её уже посчитал.
 */
export function useSize<T extends HTMLElement>(min = 240) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(720);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Первое измерение — синхронное: с отложенным графики рисовали бы первый кадр
    // в ширину по умолчанию, и было бы видно, как они «усаживаются» в размер.
    setWidth(Math.max(min, el.clientWidth));
    const ro = new ResizeObserver((entries) => {
      const w = entries[entries.length - 1]?.contentRect.width ?? 0;
      // 0 — значит контейнер скрыт (display:none, пропущенная content-visibility);
      // прошлое значение лучше, чем график шириной в ноль
      if (w > 0) setWidth(Math.max(min, w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [min]);
  return { ref, width };
}

/** Анимация появления — один раз на смену набора, а не на каждый рендер. */
export function useGrow(sig: string) {
  const [grown, setGrown] = useState(() => !reducedMotion());
  useEffect(() => {
    if (reducedMotion()) {
      setGrown(true);
      return;
    }
    setGrown(false);
    let second = 0;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => setGrown(true));
    });
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
    };
  }, [sig]);
  return grown;
}

/** Шкала «до красивого числа»: 1 234 → 1 500, 980 → 1 000. */
export function niceMax(value: number): number {
  if (!isFinite(value) || value <= 0) return 1;
  const exp = Math.floor(Math.log10(value));
  const base = 10 ** exp;
  const unit = value / base;
  const step = unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10;
  return step * base;
}

/** Столбец со скруглением сверху (низ упирается в ось). */
export function barPath(x: number, y: number, w: number, h: number, r = 3): string {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  const x2 = x + w;
  const yb = y + h;
  return `M${r2(x)},${r2(yb)}L${r2(x)},${r2(y + rr)}Q${r2(x)},${r2(y)} ${r2(x + rr)},${r2(y)}` +
    `L${r2(x2 - rr)},${r2(y)}Q${r2(x2)},${r2(y)} ${r2(x2)},${r2(y + rr)}L${r2(x2)},${r2(yb)}Z`;
}

/** Сегмент стека: скругление только у верхнего сегмента. */
export function rectPath(x: number, y: number, w: number, h: number): string {
  return `M${r2(x)},${r2(y)}h${r2(w)}v${r2(h)}h${r2(-w)}Z`;
}

/** Монотонный сплайн (Fritsch–Carlson) — без «петель» между точками. */
export function smoothPath(pts: { x: number; y: number }[]): string {
  const n = pts.length;
  if (n === 0) return "";
  if (n === 1) return `M${r2(pts[0].x)},${r2(pts[0].y)}`;
  if (n === 2) return `M${r2(pts[0].x)},${r2(pts[0].y)}L${r2(pts[1].x)},${r2(pts[1].y)}`;
  const dx: number[] = [];
  const slope: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    dx[i] = pts[i + 1].x - pts[i].x;
    slope[i] = (pts[i + 1].y - pts[i].y) / (dx[i] || 1);
  }
  const tan: number[] = new Array(n);
  tan[0] = slope[0];
  tan[n - 1] = slope[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (slope[i - 1] * slope[i] <= 0) tan[i] = 0;
    else {
      const w1 = 2 * dx[i] + dx[i - 1];
      const w2 = dx[i] + 2 * dx[i - 1];
      tan[i] = (w1 + w2) / (w1 / slope[i - 1] + w2 / slope[i]);
    }
  }
  let d = `M${r2(pts[0].x)},${r2(pts[0].y)}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i];
    d += `C${r2(pts[i].x + h / 3)},${r2(pts[i].y + (tan[i] * h) / 3)} ` +
      `${r2(pts[i + 1].x - h / 3)},${r2(pts[i + 1].y - (tan[i + 1] * h) / 3)} ` +
      `${r2(pts[i + 1].x)},${r2(pts[i + 1].y)}`;
  }
  return d;
}

/** Дуга «шайбы»/«спидометра» по углу в радианах (0 = вверх, по часовой). */
export function arcPath(cx: number, cy: number, r: number, from: number, to: number, width: number): string {
  const p = (angle: number, radius: number) => [
    cx + radius * Math.sin(angle),
    cy - radius * Math.cos(angle),
  ];
  const [x1, y1] = p(from, r);
  const [x2, y2] = p(to, r);
  const [x3, y3] = p(to, r - width);
  const [x4, y4] = p(from, r - width);
  const big = Math.abs(to - from) > Math.PI ? 1 : 0;
  return `M${r2(x1)},${r2(y1)}A${r2(r)},${r2(r)} 0 ${big} 1 ${r2(x2)},${r2(y2)}` +
    `L${r2(x3)},${r2(y3)}A${r2(r)},${r2(r)} 0 ${big} 0 ${r2(x4)},${r2(y4)}Z`;
}

/** Короткое число: 12 345 → «12,3 тыс.» — для подписей, где тонны не влезают. */
export function compact(v: number, digits = 1): string {
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `${nf(v / 1_000_000, digits)} млн`;
  if (abs >= 10_000) return `${nf(v / 1000, abs >= 100_000 ? 0 : digits)} тыс.`;
  return nf(v, abs >= 100 ? 0 : digits > 0 && abs < 10 ? 1 : 0);
}

export const pctText = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined ? "—" : `${nf(v, digits)}%`;

/** Пустой график: одна формулировка на всю страницу. */
export function ChartEmpty({ text = "Нет данных за период" }: { text?: string }) {
  return <div className="grid h-full min-h-[120px] place-items-center text-sm text-ink-300">{text}</div>;
}

/** Карточка-тултип (матовое стекло) — та же, что у DynamicsChart. */
export function ChartTip({
  x, y, width, below, title, rows,
}: {
  x: number; y: number; width: number; below: boolean;
  title: string;
  rows: { label: string; value: string; color?: string }[];
}) {
  const half = 110;
  return (
    <div
      className="dc-tip"
      style={{
        left: `${(Math.min(Math.max(x, half), width - half) / width) * 100}%`,
        top: `${y}px`,
        transform: `translate(-50%, ${below ? "0" : "-100%"})`,
      }}
    >
      <div className="dc-tip-title">{title}</div>
      {rows.map((r, i) => (
        // ключ по индексу: подписи строк повторяются (ряд + строка тултипа «судозаходов»)
        <div className="dc-tip-row" key={`${r.label}-${i}`}>
          <span>
            {r.color && (
              <i
                className="dc-swatch dc-swatch-line mr-1.5"
                style={{ background: r.color, display: "inline-block" }}
                aria-hidden="true"
              />
            )}
            {r.label}
          </span>
          <b className="num">{r.value}</b>
        </div>
      ))}
    </div>
  );
}

/** Легенда-пилюли: наведение подсвечивает ряд, остальные приглушает график. */
export function ChartLegend({
  items, focus, onFocus, note,
}: {
  items: { key: string; label: string; color: string; dashed?: boolean }[];
  focus: string | null;
  onFocus: (key: string | null) => void;
  note?: string;
}) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-ink-300">
      {items.map((it) => (
        <button
          key={it.key}
          type="button"
          className="dc-pill"
          data-legend={it.key}
          data-on={focus === it.key ? "1" : "0"}
          onMouseEnter={() => onFocus(it.key)}
          onMouseLeave={() => onFocus(null)}
          onFocus={() => onFocus(it.key)}
          onBlur={() => onFocus(null)}
        >
          <i
            className="dc-swatch"
            style={{
              width: it.dashed ? 14 : 10,
              height: it.dashed ? 3 : 10,
              borderRadius: it.dashed ? 2 : 3,
              background: it.color,
            }}
            aria-hidden="true"
          />{" "}
          {it.label}
        </button>
      ))}
      {note && <span className="ml-auto text-ink-300">{note}</span>}
    </div>
  );
}

/** Подписи оси X: показываем каждую N-ю, чтобы не сливались. */
export function labelEvery(count: number, innerWidth: number, minPx = 54) {
  return Math.ceil(count / Math.max(3, Math.floor(innerWidth / minPx)));
}

export const gridLines = [1, 0.75, 0.5, 0.25, 0];
