import type { CapacityRow } from "../api";
import { nf, pct } from "../format";

const tone = {
  ok: { bar: "bg-ok", chip: "bg-ok/15 text-ok", label: "в норме" },
  warning: { bar: "bg-warn", chip: "bg-warn/15 text-warn", label: "внимание" },
  critical: { bar: "bg-crit", chip: "bg-crit/15 text-crit", label: "перегруз" },
} as const;

export function CapacityPanel({ rows }: { rows: CapacityRow[] }) {
  if (!rows.length) return <div className="text-sm text-ink-400 py-8 text-center">Нет данных по складам</div>;

  return (
    <ul className="space-y-3.5">
      {rows.map((r) => {
        const t = tone[r.status];
        return (
          <li key={r.warehouse_id}>
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm truncate">
                <span className="num text-ink-400 mr-2">{r.code}</span>
                {r.warehouse}
              </span>
              <span className={`chip ${t.chip}`}>{pct(r.utilization)}</span>
            </div>
            <div className="mt-1.5 h-2 rounded-sm bg-ink-700 overflow-hidden">
              <div className={`h-full ${t.bar}`} style={{ width: `${Math.min(r.utilization * 100, 100)}%` }} />
            </div>
            <div className="mt-1 flex justify-between text-[11px] text-ink-400">
              <span className="num">
                занято {nf(r.used, 0)} / {nf(r.capacity, 0)} {r.unit}
              </span>
              <span>
                свободно <span className="num">{nf(r.free, 0)}</span> {r.unit} · {t.label}
              </span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
