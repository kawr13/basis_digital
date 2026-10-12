import type { CargoRow } from "../api";
import { nf } from "../format";

export function CargoTable({ rows }: { rows: CargoRow[] }) {
  if (!rows.length) return <div className="text-sm text-ink-300 py-8 text-center">Нет остатков</div>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wider text-ink-400 bg-ink-800">
            <th className="py-2 pr-3 font-medium">Склад</th>
            <th className="py-2 pr-3 font-medium">Группа груза</th>
            <th className="py-2 pr-3 font-medium text-right">Мест</th>
            <th className="py-2 pr-3 font-medium text-right">Вес, т</th>
            <th className="py-2 pr-3 font-medium text-right">Просрочка, т</th>
            <th className="py-2 font-medium text-right">Ср. срок, сут</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.warehouse_id}-${r.cargo_group}-${i}`} className="border-t border-ink-700 hover:bg-ink-800">
              <td className="py-2 pr-3 text-ink-300">{r.warehouse}</td>
              <td className="py-2 pr-3">{r.cargo_group}</td>
              <td className="py-2 pr-3 num text-right text-ink-300">{nf(r.qty)}</td>
              <td className="py-2 pr-3 num text-right font-medium">{nf(r.weight_t, 1)}</td>
              <td className={`py-2 pr-3 num text-right ${r.overdue_weight_t > 0 ? "text-crit" : "text-ink-300"}`}>
                {r.overdue_weight_t > 0 ? nf(r.overdue_weight_t, 1) : "—"}
              </td>
              <td className="py-2 num text-right text-ink-300">{nf(r.avg_age_days, 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
