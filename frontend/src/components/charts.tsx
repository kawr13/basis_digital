/**
 * Графики страницы «Статистика». Все — собственный SVG (без библиотек),
 * общий стиль и поведение — в `chartKit`, там же палитра и тултипы.
 *
 * Один `BarsChart` закрывает половину страницы: столбцы группой, стеком,
 * «в процентах», линия поверх и вторая шкала справа. Так подписи, тултипы,
 * анимация и поведение легенды одинаковые во всех виджетах, а новый график —
 * это массив данных + три пропса, а не новая компонента.
 */
import { useState } from "react";
import { dayLabel, nf } from "../format";
import {
  CHART, ChartEmpty, ChartLegend, ChartTip, arcPath, barPath, colorAt, compact,
  gridLines, labelEvery, niceMax, r2, rectPath, smoothPath, useGrow, useSize,
} from "./chartKit";

/* ================================================================= BarsChart = */

export type BarSeries = {
  key: string;
  label: string;
  color?: string;
  /** линия вместо столбца (например «план» поверх факта) */
  line?: boolean;
  /** шкала справа — для процентов и часов, когда тонны идут слева */
  right?: boolean;
  /** подписывать значения на столбцах (когда их мало) */
  digits?: number;
  unit?: string;
};

export type BarDatum = {
  key: string;
  label: string;
  values: Record<string, number | null | undefined>;
  /** строк тултипа сверх числовых рядов */
  tip?: { label: string; value: string }[];
};

/**
 * Столбцы + линии. `stack` — складывать серии в одну колонку, `percent` —
 * нормировать стек к 100 % (структура), `onSelectKey` — клик по колонке
 * (сутки) для детализации.
 */
export function BarsChart({
  data, series, height = 220, stack = false, percent = false, unit = "",
  onSelectKey, selectedKey, legendNote, ariaLabel,
}: {
  data: BarDatum[];
  series: BarSeries[];
  height?: number;
  stack?: boolean;
  percent?: boolean;
  unit?: string;
  onSelectKey?: (key: string) => void;
  selectedKey?: string | null;
  legendNote?: string;
  ariaLabel?: string;
}) {
  const { ref, width } = useSize<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const grown = useGrow(`${data.length}:${data[0]?.key ?? ""}:${data[data.length - 1]?.key ?? ""}`);

  if (!data.length || !series.length) return <ChartEmpty />;

  const W = Math.max(280, width);
  const H = Math.max(140, height);
  const hasRight = series.some((s) => s.right);
  const pad = { t: 14, r: hasRight ? 44 : 12, b: 26, l: 46 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const base = pad.t + ih;

  const value = (d: BarDatum, s: BarSeries) => {
    const raw = d.values[s.key];
    return raw === null || raw === undefined || !isFinite(raw) ? 0 : raw;
  };
  const total = (d: BarDatum) => series.filter((s) => !s.right && !s.line).reduce((a, s) => a + value(d, s), 0);
  const scaled = (d: BarDatum, s: BarSeries) =>
    percent && !s.right && !s.line && total(d) > 0 ? (value(d, s) / total(d)) * 100 : value(d, s);

  const leftMax = niceOf(Math.max(
    1,
    ...data.map((d) =>
      stack || percent
        ? series.filter((s) => !s.right && !s.line).reduce((a, s) => a + scaled(d, s), 0)
        : Math.max(...series.filter((s) => !s.right && !s.line).map((s) => scaled(d, s)), 0),
    ),
    ...data.flatMap((d) => series.filter((s) => !s.right && s.line).map((s) => scaled(d, s))),
  ));
  const rightMax = niceOf(Math.max(1, ...data.flatMap((d) => series.filter((s) => s.right).map((s) => value(d, s)))));

  const yL = (v: number) => pad.t + ih - (v / leftMax) * ih;
  const yR = (v: number) => pad.t + ih - (v / rightMax) * ih;
  const step = iw / data.length;
  const barSeries = series.filter((s) => !s.line);
  const bw = Math.max(stack || barSeries.length <= 1 ? step * 0.62 : (step * 0.62) / barSeries.length, 2);
  const cx = (i: number) => pad.l + step * (i + 0.5);
  const every = labelEvery(data.length, iw, onSelectKey ? 46 : 58);
  const dim = (key: string) => (focus && focus !== key ? 0.28 : 1);

  const hi = hover != null && hover >= 0 && hover < data.length ? hover : null;
  let tip: { x: number; y: number; below: boolean; d: BarDatum } | null = null;
  if (hi != null) {
    const d = data[hi];
    const top = Math.min(
      ...series.map((s) => (s.right ? yR(value(d, s)) : yL(scaled(d, s)))),
      base,
    );
    const below = top < pad.t + 96;
    tip = { x: cx(hi), y: below ? top + 12 : top - 8, below, d };
  }

  return (
    <div ref={ref} className="relative w-full" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label={ariaLabel ?? "График"}>
        {gridLines.map((f) => (
          <g key={`g${f}`}>
            <line x1={pad.l} x2={W - pad.r} y1={base - f * ih} y2={base - f * ih}
              stroke={CHART.grid} strokeWidth={1} strokeDasharray="3 4" />
            <text x={pad.l - 7} y={base - f * ih + 4} textAnchor="end" fontSize="10" fill={CHART.muted}>
              {compact(leftMax * f, 0)}
            </text>
            {hasRight && (
              <text x={W - pad.r + 7} y={base - f * ih + 4} fontSize="10" fill={CHART.amber} opacity={0.85}>
                {nf(rightMax * f, rightMax < 20 ? 1 : 0)}
              </text>
            )}
          </g>
        ))}
        <line x1={pad.l} x2={W - pad.r} y1={base} y2={base} stroke={CHART.axis} strokeWidth={1.5} />

        {data.map((d, i) => {
          let acc = 0;
          const bars = barSeries.map((s, si) => {
            const v = scaled(d, s);
            const h = Math.max(((v / leftMax) * ih) * (grown ? 1 : 0), 0);
            const x = stack
              ? cx(i) - bw / 2
              : cx(i) - (bw * barSeries.length) / 2 + bw * si;
            const y = stack ? base - acc - h : base - h;
            acc += h;
            const color = s.color ?? colorAt(si);
            return (
              <path
                key={`${d.key}-${s.key}`}
                d={stack && si < barSeries.length - 1 ? rectPath(x, y, bw, h) : barPath(x, y, bw, h, 3)}
                fill={color}
                opacity={dim(s.key) * (hover == null || hover === i ? 1 : 0.82)}
                style={{ transition: "opacity .16s ease" }}
              />
            );
          });
          return (
            <g key={`b-${d.key}`}>
              {bars}
              {selectedKey === d.key && (
                <rect x={pad.l + step * i} y={pad.t} width={step} height={ih}
                  fill="rgba(22,104,168,.10)" pointerEvents="none" />
              )}
            </g>
          );
        })}

        {series.filter((s) => s.line).map((s) => {
          const pts = data.map((d, i) => ({
            x: cx(i),
            y: (s.right ? yR(value(d, s)) : yL(value(d, s))) as number,
          }));
          const d = smoothPath(pts);
          const color = s.color ?? CHART.amber;
          return (
            <g key={`l-${s.key}`} opacity={dim(s.key)} style={{ transition: "opacity .16s ease" }}>
              <path className="dc-line" d={d} fill="none" stroke={color} strokeWidth={2}
                strokeLinecap="round" pathLength={1}
                style={{ strokeDasharray: 1, strokeDashoffset: grown ? 0 : 1, transition: "stroke-dashoffset 420ms ease-out" }} />
              {pts.map((p, i) => (
                <circle key={i} cx={p.x} cy={p.y} r={hi === i ? 4 : 2.4} fill={color} stroke="#fff" strokeWidth={1.2} />
              ))}
            </g>
          );
        })}

        {data.map((d, i) =>
          i % every === 0 ? (
            <text key={`x-${d.key}`} x={cx(i)} y={H - 8} textAnchor="middle" fontSize="10" fill={CHART.muted}>
              {d.label}
            </text>
          ) : null,
        )}

        {hi != null && (
          <line x1={cx(hi)} x2={cx(hi)} y1={pad.t} y2={base} stroke={CHART.cross} strokeWidth={1} pointerEvents="none" />
        )}

        {data.map((d, i) => (
          <rect
            key={`hit-${d.key}`}
            className="dc-hit"
            data-bar={d.key}
            data-selected={selectedKey === d.key ? "1" : "0"}
            x={pad.l + step * i}
            y={pad.t}
            width={step}
            height={ih}
            role={onSelectKey ? "button" : undefined}
            tabIndex={onSelectKey ? 0 : undefined}
            aria-selected={onSelectKey ? selectedKey === d.key : undefined}
            aria-label={`${d.label}: ${series.map((s) => `${s.label} ${nf(value(d, s), s.digits ?? 0)}${s.unit ?? unit}`).join(", ")}`}
            onClick={onSelectKey ? () => onSelectKey(d.key) : undefined}
            onKeyDown={onSelectKey
              ? (e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelectKey(d.key);
                  }
                }
              : undefined}
            onMouseEnter={() => setHover(i)}
            onFocus={() => setHover(i)}
            onBlur={() => setHover((cur) => (cur === i ? null : cur))}
          >
            <title>{`${d.label}: ${series.map((s) => `${s.label} ${nf(value(d, s), s.digits ?? 0)}`).join(", ")}`}</title>
          </rect>
        ))}
      </svg>

      {tip && (
        <ChartTip
          x={tip.x}
          y={tip.y}
          width={W}
          below={tip.below}
          title={tip.d.label}
          rows={[
            ...series.map((s) => ({
              label: s.label,
              value: `${nf(value(tip.d, s), s.digits ?? (unit === "%" || s.right ? 1 : 0))}${s.unit ?? unit}`,
              color: s.color ?? colorAt(series.indexOf(s)),
            })),
            ...(tip.d.tip ?? []),
          ]}
        />
      )}

      <ChartLegend
        items={series.map((s, i) => ({
          key: s.key, label: s.label, color: s.color ?? colorAt(i), dashed: s.line,
        }))}
        focus={focus}
        onFocus={setFocus}
        note={legendNote ?? (onSelectKey ? "клик по столбцу — детализация за день" : undefined)}
      />
    </div>
  );
}

/** 0 и NaN не должны уходить в шкалу. */
function niceOf(v: number) {
  return isFinite(v) && v > 0 ? niceMax(v) : 1;
}

/* ================================================================== Donut ==== */

export type Slice = { label: string; value: number; color?: string };

/** Кольцо структуры: доля груза/операции + наведение с цифрами. */
export function DonutChart({
  items, height = 220, unit = "т", centerLabel = "всего", max = 8, onSelect,
}: {
  items: Slice[];
  height?: number;
  unit?: string;
  centerLabel?: string;
  max?: number;
  onSelect?: (label: string) => void;
}) {
  const { ref, width } = useSize<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const grown = useGrow(items.map((i) => i.label).join("|"));

  const shown = items.filter((i) => i.value > 0);
  if (!shown.length) return <ChartEmpty />;
  const top = shown.slice(0, max);
  const rest = shown.slice(max);
  const slices: Slice[] = rest.length
    ? [...top, { label: `ещё ${rest.length}`, value: rest.reduce((a, i) => a + i.value, 0), color: CHART.plan }]
    : top;
  const total = slices.reduce((a, i) => a + i.value, 0) || 1;

  const W = Math.max(220, width);
  const H = Math.max(150, height);
  const size = Math.min(W * 0.42, H - 8);
  const cx = size / 2 + 10;
  const cy = H / 2;
  const r = size / 2;
  const ring = Math.max(r * 0.34, 14);

  let angle = 0;
  const arcs = slices.map((s, i) => {
    const sweep = (s.value / total) * Math.PI * 2;
    const arc = { ...s, from: angle, to: angle + sweep, color: s.color ?? colorAt(i), index: i };
    angle += sweep;
    return arc;
  });

  const legendW = W - size - 30;
  const legendRows = Math.min(arcs.length, Math.max(3, Math.floor((H - 10) / 17)));
  const perRow = legendW > 250 ? 2 : 1;
  const legend = arcs.slice(0, legendRows * perRow);

  return (
    <div ref={ref} className="relative w-full" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Структура">
        {arcs.map((a) => (
          <path
            key={a.label}
            d={arcPath(cx, cy, r, a.from, a.to - 0.008, ring)}
            fill={a.color}
            opacity={hover == null || hover === a.index ? 1 : 0.35}
            style={{
              transformBox: "view-box", transformOrigin: `${cx}px ${cy}px`,
              transform: grown ? "scale(1)" : "scale(.6)",
              transition: grown ? "transform 380ms cubic-bezier(.22,.75,.25,1), opacity .16s ease" : "none",
              cursor: onSelect ? "pointer" : "default",
            }}
            onMouseEnter={() => setHover(a.index)}
            onClick={onSelect ? () => onSelect(a.label) : undefined}
          >
            <title>{`${a.label}: ${nf(a.value, 0)} ${unit} (${nf((a.value / total) * 100, 1)}%)`}</title>
          </path>
        ))}
        <text x={cx} y={cy - 2} textAnchor="middle" fontSize="19" fontWeight="600" fill={CHART.text} className="num">
          {compact(total, 1)}
        </text>
        <text x={cx} y={cy + 14} textAnchor="middle" fontSize="10" fill={CHART.muted}>
          {centerLabel}, {unit}
        </text>

        {legend.map((a, i) => {
          const col = Math.floor(i / legendRows);
          const row = i % legendRows;
          const x = size + 24 + col * (legendW / perRow);
          const y = cy - ((Math.min(legend.length, legendRows) - 1) * 16) / 2 + row * 16;
          return (
            <g key={`lg-${a.label}`} transform={`translate(${r2(x)},${r2(y)})`}
              onMouseEnter={() => setHover(a.index)} opacity={hover == null || hover === a.index ? 1 : 0.45}>
              <rect x={0} y={-7} width={9} height={9} rx={2} fill={a.color} />
              <text x={14} y={1} fontSize="10.5" fill={CHART.muted}>
                {a.label.length > 16 ? `${a.label.slice(0, 15)}…` : a.label}
              </text>
              <text x={legendW / perRow - 12} y={1} fontSize="10.5" textAnchor="end" fill={CHART.text} className="num">
                {nf((a.value / total) * 100, 1)}%
              </text>
            </g>
          );
        })}
      </svg>
      {hover != null && arcs[hover] && (
        <div className="mt-1 text-[11px] text-ink-300">
          <span className="text-ink-100">{arcs[hover].label}</span> · {nf(arcs[hover].value, 0)} {unit} ·{" "}
          {nf((arcs[hover].value / total) * 100, 1)}%
        </div>
      )}
    </div>
  );
}

/* ================================================================== HBars === */

export type HBarItem = { label: string; value: number; sub?: string; color?: string; tip?: string };

/**
 * Горизонтальные полосы — топы (суда, грузы, станции): подпись читается
 * целиком, порядок виден сверху вниз.
 */
export function HBarsChart({
  items, unit = "", height = 220, max = 12, color, valueDigits = 0, showShare = true,
  onSelectKey, selectedKey,
}: {
  items: HBarItem[];
  unit?: string;
  height?: number;
  max?: number;
  color?: string;
  valueDigits?: number;
  showShare?: boolean;
  /** Клик по полосе — таблица строк, из которых она посчитана. */
  onSelectKey?: (label: string) => void;
  selectedKey?: string | null;
}) {
  const rows = items.filter((i) => i.value > 0).slice(0, max);
  if (!rows.length) return <ChartEmpty />;
  const top = Math.max(...rows.map((r) => r.value), 1);
  const total = rows.reduce((a, r) => a + r.value, 0) || 1;
  return (
    <div className="overflow-y-auto" style={{ maxHeight: height }}>
      <ul className="space-y-1.5">
        {rows.map((r, i) => (
          <li key={r.label} className="group" title={r.tip}>
            <div
              className={`flex items-baseline justify-between gap-2 text-[11px] ${
                onSelectKey ? "cursor-pointer rounded-sm hover:bg-brand-sky/10" : ""
              } ${selectedKey === r.label ? "ring-1 ring-accent" : ""}`}
              role={onSelectKey ? "button" : undefined}
              tabIndex={onSelectKey ? 0 : undefined}
              data-bar={onSelectKey ? r.label : undefined}
              aria-label={onSelectKey ? `${r.label}: ${nf(r.value, valueDigits)} ${unit}` : undefined}
              onClick={onSelectKey ? () => onSelectKey(r.label) : undefined}
              onKeyDown={
                onSelectKey
                  ? (e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelectKey(r.label);
                      }
                    }
                  : undefined
              }
            >
              <span className="truncate text-ink-100" style={{ maxWidth: "58%" }}>
                <span className="text-ink-300 num mr-1">{i + 1}</span>
                {r.label}
              </span>
              <span className="num shrink-0 text-ink-300">
                <b className="text-ink-100">{nf(r.value, valueDigits)}</b>
                {unit && ` ${unit}`}
                {showShare && ` · ${nf((r.value / total) * 100, 1)}%`}
                {r.sub && <span className="ml-1 text-ink-300">· {r.sub}</span>}
              </span>
            </div>
            <div className="mt-1 h-2 rounded-sm" style={{ background: CHART.track }}>
              <div
                className="h-2 rounded-sm"
                style={{
                  width: `${Math.max((r.value / top) * 100, 1.5)}%`,
                  background: r.color ?? color ?? colorAt(i),
                  transition: "width .38s cubic-bezier(.22,.75,.25,1)",
                }}
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ================================================================= Bullet === */

export type BulletItem = {
  label: string;
  fact: number;
  plan: number;
  sub?: string;
  /** чем открывать панель при клике (например ISO-дата); если не задан — label */
  key?: string;
};

/**
 * «Пистон»: план — светлая дорожка, факт — тёмная полоса, сверху процент.
 * Компактный ответ на вопрос «где не доработали», когда дней много.
 */
export function BulletChart({
  items, height = 240, unit = "т", max = 14, onSelectKey, selectedKey,
}: {
  items: BulletItem[];
  height?: number;
  unit?: string;
  max?: number;
  onSelectKey?: (key: string) => void;
  selectedKey?: string | null;
}) {
  const rows = items.filter((i) => i.plan > 0 || i.fact > 0).slice(0, max);
  if (!rows.length) return <ChartEmpty />;
  const scale = Math.max(...rows.map((r) => Math.max(r.plan, r.fact)), 1);
  return (
    <div className="overflow-y-auto" style={{ maxHeight: height }}>
      <ul className="space-y-2">
        {rows.map((r) => {
          const pct = r.plan > 0 ? (r.fact / r.plan) * 100 : null;
          const tone = pct === null ? CHART.muted : pct >= 100 ? CHART.ok : pct >= 80 ? CHART.accent : CHART.crit;
          return (
            <li key={r.label}>
              <div
                className={`flex items-baseline justify-between text-[11px] ${
                  onSelectKey ? "cursor-pointer rounded-sm hover:bg-brand-sky/10" : ""
                } ${selectedKey === r.label ? "ring-1 ring-accent" : ""}`}
                role={onSelectKey ? "button" : undefined}
                tabIndex={onSelectKey ? 0 : undefined}
                data-bar={onSelectKey ? r.key ?? r.label : undefined}
                onClick={onSelectKey ? () => onSelectKey(r.key ?? r.label) : undefined}
                onKeyDown={
                  onSelectKey
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onSelectKey(r.key ?? r.label);
                        }
                      }
                    : undefined
                }
              >
                <span className="text-ink-300">{r.label}</span>
                <span className="num">
                  <b style={{ color: tone }}>{pct === null ? "—" : `${nf(pct, 0)}%`}</b>
                  <span className="ml-1.5 text-ink-300">
                    {nf(r.fact, 0)} / {nf(r.plan, 0)} {unit}
                  </span>
                </span>
              </div>
              <div className="relative mt-1 h-3 rounded-sm" style={{ background: CHART.track }}>
                <div className="h-3 rounded-sm" style={{
                  width: `${Math.max((r.fact / scale) * 100, 0.8)}%`,
                  background: tone,
                  transition: "width .38s cubic-bezier(.22,.75,.25,1)",
                }} />
                {r.plan > 0 && (
                  <div className="absolute top-0 h-3 w-[2px]" style={{ left: `${(r.plan / scale) * 100}%`, background: CHART.navy }}
                    title={`план ${nf(r.plan, 0)} ${unit}`} />
                )}
              </div>
              {r.sub && <div className="mt-0.5 text-[10px] text-ink-300">{r.sub}</div>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* ================================================================= Gauge ==== */

/** Полукруговой «спидометр»: выполнение плана, явка, исправность. */
export function GaugeChart({
  value, max = 100, label, sub, height = 150, thresholds = [80, 95], invert = false,
}: {
  value: number | null;
  max?: number;
  label: string;
  sub?: string;
  height?: number;
  /** [внимание, критично] в тех же единицах, что и value */
  thresholds?: [number, number];
  /** true — чем меньше, тем лучше (потери времени) */
  invert?: boolean;
}) {
  const { ref, width } = useSize<HTMLDivElement>(160);
  const grown = useGrow(`${label}:${value ?? 0}`);
  const v = value ?? 0;
  const W = Math.max(160, width);
  const H = Math.max(110, height);
  const r = Math.min(W / 2 - 6, H - 26);
  const cx = W / 2;
  const cy = H - 14;
  const span = Math.PI;
  const frac = Math.max(0, Math.min(v / (max || 1), 1));
  const toneOf = (x: number) => {
    const [warn, crit] = thresholds;
    if (invert) return x > crit ? CHART.crit : x > warn ? CHART.warn : CHART.ok;
    return x >= crit ? CHART.ok : x >= warn ? CHART.accent : CHART.crit;
  };
  const tone = value === null ? CHART.muted : toneOf(v);

  return (
    <div ref={ref} className="w-full">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label={`${label}: ${nf(v, 1)}`}>
        <path d={arcPath(cx, cy, r, -span / 2, span / 2, Math.max(r * 0.22, 9))} fill={CHART.track} />
        <path
          d={arcPath(cx, cy, r, -span / 2, -span / 2 + span * frac * (grown ? 1 : 0), Math.max(r * 0.22, 9))}
          fill={tone}
          style={{ transition: "d 380ms ease-out" }}
        />
        <text x={cx} y={cy - 6} textAnchor="middle" fontSize="22" fontWeight="600" fill={tone} className="num">
          {value === null ? "—" : nf(v, v < 10 ? 1 : 0)}
        </text>
        <text x={cx} y={cy + 10} textAnchor="middle" fontSize="10" fill={CHART.muted}>{label}</text>
      </svg>
      {sub && <div className="-mt-1 text-center text-[11px] text-ink-300">{sub}</div>}
    </div>
  );
}

/* =============================================================== Heatmap ==== */

export function HeatmapChart({
  rows, cols, value, format, height = 200, colorFor,
}: {
  rows: string[];
  cols: string[];
  value: (row: string, col: string) => number | null;
  format: (v: number) => string;
  height?: number;
  colorFor?: (v: number, max: number) => string;
}) {
  const cells = rows.flatMap((r) => cols.map((c) => value(r, c))).filter((v): v is number => v != null);
  if (!cells.length) return <ChartEmpty />;
  const max = Math.max(...cells, 1);
  const color = colorFor ?? ((v: number, m: number) => `rgba(22,104,168,${0.08 + 0.72 * Math.sqrt(v / m)})`);
  return (
    <div className="overflow-auto" style={{ maxHeight: height }}>
      <table className="w-full border-separate" style={{ borderSpacing: 3 }}>
        <thead>
          <tr>
            <th />
            {cols.map((c) => (
              <th key={c} className="panel-title px-1 pb-1 text-center font-normal">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r}>
              <th className="whitespace-nowrap pr-1 text-right text-[11px] font-normal text-ink-300">{r}</th>
              {cols.map((c) => {
                const v = value(r, c);
                return (
                  <td key={c} className="h-8 rounded text-center text-[11px] num"
                    style={{
                      background: v == null ? CHART.track : color(v, max),
                      color: v != null && v / max > 0.62 ? "#fff" : CHART.text,
                    }}
                    title={`${r} · ${c}: ${v == null ? "нет данных" : format(v)}`}>
                    {v == null ? "" : format(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ============================================================== Scatter ===== */

export type ScatterPoint = { x: number; y: number; size?: number; label: string; color?: string; tip?: string };

/** Пузырьковая диаграмма: машинное время × производительность, размер = тоннаж. */
export function ScatterChart({ points, height = 260, xLabel, yLabel, xUnit = "", yUnit = "" }: {
  points: ScatterPoint[];
  height?: number;
  xLabel: string;
  yLabel: string;
  xUnit?: string;
  yUnit?: string;
}) {
  const { ref, width } = useSize<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const grown = useGrow(points.map((p) => p.label).join("|"));
  if (!points.length) return <ChartEmpty />;

  const W = Math.max(260, width);
  const H = Math.max(160, height);
  const pad = { t: 14, r: 16, b: 30, l: 46 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const maxX = niceMax(Math.max(...points.map((p) => p.x), 1));
  const maxY = niceMax(Math.max(...points.map((p) => p.y), 1));
  const maxSize = Math.max(...points.map((p) => p.size ?? 1), 1);
  const x = (v: number) => pad.l + (v / maxX) * iw;
  const y = (v: number) => pad.t + ih - (v / maxY) * ih;

  return (
    <div ref={ref} className="relative w-full" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label={`${yLabel} от ${xLabel}`}>
        {gridLines.map((f) => (
          <g key={`h${f}`}>
            <line x1={pad.l} x2={W - pad.r} y1={y(f * maxY)} y2={y(f * maxY)} stroke={CHART.grid} strokeDasharray="3 4" />
            <text x={pad.l - 6} y={y(f * maxY) + 4} textAnchor="end" fontSize="10" fill={CHART.muted}>{compact(f * maxY, 0)}</text>
          </g>
        ))}
        {gridLines.map((f) => (
          <text key={`v${f}`} x={x(f * maxX)} y={H - 14} textAnchor="middle" fontSize="10" fill={CHART.muted}>
            {compact(f * maxX, 0)}
          </text>
        ))}
        <line x1={pad.l} x2={W - pad.r} y1={pad.t + ih} y2={pad.t + ih} stroke={CHART.axis} strokeWidth={1.5} />
        <line x1={pad.l} x2={pad.l} y1={pad.t} y2={pad.t + ih} stroke={CHART.axis} strokeWidth={1.5} />

        {points.map((p, i) => (
          <circle
            key={p.label}
            cx={x(p.x)}
            cy={y(p.y)}
            r={(grown ? 1 : 0) * (5 + 13 * Math.sqrt((p.size ?? 1) / maxSize))}
            fill={p.color ?? colorAt(i)}
            fillOpacity={hover == null || hover === i ? 0.72 : 0.25}
            stroke={p.color ?? colorAt(i)}
            strokeWidth={1.2}
            style={{ transition: "r 360ms cubic-bezier(.22,.75,.25,1), fill-opacity .16s ease" }}
            onMouseEnter={() => setHover(i)}
          >
            <title>{p.tip ?? `${p.label}: ${xLabel} ${nf(p.x, 1)}${xUnit}, ${yLabel} ${nf(p.y, 1)}${yUnit}`}</title>
          </circle>
        ))}
        <text x={pad.l + iw / 2} y={H - 2} textAnchor="middle" fontSize="10" fill={CHART.muted}>{xLabel}</text>
        <text x={10} y={pad.t + ih / 2} fontSize="10" fill={CHART.muted}
          transform={`rotate(-90 10 ${pad.t + ih / 2})`} textAnchor="middle">{yLabel}</text>
      </svg>
      {hover != null && points[hover] && (
        <div className="mt-1 truncate text-[11px] text-ink-300">
          <span className="text-ink-100">{points[hover].label}</span> · {nf(points[hover].x, 1)}{xUnit} ·{" "}
          {nf(points[hover].y, 1)}{yUnit}
        </div>
      )}
    </div>
  );
}

/* ================================================================ Radar ==== */

export function RadarChart({
  axes, series, height = 260,
}: {
  /** максимальная база каждой оси (100 = «лучшее значение периода») */
  axes: { key: string; label: string }[];
  series: { name: string; color: string; values: Record<string, number> }[];
  height?: number;
}) {
  const { ref, width } = useSize<HTMLDivElement>();
  const grown = useGrow(series.map((s) => s.name).join("|"));
  if (!axes.length || !series.length) return <ChartEmpty />;

  const W = Math.max(240, width);
  const H = Math.max(180, height);
  const cx = W / 2;
  const cy = H / 2 + 4;
  const r = Math.min(W / 2 - 54, H / 2 - 26);
  const angle = (i: number) => (Math.PI * 2 * i) / axes.length - Math.PI / 2;
  const point = (i: number, frac: number) => ({
    x: cx + Math.cos(angle(i)) * r * frac,
    y: cy + Math.sin(angle(i)) * r * frac,
  });

  return (
    <div ref={ref} className="w-full">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Сравнение смен">
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <polygon key={f}
            points={axes.map((_, i) => { const p = point(i, f); return `${r2(p.x)},${r2(p.y)}`; }).join(" ")}
            fill="none" stroke={CHART.grid} />
        ))}
        {axes.map((a, i) => {
          const p = point(i, 1);
          const lp = point(i, 1.16);
          return (
            <g key={a.key}>
              <line x1={cx} y1={cy} x2={p.x} y2={p.y} stroke={CHART.grid} />
              <text x={lp.x} y={lp.y + 3} fontSize="10" fill={CHART.muted}
                textAnchor={Math.abs(lp.x - cx) < 12 ? "middle" : lp.x > cx ? "start" : "end"}>
                {a.label}
              </text>
            </g>
          );
        })}
        {series.map((s) => {
          const d = axes.map((a, i) => {
            const p = point(i, Math.max(0.02, Math.min(s.values[a.key] ?? 0, 1)) * (grown ? 1 : 0));
            return `${r2(p.x)},${r2(p.y)}`;
          }).join(" ");
          return (
            <polygon key={s.name} points={d} fill={s.color} fillOpacity={0.18}
              stroke={s.color} strokeWidth={2} strokeLinejoin="round"
              style={{ transition: "all 380ms cubic-bezier(.22,.75,.25,1)" }}>
              <title>{`${s.name}: ${axes.map((a) => `${a.label} ${nf((s.values[a.key] ?? 0) * 100, 0)}%`).join(", ")}`}</title>
            </polygon>
          );
        })}
      </svg>
      <div className="flex flex-wrap justify-center gap-3 text-[11px] text-ink-300">
        {series.map((s) => (
          <span key={s.name} className="inline-flex items-center gap-1.5">
            <i className="dc-swatch" style={{ width: 10, height: 10, background: s.color }} /> {s.name}
          </span>
        ))}
        <span className="text-ink-300">— доля от лучшего значения периода</span>
      </div>
    </div>
  );
}

/* ============================================================ DayLabels ==== */

/** Подсказка: «05.11» для оси, если данные приходят сутками. */
export const dayAxis = (iso: string) => dayLabel(iso);

/* ============================================================== Table ==== */

export function TableLite({ head, children, foot }: {
  head: { label: string; align?: "right" | "center"; title?: string }[];
  children: React.ReactNode;
  foot?: React.ReactNode;
}) {
  return (
    <div className="overflow-auto">
      <table className="w-full text-xs">
        <thead className="sticky top-0 z-[1] bg-ink-800/90 backdrop-blur">
          <tr>
            {head.map((h) => (
              <th key={h.label} title={h.title}
                className={`panel-title whitespace-nowrap px-2 py-1.5 font-normal ${h.align === "right" ? "text-right" : h.align === "center" ? "text-center" : "text-left"}`}>
                {h.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
        {foot && <tfoot>{foot}</tfoot>}
      </table>
    </div>
  );
}

export const Td = ({ children, right, className = "", title, colSpan, onClick }: {
  children?: React.ReactNode; right?: boolean; className?: string; title?: string; colSpan?: number;
  onClick?: () => void;
}) => (
  <td
    title={title}
    colSpan={colSpan}
    onClick={onClick}
    className={`px-2 py-1.5 align-top ${right ? "text-right num" : ""} ${className}`}
  >
    {children}
  </td>
);

const TONE_CLASS = { ok: "text-ok", warn: "text-warn", crit: "text-crit" } as const;

export const Tr = ({ children, tone }: { children?: React.ReactNode; tone?: keyof typeof TONE_CLASS }) => (
  <tr className={`border-t border-ink-700 hover:bg-ink-800/70 ${tone ? TONE_CLASS[tone] : ""}`}>{children}</tr>
);
