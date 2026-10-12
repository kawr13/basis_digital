import type { CapacityRow } from "../api";
import { nf, pct } from "../format";

const tone = {
  ok: { bar: "bg-ok", chip: "bg-ok/15 text-ok", label: "в норме" },
  warning: { bar: "bg-warn", chip: "bg-warn/15 text-warn", label: "внимание" },
  critical: { bar: "bg-crit", chip: "bg-crit/15 text-crit", label: "перегруз" },
} as const;

/**
 * Ёмкость складов. Если передан onSelectWarehouse, строка кликабельна —
 * открывается панель детализации по складу (повторный клик по той же строке
 * закрывает её). Без обработатора панель выглядит и ведёт себя как раньше.
 */
export function CapacityPanel({
  rows,
  onSelectWarehouse,
  selectedWarehouseId = null,
}: {
  rows: CapacityRow[];
  onSelectWarehouse?: (id: number) => void;
  selectedWarehouseId?: number | null;
}) {
  if (!rows.length) return <div className="text-sm text-ink-300 py-8 text-center">Нет данных по складам</div>;

  return (
    <ul className="space-y-3.5">
      {rows.map((r) => {
        const body = <RowBody r={r} />;
        if (!onSelectWarehouse) return <li key={r.warehouse_id}>{body}</li>;

        const sel = selectedWarehouseId === r.warehouse_id;
        return (
          <li key={r.warehouse_id}>
            <button
              type="button"
              data-warehouse={r.warehouse_id}
              data-selected={sel ? "1" : "0"}
              aria-selected={sel}
              aria-label={`${r.code} ${r.warehouse}: загрузка ${pct(r.utilization)}, ${tone[r.status].label}. ${
                sel ? "Скрыть детализацию" : "Показать детализацию по складу"
              }`}
              onClick={() => onSelectWarehouse(r.warehouse_id)}
              className={[
                "block w-full text-left rounded-md px-2 -mx-2 py-1.5 transition",
                sel
                  ? "bg-accent/10 ring-1 ring-accent/60"
                  : "hover:bg-ink-800 focus-visible:bg-ink-700",
              ].join(" ")}
            >
              {body}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** Содержимое строки: только span-ы — чтобы кнопку можно было обернуть без вложенных block-элементов. */
function RowBody({ r }: { r: CapacityRow }) {
  const t = tone[r.status];
  return (
    <>
      <span className="flex items-baseline justify-between gap-3">
        <span className="text-sm truncate">
          <span className="num text-ink-300 mr-2">{r.code}</span>
          {r.warehouse}
        </span>
        <span className={`chip ${t.chip}`}>{pct(r.utilization)}</span>
      </span>
      <span className="mt-1.5 block h-2 rounded-sm bg-ink-700 overflow-hidden">
        <span className={`block h-full ${t.bar}`} style={{ width: `${Math.min(r.utilization * 100, 100)}%` }} />
      </span>
      <span className="mt-1 flex justify-between text-[11px] text-ink-300">
        <span className="num">
          занято {nf(r.used, 0)} / {nf(r.capacity, 0)} {r.unit}
        </span>
        <span>
          свободно <span className="num">{nf(r.free, 0)}</span> {r.unit} · {t.label}
        </span>
      </span>
    </>
  );
}
