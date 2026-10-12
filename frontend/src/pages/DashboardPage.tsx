import { useCallback, useEffect, useState } from "react";
import {
  api, type Capacity, type CargoBalance, type Dynamics, type Summary,
} from "../api";
import { useAuth } from "../auth";
import { CargoTable } from "../components/CargoTable";
import { CapacityPanel } from "../components/CapacityPanel";
import { DynamicsChart } from "../components/DynamicsChart";
import { KpiCard } from "../components/KpiCard";
import { asOf, nf, pct, roleLabel, signed } from "../format";

type Data = {
  summary: Summary;
  cargo: CargoBalance;
  capacity: Capacity;
  dynamics: Dynamics;
};

const REFRESH_MS = 60_000;

export default function DashboardPage() {
  const { user, logout } = useAuth();
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [summary, cargo, capacity, dynamics] = await Promise.all([
        api.summary(),
        api.cargo(),
        api.capacity(),
        api.dynamics(14),
      ]);
      setData({ summary, cargo, capacity, dynamics });
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить данные");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  const util = data?.summary.utilization ?? 0;
  const utilTone = util >= 0.95 ? "crit" : util >= 0.85 ? "warn" : "ok";
  const delta = data?.summary.delta_pct ?? null;

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-10 bg-ink-800/95 backdrop-blur border-b border-ink-600">
        <div className="mx-auto max-w-[1400px] px-6 py-3 flex items-center gap-4">
          <div>
            <div className="text-[11px] uppercase tracking-[0.18em] text-ink-400">БазисПорт</div>
            <div className="text-sm font-medium">Кабинет веб-статистики</div>
          </div>

          <div className="ml-4 hidden md:flex items-center gap-2 text-xs text-ink-400">
            <span className="chip bg-ink-700 text-ink-300">
              данные 1С на <span className="num ml-1 text-ink-100">{asOf(data?.summary.data_as_of ?? null)}</span>
            </span>
            <span className="chip bg-ink-700 text-ink-300">
              {user?.all_warehouses ? "все склады" : `${user?.warehouse_ids.length} склада(ов)`}
            </span>
          </div>

          <div className="ml-auto flex items-center gap-3">
            <button className="btn-ghost text-xs" onClick={load} disabled={busy}>
              {busy ? "Обновление…" : "Обновить"}
            </button>
            <div className="text-right leading-tight">
              <div className="text-sm">{user?.full_name}</div>
              <div className="text-[11px] text-ink-400">{roleLabel[user?.role ?? ""]}</div>
            </div>
            <button className="btn-ghost text-xs" onClick={logout}>
              Выйти
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] px-6 py-6 space-y-6">
        {error && (
          <div className="panel border-crit/40 p-3 text-sm text-crit">
            {error}{" "}
            <button className="underline underline-offset-2" onClick={load}>
              повторить
            </button>
          </div>
        )}

        <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <KpiCard
            label="Остаток грузов"
            value={nf(data?.summary.cargo_weight_t ?? 0, 0)}
            unit="т"
            sub={
              <>
                просрочено хранение:{" "}
                <span className={data && data.summary.overdue_weight_t > 0 ? "text-crit num" : "num"}>
                  {nf(data?.summary.overdue_weight_t ?? 0, 0)} т
                </span>
              </>
            }
          />
          <KpiCard
            label="Загрузка складов"
            value={pct(util, 1)}
            tone={utilTone}
            sub={`${data?.summary.warehouses ?? 0} складов в контуре`}
          />
          <KpiCard
            label="Обработано за 7 суток"
            value={nf(data?.summary.processed_7d_t ?? 0, 0)}
            unit="т"
            sub={
              delta === null ? (
                "нет данных за предыдущий период"
              ) : (
                <span className={delta >= 0 ? "text-ok" : "text-crit"}>
                  {signed(delta)} к предыдущим 14 суткам
                </span>
              )
            }
          />
          <KpiCard
            label="Среднее время хранения"
            value={nf(data?.summary.dwell_hours ?? 0, 1)}
            unit="ч"
            sub={`сегодня обработано ${nf(data?.summary.processed_today_t ?? 0, 0)} т`}
          />
        </section>

        <section className="grid gap-4 xl:grid-cols-[1.55fr_1fr]">
          <div className="panel p-4">
            <div className="flex items-baseline justify-between">
              <h2 className="panel-title">Динамика обработки · 14 суток</h2>
              <span className="text-[11px] text-ink-400 num">
                всего {nf(data?.dynamics.processed_total ?? 0, 0)} т
              </span>
            </div>
            <div className="mt-3">
              <DynamicsChart points={data?.dynamics.points ?? []} />
            </div>
          </div>

          <div className="panel p-4">
            <h2 className="panel-title">Ёмкость складов</h2>
            <div className="mt-3">
              <CapacityPanel rows={data?.capacity.rows ?? []} />
            </div>
          </div>
        </section>

        <section className="panel p-4">
          <div className="flex items-baseline justify-between">
            <h2 className="panel-title">Остатки грузов по складам</h2>
            <span className="text-[11px] text-ink-400">
              срез <span className="num text-ink-300">{asOf(data?.cargo.snapshot_at ?? null)}</span>
            </span>
          </div>
          <div className="mt-3">
            <CargoTable rows={data?.cargo.rows ?? []} />
          </div>
        </section>

        <p className="text-[11px] text-ink-500 leading-relaxed">
          Источник цифр — 1С, витрина кабинета обновляется автоматически. Если данные
          расходятся с 1С, сообщите администратору кабинета: проверим выгрузку.
        </p>
      </main>
    </div>
  );
}
