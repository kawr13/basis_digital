import { useId } from "react";
import { useSize } from "./chartKit";

/**
 * Мини-график (спарклайн) для KpiCard: 7–14 значений, по умолчанию 32 px высотой.
 *
 * Ширину берёт общий `useSize` (TASKS2.md п.30): тот же ResizeObserver с
 * rAF-троттлом, что у остальных графиков, а viewBox строится в реальную ширину —
 * ничего не растягивается «резинкой». У спарклайна нет подписей и осей, поэтому
 * вид не меняется, а высота (заявленный размер) остаётся точной. Значения
 * нормализуются по min/max, поэтому ряд видно, даже когда цифры отличаются в разы.
 */
export function Sparkline({
  values,
  height = 32,
  label = "динамика за период",
}: {
  values: number[];
  height?: number;
  /** подпись ряда для скринридера */
  label?: string;
}) {
  // min=120: карточки KpiCard бывают узкими — clamp выше только сжал бы viewBox
  const gid = useId();
  const { ref, width } = useSize<HTMLDivElement>(120);
  if (values.length < 3) return null;

  const W = Math.round(width);
  const H = height;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const px = (i: number) => (i / (values.length - 1)) * W;
  const py = (v: number) => H - 3 - ((v - min) / span) * (H - 6);

  const line = values.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ");
  const area = `0,${H} ${line} ${W},${H}`;

  return (
    <div ref={ref} className="mt-2">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        preserveAspectRatio="none"
        className="block"
        role="img"
        aria-label={label}
        data-sparkline={values.length}
      >
        <defs>
          <linearGradient id={`sp-${gid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#337ab7" stopOpacity={0.28} />
            <stop offset="100%" stopColor="#337ab7" stopOpacity={0} />
          </linearGradient>
        </defs>
        <polygon points={area} fill={`url(#sp-${gid})`} />
        <polyline
          points={line}
          fill="none"
          stroke="#337ab7"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
}
