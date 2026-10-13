import { useEffect, useRef, useState } from "react";
import type { DynamicsPoint } from "../api";
import { dayFull, dayLabel, nf } from "../format";

/**
 * Палитра графика повторяет tailwind-палитру (ink / brand): держим её здесь,
 * чтобы SVG-оси не разъезжались с цветами панелей. Все цвета — под СВЕТЛЫЙ фон
 * матовой плашки: янтарные столбцы на белом дают 2.2:1 и не читаются, поэтому
 * столбцы синие (верх светлее, низ — brand.mid), линия хранения — янтарь.
 */
const CHART = {
  grid: "rgba(27,51,96,.12)", // линии сетки — navy, едва заметный на светлом
  axis: "rgba(27,51,96,.25)", // базовая ось — чуть плотнее сетки
  cross: "rgba(27,51,96,.25)", // перекрестие наведённой колонки
  muted: "#566a85", // ink-400 — подписи осей
  barTop: "#4a8ecb", // верх столбца — светлее, низ brand.mid: столбец объёмный
  barBottom: "#337ab7",
  selTop: "#1b3360", // brand.navy — выбранный столбец («мы здесь»)
  selBottom: "#24457a",
  selStroke: "#cfd8e6", // ink-600 — тонкая светлая обводка выбранного столбца
  dwell: "#c47b12", // янтарь, затемнённый под светлый фон — среднее время хранения
  areaTop: "rgba(196,123,18,.20)", // заливка под линией хранения
  areaBottom: "rgba(196,123,18,0)",
  out: "#1b3360", // brand.navy — отгрузка
} as const;

/** Ряды, которые умеет подсвечивать легенда-пилюли. */
type Series = "bars" | "dwell" | "out";

const LEGEND: { key: Series; label: string; swatch: string; style?: React.CSSProperties }[] = [
  { key: "bars", label: "обработано, т", swatch: "dc-swatch-bar" },
  { key: "dwell", label: "среднее время хранения, ч", swatch: "dc-swatch-line", style: { background: CHART.dwell } },
  { key: "out", label: "отгружено, т", swatch: "dc-swatch-line", style: { background: CHART.out } },
];

/** Сетка из трёх линий: 0 / 50 / 100 % шкалы. */
const GRID_F = [1, 0.5, 0];
/** Длительность появления (столбцы растут, линия рисуется). */
const ANIM_MS = 360;
/** Оценка ширины карточки-тултипа — нужна, чтобы не вылезать за края графика. */
const TIP_W = 200;

const r2 = (v: number) => Math.round(v * 100) / 100;

const reducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;

/**
 * Плавная линия без «петель»: монотонная кубическая интерполяция
 * (Fritsch–Carlson). Касательные в точках считаются гармоническим средним
 * соседних наклонов и обнуляются на смене знака — поэтому сплайн не даёт
 * перелётов за соседние точки (в отличие от наивного Catmull-Rom) и честно
 * ложится на реальные сутки. Библиотек не берём: реализация — 25 строк.
 */
function smoothPath(pts: { x: number; y: number }[]): string {
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
    if (slope[i - 1] * slope[i] <= 0) {
      tan[i] = 0; // экстремум — касательная горизонтальна, петли не будет
    } else {
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

/** Столбец со скруглением только сверху: низ остаётся прямым и упирается в ось. */
function barPath(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  const x2 = x + w;
  const yb = y + h;
  return `M${r2(x)},${r2(yb)}L${r2(x)},${r2(y + rr)}Q${r2(x)},${r2(y)} ${r2(x + rr)},${r2(y)}` +
    `L${r2(x2 - rr)},${r2(y)}Q${r2(x2)},${r2(y)} ${r2(x2)},${r2(y + rr)}L${r2(x2)},${r2(yb)}Z`;
}

/**
 * Динамика обработки: столбцы (обработано, т) + плавная линия среднего времени
 * хранения с градиентной заливкой + засечки отгрузки. Наведение даёт перекрестие
 * и карточку-тултип (матовое стекло), клик по колонке открывает детализацию.
 *
 * SVG рисуется в реальных пикселях (viewBox = фактическая ширина контейнера,
 * ResizeObserver), поэтому текст не растягивается, когда плашку растягивают мышью.
 *
 * Интерактивный слой (rect.dc-hit) рисуется ПОСЛЕДНИМ: он перекрывает столбцы,
 * поэтому в него обязательно возвращать управление (клик/Enter/Space) и держать
 * data-bar / data-selected / aria-selected / aria-label — на них висят автотесты
 * tools/ui_drilldown.mjs.
 *
 * Без onSelectDay график ведёт себя как раньше (только тултип).
 */
export function DynamicsChart({
  points,
  height = 220,
  onSelectDay,
  selectedDay = null,
}: {
  points: DynamicsPoint[];
  height?: number;
  /** клик по столбцу (сутки); не передан — график неинтерактивный */
  onSelectDay?: (day: string) => void;
  /** выбранные сутки: столбец подсвечивается фирменным голубым (accent) */
  selectedDay?: string | null;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(760);
  /** индекс наведённой/сфокусированной колонки — под перекрестие и тултип */
  const [hover, setHover] = useState<number | null>(null);
  /** ряд, подсвеченный легендой; остальные приглушаются */
  const [focusSeries, setFocusSeries] = useState<Series | null>(null);
  /**
   * Анимация появления проигрывается один раз на приход НАБОРА точек (sig),
   * а не на каждый рендер: иначе дёргалась бы при рефетче раз в минуту,
   * при перетаскивании плашки и при смене выбранного дня.
   */
  const sig = `${points.length}:${points[0]?.bucket_date ?? ""}:${points[points.length - 1]?.bucket_date ?? ""}`;
  const [grown, setGrown] = useState(() => !reducedMotion());

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const apply = () => setWidth(Math.max(320, el.clientWidth));
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (reducedMotion()) {
      setGrown(true);
      return;
    }
    setGrown(false);
    // два кадра: первый — «нулевое» состояние без transition, второй — рост
    let second = 0;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => setGrown(true));
    });
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
    };
  }, [sig]);

  if (!points.length) {
    return <div className="text-sm text-ink-300 py-10 text-center">Нет данных о обработке</div>;
  }

  const W = Math.max(320, width);
  const H = Math.max(160, height);
  const pad = { t: 16, r: 46, b: 26, l: 46 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const base = pad.t + ih;

  const maxT = Math.max(...points.map((p) => Math.max(p.processed_t, p.inbound_t, p.outbound_t)), 1);
  const maxD = Math.max(...points.map((p) => p.avg_dwell_hours), 1);
  const step = iw / points.length;
  const bw = Math.max(step * 0.55, 3);

  const yT = (v: number) => pad.t + ih - (v / maxT) * ih;
  const yD = (v: number) => pad.t + ih - (v / maxD) * ih;
  const cx = (i: number) => pad.l + step * (i + 0.5);

  // линия хранения — сглаженная; заливка замкнута по нижней границе графика
  const pts = points.map((p, i) => ({ x: cx(i), y: yD(p.avg_dwell_hours) }));
  const linePath = smoothPath(pts);
  const areaPath = `${linePath}L${r2(pts[pts.length - 1].x)},${base}L${r2(pts[0].x)},${base}Z`;

  const labelEvery = Math.ceil(points.length / Math.max(4, Math.floor(iw / 70)));

  // наведение живёт и у мыши, и у клавиатуры: тултип показываем в обоих случаях
  const hi = hover != null && hover >= 0 && hover < points.length ? hover : null;
  const dim = (s: Series) => (focusSeries && focusSeries !== s ? 0.35 : 1);

  // карточка-тултип: X в процентах (svg умеет сжиматься при узкой плашке),
  // Y в пикселях (height фиксированный); по краям карточку прижимаем к центру
  let tip: { left: number; top: number; below: boolean; p: DynamicsPoint } | null = null;
  if (hi != null) {
    const p = points[hi];
    const half = Math.min(TIP_W / 2, W / 2 - 2);
    const anchorY = Math.min(yT(p.processed_t), yD(p.avg_dwell_hours));
    const below = anchorY < pad.t + 104; // сверху карточка не влезает → рисуем ниже
    tip = {
      left: Math.min(Math.max(cx(hi), half + 2), W - half - 2),
      top: below ? anchorY + 14 : anchorY - 10,
      below,
      p,
    };
  }

  return (
    <div ref={boxRef} className="w-full relative" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Динамика обработки">
        <defs>
          <linearGradient id="dc-bar" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CHART.barTop} stopOpacity={1} />
            <stop offset="100%" stopColor={CHART.barBottom} stopOpacity={0.92} />
          </linearGradient>
          <linearGradient id="dc-bar-sel" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CHART.selTop} stopOpacity={0.98} />
            <stop offset="100%" stopColor={CHART.selBottom} stopOpacity={0.9} />
          </linearGradient>
          <linearGradient id="dc-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CHART.areaTop} />
            <stop offset="100%" stopColor={CHART.areaBottom} />
          </linearGradient>
        </defs>

        {/* сетка: три пунктирные линии (0 / 50 / 100 %), слева — тонны, справа — часы */}
        {GRID_F.map((f) => (
          <g key={`g-${f}`}>
            <line
              x1={pad.l}
              x2={W - pad.r}
              y1={base - f * ih}
              y2={base - f * ih}
              stroke={CHART.grid}
              strokeWidth={1}
              strokeDasharray="3 4"
            />
            <text x={pad.l - 8} y={base - f * ih + 4} textAnchor="end" fontSize="10" fill={CHART.muted}>
              {nf(Math.round(maxT * f))}
            </text>
            <text x={W - pad.r + 8} y={base - f * ih + 4} fontSize="10" fill={CHART.dwell} opacity={0.8}>
              {nf(Math.round(maxD * f))} ч
            </text>
          </g>
        ))}
        {/* базовая ось — чуть ярче сетки */}
        <line x1={pad.l} x2={W - pad.r} y1={base} y2={base} stroke={CHART.axis} strokeWidth={1.5} />

        {/* столбцы обработки: скругление сверху, вертикальный градиент, при
            наведении — ярче, у выбранного — navy со светлой обводкой */}
        <g className="dc-series" opacity={dim("bars")} style={{ transition: "opacity .18s ease" }}>
          {points.map((p, i) => {
            const x = pad.l + step * i + (step - bw) / 2;
            const h = Math.max(ih - (yT(p.processed_t) - pad.t), 1);
            const sel = selectedDay === p.bucket_date;
            const hov = hi === i;
            return (
              <path
                key={`b-${p.bucket_date}`}
                className="dc-bar"
                d={barPath(x, yT(p.processed_t), bw, h, 3)}
                fill={sel ? "url(#dc-bar-sel)" : "url(#dc-bar)"}
                stroke={sel ? CHART.selStroke : "none"}
                strokeWidth={sel ? 1 : 0}
                style={{
                  transformBox: "fill-box",
                  transformOrigin: "bottom",
                  transform: grown ? "scaleY(1)" : "scaleY(0)",
                  // longhand вместо shorthand: shorthand + transitionProperty
                  // перетирают друг друга, а здесь важны и рост, и подсветка
                  transitionProperty: grown ? "transform, filter, opacity" : "none",
                  transitionDuration: grown ? `${ANIM_MS}ms` : "0s",
                  transitionTimingFunction: "cubic-bezier(.22,.75,.25,1)",
                  filter: hov ? "brightness(1.15) saturate(1.05)" : undefined,
                  opacity: hover == null || hov ? 1 : 0.85,
                }}
              />
            );
          })}
        </g>

        {/* засечки отгрузки */}
        <g className="dc-series" opacity={dim("out")} style={{ transition: "opacity .18s ease" }}>
          {points.map((p, i) => {
            const x = pad.l + step * i + (step - bw) / 2;
            return (
              <line
                key={`o-${p.bucket_date}`}
                x1={x - 1}
                x2={x + bw + 1}
                y1={yT(p.outbound_t)}
                y2={yT(p.outbound_t)}
                stroke={CHART.out}
                strokeWidth={1.25}
                strokeLinecap="round"
                opacity={0.75}
              />
            );
          })}
        </g>

        {/* линия среднего времени хранения: сглаженная, рисуется «кисточкой»
            через pathLength=1 + stroke-dashoffset (не нужен getTotalLength) */}
        <g className="dc-series" opacity={dim("dwell")} style={{ transition: "opacity .18s ease" }}>
          <path
            className="dc-area"
            d={areaPath}
            fill="url(#dc-area)"
            style={{ opacity: grown ? 1 : 0, transition: grown ? `opacity ${ANIM_MS}ms ease-out` : "none" }}
          />
          <path
            className="dc-line"
            d={linePath}
            fill="none"
            stroke={CHART.dwell}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            pathLength={1}
            style={{
              strokeDasharray: 1,
              strokeDashoffset: grown ? 0 : 1,
              transition: grown ? "stroke-dashoffset 420ms ease-out" : "none",
            }}
          />
        </g>

        {points.map((p, i) =>
          i % labelEvery === 0 ? (
            <text key={`l-${p.bucket_date}`} x={cx(i)} y={H - 8} textAnchor="middle" fontSize="10" fill={CHART.muted}>
              {dayLabel(p.bucket_date)}
            </text>
          ) : null,
        )}

        {/* перекрестие наведённой колонки + точка на линии хранения */}
        {hi != null && (
          <g pointerEvents="none">
            <line x1={cx(hi)} x2={cx(hi)} y1={pad.t} y2={base} stroke={CHART.cross} strokeWidth={1} />
            <circle cx={cx(hi)} cy={yD(points[hi].avg_dwell_hours)} r={3.5} fill={CHART.dwell} stroke="#fff" strokeWidth={1.5} />
          </g>
        )}

        {/*
          Интерактивный слой рисуем последним: невидимый rect на всю колонку ловит
          клик шире самого столбца, а линии/область хранения не мешают попадать.
          data-bar / data-selected / role / tabindex / aria-* — контракт автотестов.
        */}
        {onSelectDay &&
          points.map((p, i) => {
            const sel = selectedDay === p.bucket_date;
            return (
              <rect
                key={`hit-${p.bucket_date}`}
                className="dc-hit"
                data-bar={p.bucket_date}
                data-selected={sel ? "1" : "0"}
                x={pad.l + step * i}
                y={pad.t}
                width={step}
                height={ih}
                role="button"
                tabIndex={0}
                aria-selected={sel}
                aria-label={`${dayFull(p.bucket_date)}: обработано ${nf(p.processed_t, 1)} т, принято ${nf(
                  p.inbound_t,
                  1,
                )} т, отгружено ${nf(p.outbound_t, 1)} т, документов ${p.docs_count}. ${
                  sel ? "Скрыть детализацию" : "Показать детализацию по складам"
                }`}
                onClick={() => onSelectDay(p.bucket_date)}
                onMouseEnter={() => setHover(i)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover((cur) => (cur === i ? null : cur))}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
                    e.preventDefault();
                    onSelectDay(p.bucket_date);
                  }
                }}
              >
                {/* нативный <title> остаётся фолбэком для доступности (и для
                    автотеста), основной тултип — HTML-карточка ниже */}
                <title>
                  {dayLabel(p.bucket_date)}: обработано {nf(p.processed_t, 1)} т, принято{" "}
                  {nf(p.inbound_t, 1)} т, отгружено {nf(p.outbound_t, 1)} т, документов {p.docs_count}
                </title>
              </rect>
            );
          })}
      </svg>

      {/* Карточка-тултип: матовое стекло, pointer-events none (см. .dc-tip),
          показывается и при наведении мыши, и при фокусе с клавиатуры */}
      {tip && (
        <div
          className="dc-tip"
          data-chart-tip={tip.p.bucket_date}
          style={{
            left: `${(tip.left / W) * 100}%`,
            top: `${tip.top}px`,
            transform: `translate(-50%, ${tip.below ? "0" : "-100%"})`,
          }}
        >
          <div className="dc-tip-title">{dayFull(tip.p.bucket_date)}</div>
          <div className="dc-tip-row">
            <span>обработано</span>
            <b className="num">{nf(tip.p.processed_t, 1)} т</b>
          </div>
          <div className="dc-tip-row">
            <span>принято</span>
            <b className="num">{nf(tip.p.inbound_t, 1)} т</b>
          </div>
          <div className="dc-tip-row">
            <span>отгружено</span>
            <b className="num">{nf(tip.p.outbound_t, 1)} т</b>
          </div>
          <div className="dc-tip-row">
            <span>документов</span>
            <b className="num">{tip.p.docs_count}</b>
          </div>
          <div className="dc-tip-row">
            <span>ср. хранение</span>
            <b className="num">{nf(tip.p.avg_dwell_hours, 1)} ч</b>
          </div>
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-ink-300">
        {LEGEND.map((l) => (
          <button
            key={l.key}
            type="button"
            className="dc-pill"
            data-legend={l.key}
            data-on={focusSeries === l.key ? "1" : "0"}
            onMouseEnter={() => setFocusSeries(l.key)}
            onMouseLeave={() => setFocusSeries(null)}
            onFocus={() => setFocusSeries(l.key)}
            onBlur={() => setFocusSeries(null)}
          >
            <i className={`dc-swatch ${l.swatch}`} style={l.style} aria-hidden="true" /> {l.label}
          </button>
        ))}
        {onSelectDay && (
          <span className="ml-auto text-ink-300">клик по столбцу — детализация за день</span>
        )}
      </div>
    </div>
  );
}
