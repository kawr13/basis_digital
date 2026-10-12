import { useEffect, useRef, useState } from "react";
import type { DynamicsPoint } from "../api";
import { dayFull, dayLabel, nf } from "../format";

/**
 * Палитра графика повторяет tailwind-палитру (ink / accent): держим её здесь,
 * чтобы SVG-оси не разъезжались с цветами панелей.
 */
const CHART = {
  grid: "rgba(255,255,255,.10)", // линии сетки — светлая линия поверх стекла
  axis: "rgba(127,151,189,.35)", // ink-400 полупрозрачный — базовая ось
  muted: "#7f97bd", // ink-400 — подписи осей
  barTop: "#f0a02c", // amber2 — обычные столбцы оставляем янтарными (6.8:1, синий даёт ~3:1)
  barBottom: "#c9781c", // низ столбца — тот же янтарь, только глубже (градиент сверху вниз)
  selTop: "#2fbaec", // accent — выбранный столбец: фирменный голубой сайта («мы здесь»)
  selBottom: "#337ab7", // brand.mid — низ выбранного столбца (тот же голубой, глубже)
  selStroke: "#b9c9e2", // ink-300 — тонкая рамка выбранного столбца
  dwell: "#2fbaec", // accent — среднее время хранения
  out: "#b9c9e2", // ink-300 — отгрузка
} as const;

/**
 * Полосы — объём обработки за сутки (янтарь), голубой пунктир — среднее время
 * хранения, светлая черта — отгрузка; выбранный столбец — фирменный голубой.
 *
 * SVG рисуется в реальных пикселях (viewBox = фактическая ширина контейнера),
 * поэтому текст не растягивается, когда плашку растягивают мышью.
 *
 * Каждый столбец кликабелен (если передан onSelectDay): клик открывает
 * детализацию за сутки, повторный клик по тому же столбцу — закрывает.
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

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const apply = () => setWidth(Math.max(320, el.clientWidth));
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (!points.length) {
    return <div className="text-sm text-ink-300 py-10 text-center">Нет данных о обработке</div>;
  }

  const W = Math.max(320, width);
  const H = Math.max(160, height);
  const pad = { t: 14, r: 44, b: 26, l: 46 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;

  const maxT = Math.max(...points.map((p) => Math.max(p.processed_t, p.inbound_t, p.outbound_t)), 1);
  const maxD = Math.max(...points.map((p) => p.avg_dwell_hours), 1);
  const step = iw / points.length;
  const bw = Math.max(step * 0.55, 3);

  const yT = (v: number) => pad.t + ih - (v / maxT) * ih;
  const yD = (v: number) => pad.t + ih - (v / maxD) * ih;
  const line = points.map((p, i) => `${pad.l + step * (i + 0.5)},${yD(p.avg_dwell_hours)}`).join(" ");
  // голубая подсветка под линией хранения (замкнутый контур по нижней границе)
  const xFirst = pad.l + step * 0.5;
  const xLast = pad.l + step * (points.length - 0.5);
  const area = `${xFirst},${pad.t + ih} ${line} ${xLast},${pad.t + ih}`;
  const gridVals = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(maxT * f));
  const labelEvery = Math.ceil(points.length / Math.max(4, Math.floor(iw / 70)));

  return (
    <div ref={boxRef} className="w-full">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Динамика обработки">
        <defs>
          <linearGradient id="dc-bar" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CHART.barTop} stopOpacity={0.95} />
            <stop offset="100%" stopColor={CHART.barBottom} stopOpacity={0.85} />
          </linearGradient>
          <linearGradient id="dc-bar-sel" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CHART.selTop} stopOpacity={0.95} />
            <stop offset="100%" stopColor={CHART.selBottom} stopOpacity={0.85} />
          </linearGradient>
          <linearGradient id="dc-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CHART.dwell} stopOpacity={0.22} />
            <stop offset="100%" stopColor={CHART.dwell} stopOpacity={0} />
          </linearGradient>
        </defs>
        {gridVals.map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={W - pad.r} y1={yT(v)} y2={yT(v)} stroke={CHART.grid} strokeWidth={1} />
            <text x={pad.l - 8} y={yT(v) + 4} textAnchor="end" fontSize="10" fill={CHART.muted}>
              {nf(v)}
            </text>
          </g>
        ))}
        {/* базовая ось — чуть ярче сетки; под ней голубая подсветка низа графика */}
        <line x1={pad.l} x2={W - pad.r} y1={pad.t + ih} y2={pad.t + ih} stroke={CHART.axis} strokeWidth={1.5} />

        {points.map((p, i) => {
          const x = pad.l + step * i + (step - bw) / 2;
          const h = Math.max(ih - (yT(p.processed_t) - pad.t), 1);
          const sel = selectedDay === p.bucket_date;
          return (
            <g key={p.bucket_date}>
              <rect
                x={x}
                y={yT(p.processed_t)}
                width={bw}
                height={h}
                fill={sel ? "url(#dc-bar-sel)" : "url(#dc-bar)"}
                rx={1.5}
                stroke={sel ? CHART.selStroke : "none"}
                strokeWidth={sel ? 1 : 0}
              >
                <title>
                  {dayLabel(p.bucket_date)}: обработано {nf(p.processed_t, 1)} т, принято{" "}
                  {nf(p.inbound_t, 1)} т, отгружено {nf(p.outbound_t, 1)} т, документов {p.docs_count}
                </title>
              </rect>
              <line
                x1={x - 1}
                x2={x + bw + 1}
                y1={yT(p.outbound_t)}
                y2={yT(p.outbound_t)}
                stroke={CHART.out}
                strokeWidth={1}
                opacity={0.7}
              />
            </g>
          );
        })}

        <polygon points={area} fill="url(#dc-area)" />
        <polyline points={line} fill="none" stroke={CHART.dwell} strokeWidth={1.5} strokeDasharray="4 3" />

        {points.map((p, i) =>
          i % labelEvery === 0 ? (
            <text
              key={`l-${p.bucket_date}`}
              x={pad.l + step * (i + 0.5)}
              y={H - 8}
              textAnchor="middle"
              fontSize="10"
              fill={CHART.muted}
            >
              {dayLabel(p.bucket_date)}
            </text>
          ) : null,
        )}

        <text x={W - pad.r + 6} y={pad.t + 6} fontSize="10" fill={CHART.dwell}>
          {nf(maxD)} ч
        </text>
        <text x={W - pad.r + 6} y={pad.t + ih} fontSize="10" fill={CHART.dwell}>
          0 ч
        </text>
        {/*
          Интерактивный слой рисуем последним: невидимый rect на всю колонку ловит
          клик шире самого столбца, а линии/область хранения не мешают попадать.
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
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
                    e.preventDefault();
                    onSelectDay(p.bucket_date);
                  }
                }}
              >
                {/* тултип дублируем на зоне клика: прозрачный rect перекрывает
                    столбец, и без своего <title> наводка мыши ничего бы не показала */}
                <title>
                  {dayLabel(p.bucket_date)}: обработано {nf(p.processed_t, 1)} т, принято{" "}
                  {nf(p.inbound_t, 1)} т, отгружено {nf(p.outbound_t, 1)} т, документов {p.docs_count}
                </title>
              </rect>
            );
          })}

      </svg>

      <div className="mt-2 flex flex-wrap items-center gap-4 text-[11px] text-ink-300">
        <span className="flex items-center gap-1.5">
          <i className="inline-block w-3 h-3 rounded-sm bg-amber2" /> обработано, т
        </span>
        <span className="flex items-center gap-1.5">
          <i className="inline-block w-3 h-[2px] bg-accent" /> среднее время хранения, ч
        </span>
        <span className="flex items-center gap-1.5">
          <i className="inline-block w-3 h-[2px] bg-ink-300" /> отгружено, т
        </span>
        {onSelectDay && (
          <span className="ml-auto text-ink-300">клик по столбцу — детализация за день</span>
        )}
      </div>
    </div>
  );
}
