import type { DynamicsPoint } from "../api";
import { dayLabel, nf } from "../format";

/**
 * Полосы — объём обработки за сутки, пунктир — среднее время хранения.
 * SVG рисуем сами: лёгкий компонент, без тяжёлых графических библиотек.
 */
export function DynamicsChart({ points }: { points: DynamicsPoint[] }) {
  if (!points.length) {
    return <div className="text-sm text-ink-400 py-10 text-center">Нет данных о обработке</div>;
  }

  const W = 760;
  const H = 220;
  const pad = { t: 14, r: 42, b: 26, l: 46 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;

  const maxT = Math.max(...points.map((p) => Math.max(p.processed_t, p.inbound_t, p.outbound_t)), 1);
  const maxD = Math.max(...points.map((p) => p.avg_dwell_hours), 1);
  const step = iw / points.length;
  const bw = Math.max(step * 0.55, 3);

  const yT = (v: number) => pad.t + ih - (v / maxT) * ih;
  const yD = (v: number) => pad.t + ih - (v / maxD) * ih;
  const line = points
    .map((p, i) => `${pad.l + step * (i + 0.5)},${yD(p.avg_dwell_hours)}`)
    .join(" ");

  const gridVals = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(maxT * f));

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-[220px]" role="img" aria-label="Динамика обработки">
        {gridVals.map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={W - pad.r} y1={yT(v)} y2={yT(v)} stroke="#25303d" strokeWidth={1} />
            <text x={pad.l - 8} y={yT(v) + 4} textAnchor="end" fontSize="10" fill="#6b7a8d">
              {nf(v)}
            </text>
          </g>
        ))}

        {points.map((p, i) => {
          const x = pad.l + step * i + (step - bw) / 2;
          const h = Math.max(ih - (yT(p.processed_t) - pad.t), 1);
          return (
            <g key={p.bucket_date}>
              <rect x={x} y={yT(p.processed_t)} width={bw} height={h} fill="#f0a02c" opacity={0.85} rx={1.5}>
                <title>
                  {dayLabel(p.bucket_date)}: обработано {nf(p.processed_t, 1)} т, принято {nf(p.inbound_t, 1)} т,
                  отгружено {nf(p.outbound_t, 1)} т, документов {p.docs_count}
                </title>
              </rect>
              <line x1={x - 1} x2={x + bw + 1} y1={yT(p.outbound_t)} y2={yT(p.outbound_t)}
                stroke="#9aa8b8" strokeWidth={1} opacity={0.7} />
            </g>
          );
        })}

        <polyline points={line} fill="none" stroke="#3f9fae" strokeWidth={1.5} strokeDasharray="4 3" />

        {points.map((p, i) =>
          i % Math.ceil(points.length / 7) === 0 ? (
            <text key={`l-${p.bucket_date}`} x={pad.l + step * (i + 0.5)} y={H - 8}
              textAnchor="middle" fontSize="10" fill="#6b7a8d">
              {dayLabel(p.bucket_date)}
            </text>
          ) : null,
        )}

        <text x={W - pad.r + 6} y={pad.t + 6} fontSize="10" fill="#3f9fae">
          {nf(maxD)} ч
        </text>
        <text x={W - pad.r + 6} y={pad.t + ih} fontSize="10" fill="#3f9fae">
          0 ч
        </text>
      </svg>

      <div className="mt-2 flex flex-wrap items-center gap-4 text-[11px] text-ink-400">
        <span className="flex items-center gap-1.5">
          <i className="inline-block w-3 h-3 rounded-sm bg-amber2" /> обработано, т
        </span>
        <span className="flex items-center gap-1.5">
          <i className="inline-block w-3 h-[2px] bg-teal2" /> среднее время хранения, ч
        </span>
        <span className="flex items-center gap-1.5">
          <i className="inline-block w-3 h-[2px] bg-ink-300" /> отгружено, т
        </span>
      </div>
    </div>
  );
}
