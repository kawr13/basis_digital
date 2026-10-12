import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type CargoReport, type ProcessingReport } from "../api";
import { asOf, dayFull, dayLabel, dmy, nf } from "../format";

/**
 * Панель детализации («drill-down»).
 *
 * Открывается кликом по столбцу графика (сутки) или строке ёмкости (склад) и
 * показывает разрез по тому измерению, которое НЕ выбрано: за день — цифры по
 * складам, по складу — цифры по дням. Источник — те же отчёты, что и на вкладке
 * «Отчёты» (/api/reports/processing, /api/reports/cargo), поэтому цифры в панели
 * и в отчёте всегда совпадают, а права фильтруются бэкендом по scope пользователя.
 */

export type DrilldownTarget =
  | { kind: "day"; day: string }
  | { kind: "warehouse"; warehouseId: number; warehouseName?: string };

/** За какой период смотреть склад, когда выбран склад (сутки берутся «как есть»). */
const WAREHOUSE_DAYS = 14;

const iso = (d: Date) => d.toISOString().slice(0, 10);
const isoDaysAgo = (n: number) => iso(new Date(Date.now() - (n - 1) * 86400_000));

type Cell = {
  inbound_t: number;
  outbound_t: number;
  processed_t: number;
  docs_count: number;
  avg_dwell_hours: number;
};

type Row = Cell & { key: string; label: string; sub?: string };

export function DrilldownPanel({
  target,
  scopeIds,
  onClose,
}: {
  target: DrilldownTarget;
  /** доступные пользователю склады; пусто = все склады (роль top) */
  scopeIds: number[];
  onClose: () => void;
}) {
  // примитивы вместо объекта target: иначе эффект зависел бы от нового объекта
  // на каждый рендер и панель перезагружала бы данные бесконечно
  const day = target.kind === "day" ? target.day : null;
  const warehouseId = target.kind === "warehouse" ? target.warehouseId : null;
  const scopeKey = scopeIds.join(",");

  const [proc, setProc] = useState<ProcessingReport | null>(null);
  const [cargo, setCargo] = useState<CargoReport | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const panelRef = useRef<HTMLElement>(null);
  const returnFocusTo = useRef<HTMLElement | null>(null);
  // номер запроса: если пользователь переключил день/склад, пока запрос в полёте,
  // ответ отсталого запроса не должен перезатереть новые данные
  const seq = useRef(0);

  const period = day
    ? { from: day, to: day }
    : { from: isoDaysAgo(WAREHOUSE_DAYS), to: iso(new Date()) };

  const load = useCallback(async () => {
    const my = ++seq.current;
    setBusy(true);
    try {
      const warehouses =
        warehouseId != null ? [warehouseId] : scopeKey ? scopeKey.split(",").map(Number) : undefined;
      const report = await api.reportProcessing({ from: period.from, to: period.to, warehouses });
      // чем загружен склад — только в режиме «склад»: остатки отдаются по последнему срезу
      const balance = warehouseId != null ? await api.reportCargo({ warehouses: [warehouseId] }) : null;
      if (my !== seq.current) return;
      setProc(report);
      setCargo(balance);
      setError("");
    } catch (e) {
      if (my !== seq.current) return;
      setError(e instanceof Error ? e.message : "Не удалось получить детализацию");
    } finally {
      if (my === seq.current) setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [day, warehouseId, scopeKey, attempt]);

  useEffect(() => {
    load();
  }, [load]);

  // Esc закрывает панель с клавиатуры.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Фокус при открытии уходит в панель; точка возврата (столбец/строка, с которой
  // кликнули) запоминается один раз — при смене выбора внутри панели она та же.
  // При закрытии фокус возвращается на элемент, открывший панель.
  useEffect(() => {
    const active = document.activeElement as HTMLElement | null;
    if (active && !panelRef.current?.contains(active)) returnFocusTo.current = active;
    panelRef.current?.focus();
  }, [day, warehouseId]);

  useEffect(
    () => () => {
      const el = returnFocusTo.current;
      if (el && document.contains(el)) el.focus();
    },
    [],
  );

  const rows: Row[] = useMemo(() => {
    if (!proc) return [];
    if (day) {
      return proc.per_warehouse.map((w) => ({
        key: `w${w.warehouse_id}`,
        label: w.warehouse,
        sub: w.code,
        inbound_t: w.inbound_t,
        outbound_t: w.outbound_t,
        processed_t: w.processed_t,
        docs_count: w.docs_count,
        avg_dwell_hours: w.avg_dwell_hours,
      }));
    }
    return proc.rows.map((r) => ({
      key: `${r.bucket_date}`,
      label: dayLabel(r.bucket_date),
      inbound_t: r.inbound_t,
      outbound_t: r.outbound_t,
      processed_t: r.processed_t,
      docs_count: r.docs_count,
      avg_dwell_hours: r.avg_dwell_hours,
    }));
  }, [proc, day]);

  const cargoGroups = useMemo(() => {
    if (!cargo) return [];
    const acc = new Map<string, { qty: number; weight: number; overdue: number; age: number }>();
    for (const r of cargo.rows) {
      const g = acc.get(r.cargo_group) ?? { qty: 0, weight: 0, overdue: 0, age: 0 };
      g.qty += r.qty;
      g.weight += r.weight_t;
      g.overdue += r.overdue_weight_t;
      g.age += r.avg_age_days * r.weight_t; // средний срок считаем по весу, а не «в среднем от среднего»
      acc.set(r.cargo_group, g);
    }
    return [...acc.entries()]
      .map(([group, g]) => ({
        group,
        qty: g.qty,
        weight: g.weight,
        overdue: g.overdue,
        age: g.weight > 0 ? g.age / g.weight : 0,
      }))
      .sort((a, b) => b.weight - a.weight);
  }, [cargo]);

  // Строка, совпадающая с текущим выбором (ключи строк те же, что у измерений:
  // w<id> / bucket_date) — подсвечиваем её фирменным голубым accent.
  const selectedKey = warehouseId != null ? `w${warehouseId}` : day;

  const totals = proc?.totals;
  const state = busy ? "loading" : error ? "error" : rows.length ? "ready" : "empty";
  const title = day
    ? dayFull(day)
    : (target.kind === "warehouse" ? target.warehouseName : "") || `склад №${warehouseId}`;
  const scopeNote =
    warehouseId != null
      ? "один склад"
      : scopeIds.length
        ? `${scopeIds.length} склада(ов) — ваши доступные`
        : "все склады";

  return (
    <aside
      ref={panelRef}
      tabIndex={-1}
      role="dialog"
      aria-modal="false"
      aria-labelledby="drilldown-title"
      data-drilldown=""
      data-drilldown-mode={target.kind}
      data-drilldown-state={state}
      {...(day ? { "data-drilldown-day": day } : {})}
      {...(warehouseId != null ? { "data-drilldown-warehouse": String(warehouseId) } : {})}
      className={[
        "dd-panel panel fixed z-30 outline-none",
        // мобильный: во всю ширину снизу, шапку не задеваем
        "inset-x-0 bottom-0 max-h-[72vh] overflow-y-auto p-4 rounded-b-none",
        // десктоп: фиксированная колонка справа
        "md:inset-x-auto md:right-6 md:top-[104px] md:bottom-6 md:w-[380px] md:max-h-none md:rounded-b-lg",
      ].join(" ")}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="panel-title">{day ? "Детализация за день" : "Детализация по складу"}</div>
          <h3 id="drilldown-title" className="mt-1 text-sm font-medium truncate">
            {title}
          </h3>
          <div className="mt-1 text-[11px] text-ink-300">
            период <span className="num text-ink-300">{dmy(period.from)} — {dmy(period.to)}</span> · {scopeNote}
          </div>
        </div>
        <button type="button" className="btn-ghost text-xs shrink-0" data-drilldown-close onClick={onClose}>
          Закрыть
        </button>
      </div>

      {busy && (
        <div className="mt-4 space-y-2" aria-live="polite">
          <div className="text-xs text-ink-300">загрузка…</div>
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-4 rounded-sm bg-ink-700 animate-pulse" style={{ width: `${92 - i * 12}%` }} />
          ))}
        </div>
      )}

      {!busy && error && (
        <div className="mt-4 panel border-crit/40 p-3 text-xs text-crit" role="alert">
          {error}{" "}
          <button
            type="button"
            className="underline underline-offset-2 text-ink-100"
            data-drilldown-retry
            onClick={() => setAttempt((n) => n + 1)}
          >
            повторить
          </button>
        </div>
      )}

      {!busy && !error && !rows.length && (
        <div className="mt-4 py-8 text-center text-sm text-ink-300">
          За {day ? dayFull(day) : `${WAREHOUSE_DAYS} суток`} данных нет
        </div>
      )}

      {!busy && !error && rows.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider text-ink-400 bg-ink-800">
                <th className="py-1.5 pr-2 font-medium">{day ? "Склад" : "День"}</th>
                <th className="py-1.5 pr-2 font-medium text-right">Принято</th>
                <th className="py-1.5 pr-2 font-medium text-right">Отгружено</th>
                <th className="py-1.5 pr-2 font-medium text-right">Обработано</th>
                <th className="py-1.5 pr-2 font-medium text-right">Док.</th>
                <th className="py-1.5 font-medium text-right">Хранение</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.key}
                  data-drilldown-row={r.key}
                  data-selected={r.key === selectedKey ? "1" : "0"}
                  className={`border-t border-ink-700 hover:bg-ink-800 ${r.key === selectedKey ? "bg-accent/10" : ""}`}
                >
                  <td className="py-1.5 pr-2 text-ink-100">
                    {r.sub && <span className="num text-ink-300 mr-1.5">{r.sub}</span>}
                    <span className="break-words">{r.label}</span>
                  </td>
                  <td className="py-1.5 pr-2 num text-right text-ink-300">{nf(r.inbound_t, 1)}</td>
                  <td className="py-1.5 pr-2 num text-right text-ink-300">{nf(r.outbound_t, 1)}</td>
                  <td className="py-1.5 pr-2 num text-right font-medium">{nf(r.processed_t, 1)}</td>
                  <td className="py-1.5 pr-2 num text-right text-ink-300">{nf(r.docs_count)}</td>
                  <td className="py-1.5 num text-right text-ink-300">{nf(r.avg_dwell_hours, 1)}</td>
                </tr>
              ))}
            </tbody>
            {totals && (
              <tfoot>
                <tr className="border-t border-ink-500 text-[11px]" data-drilldown-total>
                  <td className="py-1.5 pr-2 font-medium text-ink-100">Итого</td>
                  <td className="py-1.5 pr-2 num text-right text-ink-300">{nf(totals.inbound_t, 1)}</td>
                  <td className="py-1.5 pr-2 num text-right text-ink-300">{nf(totals.outbound_t, 1)}</td>
                  <td className="py-1.5 pr-2 num text-right font-medium text-accent">
                    {nf(totals.processed_t, 1)}
                  </td>
                  <td className="py-1.5 pr-2 num text-right text-ink-300">{nf(totals.docs_count)}</td>
                  <td className="py-1.5 num text-right text-ink-300">{nf(totals.avg_dwell_hours, 1)}</td>
                </tr>
              </tfoot>
            )}
          </table>
          <div className="mt-1.5 text-[10px] text-ink-300">
            вес — тонны, хранение — среднее время на складе в часах
          </div>
        </div>
      )}

      {!busy && !error && warehouseId != null && (
        <div className="mt-4" data-drilldown-cargo>
          <div className="panel-title">Чем загружен склад</div>
          <div className="mt-0.5 text-[10px] text-ink-300">
            остатки на <span className="num text-ink-300">{asOf(cargo?.snapshot_at ?? null)}</span>
          </div>

          {!cargo || !cargoGroups.length ? (
            <div className="py-5 text-center text-xs text-ink-300">Остатков на складе нет</div>
          ) : (
            <table className="mt-1 w-full text-[11px]">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-ink-400 bg-ink-800">
                  <th className="py-1.5 pr-2 font-medium">Группа груза</th>
                  <th className="py-1.5 pr-2 font-medium text-right">Вес, т</th>
                  <th className="py-1.5 pr-2 font-medium text-right">Просрочка</th>
                  <th className="py-1.5 font-medium text-right">Ср. срок, сут</th>
                </tr>
              </thead>
              <tbody>
                {cargoGroups.map((g) => (
                  <tr key={g.group} className="border-t border-ink-700 hover:bg-ink-800">
                    <td className="py-1.5 pr-2 break-words">
                      {g.group}
                      <span className="num text-ink-300 ml-1.5">({nf(g.qty)} мест)</span>
                    </td>
                    <td className="py-1.5 pr-2 num text-right font-medium">{nf(g.weight, 1)}</td>
                    <td
                      className={`py-1.5 pr-2 num text-right ${g.overdue > 0 ? "text-crit" : "text-ink-300"}`}
                    >
                      {g.overdue > 0 ? nf(g.overdue, 1) : "—"}
                    </td>
                    <td className="py-1.5 num text-right text-ink-300">{nf(g.age, 1)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-ink-500" data-drilldown-total>
                  <td className="py-1.5 pr-2 font-medium text-ink-100">Итого</td>
                  <td className="py-1.5 pr-2 num text-right text-accent">{nf(cargo.totals.weight_t, 1)}</td>
                  <td className="py-1.5 pr-2 num text-right text-ink-300">{nf(cargo.totals.overdue_weight_t, 1)}</td>
                  <td className="py-1.5 num text-right text-ink-300">—</td>
                </tr>
              </tfoot>
            </table>
          )}
        </div>
      )}
    </aside>
  );
}
