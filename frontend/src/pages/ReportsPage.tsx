import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api, type CargoReport, type CapacityTrendReport, type ProcessingReport, type ReportWarehouse,
} from "../api";
import { useAuth } from "../auth";
import { AppShell } from "../components/AppShell";
import { asOf, dayLabel, nf, pct } from "../format";

type TabId = "daily" | "per_warehouse" | "cargo" | "capacity";

const TABS: { id: TabId; label: string; csv?: { path: string; name: string } }[] = [
  { id: "daily", label: "Обработка по дням", csv: { path: "/api/reports/processing.csv", name: "obrabotka" } },
  { id: "per_warehouse", label: "Итоги по складам", csv: { path: "/api/reports/processing.csv", name: "obrabotka" } },
  { id: "cargo", label: "Остатки грузов", csv: { path: "/api/reports/cargo.csv", name: "ostatki" } },
  { id: "capacity", label: "Загрузка складов", csv: { path: "/api/reports/capacity-trend.csv", name: "zagruzka" } },
];

const PRESETS = [
  { days: 7, label: "7 дней" },
  { days: 14, label: "14 дней" },
  { days: 30, label: "30 дней" },
  { days: 90, label: "90 дней" },
];

const iso = (d: Date) => d.toISOString().slice(0, 10);
const isoDaysAgo = (n: number) => iso(new Date(Date.now() - (n - 1) * 86400_000));

type Filters = { days: number; from: string; to: string; warehouses: number[] };

const DEFAULTS: Filters = { days: 30, from: isoDaysAgo(30), to: iso(new Date()), warehouses: [] };

type SortKey = "bucket_date" | "warehouse" | "processed_t" | "inbound_t" | "outbound_t" | "docs_count";

export default function ReportsPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<TabId>("daily");
  const [filters, setFilters] = useState<Filters>(DEFAULTS);
  const [warehouses, setWarehouses] = useState<ReportWarehouse[]>([]);
  const [daily, setDaily] = useState<ProcessingReport | null>(null);
  const [cargo, setCargo] = useState<CargoReport | null>(null);
  const [capacity, setCapacity] = useState<CapacityTrendReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "bucket_date", dir: -1 });
  const [restored, setRestored] = useState(false);

  // сохранённые фильтры пользователя
  useEffect(() => {
    api.reportWarehouses().then(setWarehouses).catch(() => setWarehouses([]));
    api
      .preferences()
      .then((prefs) => {
        const saved = prefs?.report_filters as Filters | undefined;
        if (saved?.from && saved?.to) setFilters({ ...DEFAULTS, ...saved });
      })
      .catch(() => undefined)
      .finally(() => setRestored(true));
  }, []);

  const params = useMemo(
    () => ({ from: filters.from, to: filters.to, warehouses: filters.warehouses }),
    [filters],
  );

  const load = useCallback(async () => {
    setBusy(true);
    try {
      if (tab === "daily" || tab === "per_warehouse") setDaily(await api.reportProcessing(params));
      else if (tab === "cargo") setCargo(await api.reportCargo(params));
      else setCapacity(await api.reportCapacity(params));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось получить отчёт");
    } finally {
      setBusy(false);
    }
  }, [tab, params]);

  useEffect(() => {
    load();
  }, [load]);

  // Сохраняем фильтры только после того, как отработала загрузка сохранённых:
  // иначе первый же рендер перезаписывает настройку дефолтом (гонка mount-vs-restore).
  useEffect(() => {
    if (!restored) return;
    api.savePreference("report_filters", filters).catch(() => undefined);
  }, [filters, restored]);

  const setPreset = (days: number) => setFilters((f) => ({ ...f, days, from: isoDaysAgo(days), to: iso(new Date()) }));

  const toggleWarehouse = (id: number) =>
    setFilters((f) => ({
      ...f,
      warehouses: f.warehouses.includes(id) ? f.warehouses.filter((x) => x !== id) : [...f.warehouses, id],
    }));

  const download = async () => {
    const meta = TABS.find((t) => t.id === tab)?.csv;
    if (!meta) return;
    try {
      await api.downloadCsv(meta.path, params, `${meta.name}_${filters.from}_${filters.to}.csv`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Выгрузка не удалась");
    }
  };

  const rows = useMemo(() => {
    const list = daily?.rows ?? [];
    const cmp = (x: string | number, y: string | number) =>
      typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "ru");
    return [...list].sort((a, b) => cmp(a[sort.key], b[sort.key]) * sort.dir);
  }, [daily, sort]);

  const scopeLabel =
    filters.warehouses.length === 0
      ? user?.all_warehouses
        ? "все склады"
        : `${user?.warehouse_ids.length} склада(ов)`
      : `${filters.warehouses.length} выбрано`;

  return (
    <AppShell
      // чип свежести показываем только когда реально знаем срез: у отчётов по дням
      // snapshot_at не бывает, иначе в шапке читается «данные 1С на нет данных»
      asOf={cargo?.snapshot_at ? asOf(cargo.snapshot_at) : undefined}
      scopeLabel={scopeLabel}
      actions={
        <button className="btn-primary text-xs" onClick={download} disabled={busy}>
          Выгрузить CSV
        </button>
      }
    >
      {/* -------- фильтры -------- */}
      <section className="panel p-4">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-4">
          <div>
            <div className="panel-title mb-2">Период</div>
            <div className="flex flex-wrap items-center gap-1.5">
              {PRESETS.map((p) => (
                <button
                  key={p.days}
                  onClick={() => setPreset(p.days)}
                  className={`chip ${filters.days === p.days ? "bg-amber2/20 text-amber2" : "bg-ink-800 border border-ink-700 text-ink-300 hover:text-ink-100"}`}
                >
                  {p.label}
                </button>
              ))}
              <input
                type="date"
                className="field-mini"
                value={filters.from}
                onChange={(e) => setFilters((f) => ({ ...f, from: e.target.value, days: 0 }))}
              />
              <span className="text-ink-300 text-xs">—</span>
              <input
                type="date"
                className="field-mini"
                value={filters.to}
                onChange={(e) => setFilters((f) => ({ ...f, to: e.target.value, days: 0 }))}
              />
            </div>
          </div>

          <div className="flex-1 min-w-[280px]">
            <div className="panel-title mb-2">
              Склады{" "}
              {filters.warehouses.length > 0 && (
                <button className="text-amber2 hover:underline ml-2" onClick={() => setFilters((f) => ({ ...f, warehouses: [] }))}>
                  сбросить выбор
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {warehouses.map((w) => (
                <button
                  key={w.id}
                  onClick={() => toggleWarehouse(w.id)}
                  className={`chip ${filters.warehouses.includes(w.id) ? "bg-amber2/20 text-amber2" : "bg-ink-800 border border-ink-700 text-ink-300 hover:text-ink-100"}`}
                >
                  <span className="num">{w.code}</span> {w.name}
                </button>
              ))}
              {warehouses.length === 0 && <span className="text-xs text-ink-300">нет доступных складов</span>}
            </div>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-1 border-t border-ink-700 pt-3">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`rounded-md px-3 py-1.5 text-sm transition ${
                tab === t.id ? "bg-ink-700 text-ink-100" : "text-ink-300 hover:text-ink-100"
              }`}
            >
              {t.label}
            </button>
          ))}
          {busy && <span className="ml-auto text-xs text-ink-300">загрузка…</span>}
        </div>
      </section>

      {error && (
        <div className="panel border-crit/40 p-3 text-sm text-crit">
          {error}{" "}
          <button className="underline underline-offset-2" onClick={load}>
            повторить
          </button>
        </div>
      )}

      {/* -------- таблицы -------- */}
      {(tab === "daily" || tab === "per_warehouse") && (
        <section className="panel p-4">
          <div className="flex items-baseline justify-between">
            <h2 className="panel-title">
              {tab === "daily" ? "Обработка по дням" : "Итоги по складам"} ·{" "}
              <span className="num">
                {dayLabel(filters.from)} — {dayLabel(filters.to)}
              </span>
            </h2>
            <span className="text-[11px] text-ink-300 num">
              строк: {tab === "daily" ? (daily?.rows.length ?? 0) : (daily?.per_warehouse.length ?? 0)}
            </span>
          </div>

          {tab === "daily" ? (
            <table className="w-full text-sm mt-3">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-ink-400 bg-ink-800">
                  {([
                    ["bucket_date", "Дата"],
                    ["warehouse", "Склад"],
                    ["inbound_t", "Принято, т"],
                    ["outbound_t", "Отгружено, т"],
                    ["processed_t", "Обработано, т"],
                    ["docs_count", "Документов"],
                  ] as [SortKey, string][]).map(([key, label]) => (
                    <th key={key} className={`py-2 pr-3 font-medium ${key === "warehouse" ? "" : "text-right"}`}>
                      <button
                        className="hover:text-ink-100"
                        onClick={() => setSort((s) => ({ key, dir: s.key === key ? ((s.dir * -1) as 1 | -1) : -1 }))}
                      >
                        {label}
                        {sort.key === key ? (sort.dir === -1 ? " ↓" : " ↑") : ""}
                      </button>
                    </th>
                  ))}
                  <th className="py-2 font-medium text-right">Ср. хранение, ч</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={`${r.bucket_date}-${r.warehouse_id}-${i}`} className="border-t border-ink-700 hover:bg-ink-800">
                    <td className="py-2 pr-3 num">{dayLabel(r.bucket_date)}</td>
                    <td className="py-2 pr-3 text-ink-300">
                      <span className="num text-ink-300 mr-2">{r.code}</span>
                      {r.warehouse}
                    </td>
                    <td className="py-2 pr-3 num text-right">{nf(r.inbound_t, 1)}</td>
                    <td className="py-2 pr-3 num text-right">{nf(r.outbound_t, 1)}</td>
                    <td className="py-2 pr-3 num text-right font-medium">{nf(r.processed_t, 1)}</td>
                    <td className="py-2 pr-3 num text-right text-ink-300">{nf(r.docs_count)}</td>
                    <td className="py-2 num text-right text-ink-300">{nf(r.avg_dwell_hours, 1)}</td>
                  </tr>
                ))}
                {!rows.length && (
                  <tr>
                    <td colSpan={7} className="py-6 text-center text-ink-300">
                      За этот период данных нет
                    </td>
                  </tr>
                )}
              </tbody>
              {daily?.totals && (
                <tfoot>
                  <tr className="border-t-2 border-ink-600 font-medium">
                    <td className="py-2 pr-3" colSpan={2}>
                      Итого за {daily.totals.days} дн.
                    </td>
                    <td className="py-2 pr-3 num text-right">{nf(daily.totals.inbound_t, 1)}</td>
                    <td className="py-2 pr-3 num text-right">{nf(daily.totals.outbound_t, 1)}</td>
                    <td className="py-2 pr-3 num text-right">{nf(daily.totals.processed_t, 1)}</td>
                    <td className="py-2 pr-3 num text-right">{nf(daily.totals.docs_count)}</td>
                    <td className="py-2 num text-right">{nf(daily.totals.avg_dwell_hours, 1)}</td>
                  </tr>
                </tfoot>
              )}
            </table>
          ) : (
            <table className="w-full text-sm mt-3">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-ink-400 bg-ink-800">
                  <th className="py-2 pr-3 font-medium">Склад</th>
                  <th className="py-2 pr-3 font-medium text-right">Обработано, т</th>
                  <th className="py-2 pr-3 font-medium text-right">Принято, т</th>
                  <th className="py-2 pr-3 font-medium text-right">Отгружено, т</th>
                  <th className="py-2 pr-3 font-medium text-right">Документов</th>
                  <th className="py-2 font-medium text-right">Ср. хранение, ч</th>
                </tr>
              </thead>
              <tbody>
                {(daily?.per_warehouse ?? []).map((r) => (
                  <tr key={r.warehouse_id} className="border-t border-ink-700 hover:bg-ink-800">
                    <td className="py-2 pr-3">
                      <span className="num text-ink-300 mr-2">{r.code}</span>
                      {r.warehouse}
                    </td>
                    <td className="py-2 pr-3 num text-right font-medium">{nf(r.processed_t, 1)}</td>
                    <td className="py-2 pr-3 num text-right text-ink-300">{nf(r.inbound_t, 1)}</td>
                    <td className="py-2 pr-3 num text-right text-ink-300">{nf(r.outbound_t, 1)}</td>
                    <td className="py-2 pr-3 num text-right text-ink-300">{nf(r.docs_count)}</td>
                    <td className="py-2 num text-right text-ink-300">{nf(r.avg_dwell_hours, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {tab === "cargo" && (
        <section className="panel p-4">
          <div className="flex items-baseline justify-between">
            <h2 className="panel-title">Остатки грузов на срез</h2>
            <span className="text-[11px] text-ink-300 num">
              {cargo?.totals.positions ?? 0} позиций · {nf(cargo?.totals.weight_t ?? 0, 1)} т · просрочено{" "}
              {nf(cargo?.totals.overdue_weight_t ?? 0, 1)} т
            </span>
          </div>
          <table className="w-full text-sm mt-3">
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
              {(cargo?.rows ?? []).map((r, i) => (
                <tr key={`${r.warehouse_id}-${r.cargo_group}-${i}`} className="border-t border-ink-700 hover:bg-ink-800">
                  <td className="py-2 pr-3 text-ink-300">
                    <span className="num text-ink-300 mr-2">{r.code}</span>
                    {r.warehouse}
                  </td>
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
        </section>
      )}

      {tab === "capacity" && (
        <section className="panel p-4">
          <h2 className="panel-title">Загрузка складов по срезам</h2>
          <table className="w-full text-sm mt-3">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-ink-400 bg-ink-800">
                <th className="py-2 pr-3 font-medium">Срез</th>
                <th className="py-2 pr-3 font-medium">Склад</th>
                <th className="py-2 pr-3 font-medium text-right">Занято</th>
                <th className="py-2 pr-3 font-medium text-right">Ёмкость</th>
                <th className="py-2 font-medium text-right">Загрузка</th>
              </tr>
            </thead>
            <tbody>
              {(capacity?.rows ?? []).slice(0, 300).map((r, i) => (
                <tr key={`${r.snapshot_at}-${r.warehouse_id}-${i}`} className="border-t border-ink-700 hover:bg-ink-800">
                  <td className="py-2 pr-3 num text-ink-300">{asOf(r.snapshot_at)}</td>
                  <td className="py-2 pr-3">
                    <span className="num text-ink-300 mr-2">{r.code}</span>
                    {r.warehouse}
                  </td>
                  <td className="py-2 pr-3 num text-right">{nf(r.used, 1)}</td>
                  <td className="py-2 pr-3 num text-right text-ink-300">
                    {nf(r.capacity, 1)} {r.unit}
                  </td>
                  <td className={`py-2 num text-right font-medium ${
                    r.utilization >= 0.95 ? "text-crit" : r.utilization >= 0.85 ? "text-warn" : "text-ok"
                  }`}>
                    {pct(r.utilization, 1)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <p className="text-[11px] text-ink-300 leading-relaxed">
        CSV открывается в Excel: разделитель «;», десятичный «,», кириллица с BOM. Период
        ограничен годом — исторические срезы глубже 12 месяцев выгружаются по запросу.
        Фильтры запоминаются: в следующий раз кабинет откроет тот же период и склады.
      </p>
    </AppShell>
  );
}
