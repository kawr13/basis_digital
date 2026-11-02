import { useEffect, useRef, useState } from "react";
import { api, type DispatcherRowsResult, type DispatcherRowKind } from "../api";
import { dmy, nf } from "../format";

/**
 * Таблица-детализация графика: те строки витрины, из которых посчитан бар.
 *
 * Открывается кликом по любому графику страницы «Индикаторы». Колонки описывает
 * бэкенд (`/api/dispatcher/rows`), поэтому таблица на экране и цифры на графике
 * физически из одного источника. Экран показывает страницу (`limit` строк), а
 * CSV пишет тот же бэкенд (`/api/dispatcher/rows.csv`) и без лимита: выгрузка
 * не обязана зависеть от того, сколько диспетчер успел доскроллить.
 */
export function DispatcherRowsPanel({
  kind, rowKey, from, to, shift, onClose,
}: {
  kind: DispatcherRowKind;
  rowKey?: string | null;
  from: string;
  to: string;
  shift: string;
  onClose: () => void;
}) {
  const [data, setData] = useState<DispatcherRowsResult | null>(null);
  const [error, setError] = useState("");
  const [csvBusy, setCsvBusy] = useState(false);
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError("");
    api
      .dispatcherRows({ from, to, shift, kind, key: rowKey ?? undefined })
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Не удалось загрузить строки"));
    return () => {
      alive = false;
    };
  }, [kind, rowKey, from, to, shift]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  /** CSV: файл собирает бэкенд, поэтому в нём весь период, а не экран. */
  const exportCsv = async () => {
    if (!data || csvBusy) return;
    setCsvBusy(true);
    try {
      await api.dispatcherRowsCsv({ from, to, shift, kind, key: rowKey ?? undefined });
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось выгрузить CSV");
    } finally {
      setCsvBusy(false);
    }
  };

  const cell = (v: string | number | null) => {
    if (v === null || v === undefined || v === "") return <span className="text-ink-300">—</span>;
    if (typeof v === "number") return <>{nf(v, Number.isInteger(v) ? 0 : 1)}</>;
    if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v)) return <>{dmy(v)}</>;
    return <>{v}</>;
  };

  return (
    <aside
      ref={ref}
      tabIndex={-1}
      data-drilldown="dispatcher-rows"
      data-drilldown-kind={kind}
      className="dd-panel panel fixed z-30 outline-none inset-x-0 bottom-0 max-h-[76vh] overflow-y-auto rounded-b-none p-4
        md:inset-x-auto md:right-6 md:top-[calc(var(--header-h,89px)+8px)] md:bottom-6 md:w-[min(880px,calc(100vw_-_3rem))] md:max-h-none md:rounded-b-lg
        md:shadow-[0_30px_70px_-25px_rgba(15,27,45,.55)]"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="panel-title">{data?.title ?? "Строки витрины"}</div>
          <div className="mt-0.5 text-[11px] text-ink-300 num">
            {dmy(from)} — {dmy(to)}
            {shift && shift !== "all" ? ` · смена ${shift === "08-20" ? "08–20" : "20–08"}` : " · все смены"}
            {data
              ? data.truncated
                ? ` · показаны первые ${nf(data.rows.length, 0)} из ${nf(data.total, 0)}`
                : ` · строк: ${nf(data.total, 0)}`
              : ""}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button className="btn-ghost text-xs" onClick={exportCsv}
                  disabled={!data?.rows.length || csvBusy} data-rows-csv>
            {csvBusy ? "Выгружаем…" : "CSV"}
          </button>
          <button className="btn-ghost text-xs" onClick={onClose} data-rows-close>
            Закрыть
          </button>
        </div>
      </div>

      {error && <div className="mt-3 rounded-md border border-crit/40 bg-crit/10 p-2 text-xs text-crit">{error}</div>}
      {!error && !data && <div className="mt-3 text-xs text-ink-300">Загружаем строки…</div>}

      {data && !data.rows.length && (
        <div className="mt-3 text-xs text-ink-300">За период по этому разрезу строк нет.</div>
      )}

      {data && data.rows.length > 0 && (
        <div className="mt-3 -mx-1 overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="border-b border-ink-600 text-[10px] uppercase tracking-wider text-ink-300">
                {data.columns.map((c) => (
                  <th key={c.key} className={`py-1.5 pr-2 ${c.right ? "text-right" : "text-left"}`}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r, i) => (
                <tr key={i} className="border-b border-ink-700/60">
                  {data.columns.map((c) => (
                    <td key={c.key} className={`py-1.5 pr-2 num ${c.right ? "text-right" : "text-left"}`}>
                      {cell(r[c.key] ?? null)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-ink-500 font-semibold">
                {data.columns.map((c, i) => (
                  <td key={c.key} className={`py-1.5 pr-2 num ${c.right ? "text-right" : ""}`}>
                    {i === 0 ? "итого" : c.right && data.totals[c.key] !== null && data.totals[c.key] !== undefined
                      ? nf(data.totals[c.key] as number, 0)
                      : ""}
                  </td>
                ))}
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      <p className="mt-3 text-[11px] leading-relaxed text-ink-300">
        Это строки витрины «Отчёт диспетчера», из которых посчитан график. Период и фильтр смены
        те же, что на странице.
        {data?.truncated
          ? " На экране — первые строки, в файле CSV с сервера — весь период."
          : ""}
      </p>
    </aside>
  );
}
