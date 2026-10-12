import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, type Capacity, type CargoBalance, type DashboardLayout, type Dynamics,
  type Summary, type WidgetState,
} from "../api";
import { useAuth } from "../auth";
import { AppShell } from "../components/AppShell";
import { CargoTable } from "../components/CargoTable";
import { CapacityPanel } from "../components/CapacityPanel";
import { DynamicsChart } from "../components/DynamicsChart";
import { KpiCard } from "../components/KpiCard";
import { WidgetGrid, mergeLayout, type WidgetDef } from "../components/WidgetGrid";
import { asOf, nf, pct, signed } from "../format";

type Data = { summary: Summary; cargo: CargoBalance; capacity: Capacity; dynamics: Dynamics };

const REFRESH_MS = 60_000;
const LAYOUT_KEY = "dashboard_layout";

export default function DashboardPage() {
  const { user } = useAuth();
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState(false);
  const [layout, setLayout] = useState<WidgetState[]>([]);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const saveTimer = useRef<number | undefined>(undefined);

  // ---- раскладка -----------------------------------------------------------
  useEffect(() => {
    api
      .preferences()
      .then((prefs) => {
        const stored = prefs?.[LAYOUT_KEY] as DashboardLayout | undefined;
        setLayout(() => mergeLayout(WIDGETS_PLACEHOLDER, stored?.widgets ?? null));
      })
      .catch(() => setLayout(mergeLayout(WIDGETS_PLACEHOLDER, null)));
  }, []);

  const persist = useCallback((next: WidgetState[]) => {
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      api
        .savePreference(LAYOUT_KEY, { version: 1, widgets: next } satisfies DashboardLayout)
        .then(() => setSavedAt(new Date().toISOString()))
        .catch(() => setError("Раскладку не удалось сохранить — изменения останутся только до перезагрузки"));
    }, 400);
  }, []);

  const changeLayout = useCallback(
    (next: WidgetState[]) => {
      setLayout(next);
      persist(next);
    },
    [persist],
  );

  const resetLayout = useCallback(() => {
    setLayout(mergeLayout(WIDGETS_PLACEHOLDER, null));
    api.resetPreference(LAYOUT_KEY).catch(() => undefined);
  }, []);

  // ---- данные --------------------------------------------------------------
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

  const defs: WidgetDef[] = useMemo(() => buildWidgets(data), [data]);

  return (
    <AppShell
      asOf={asOf(data?.summary.data_as_of ?? null)}
      scopeLabel={user?.all_warehouses ? "все склады" : `${user?.warehouse_ids.length} склада(ов)`}
      actions={
        <>
          {edit && savedAt && (
            <span className="text-[11px] text-ok">раскладка сохранена ✓</span>
          )}
          {edit && (
            <button className="btn-ghost text-xs" onClick={resetLayout}>
              Сбросить
            </button>
          )}
          <button className="btn-ghost text-xs" onClick={load} disabled={busy}>
            {busy ? "Обновление…" : "Обновить"}
          </button>
          <button
            className={`btn text-xs ${edit ? "btn-primary" : "btn-ghost"}`}
            onClick={() => setEdit((v) => !v)}
          >
            {edit ? "Готово" : "Настроить"}
          </button>
        </>
      }
    >
      {edit && (
        <div className="panel p-3 text-xs text-ink-300 flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="text-amber2">Режим настройки</span>
          <span>тяни блок за ⠿, чтобы переставить; ← → — без мыши;</span>
          <span>¼ ½ ⅔ во всю — ширина блока; «скрыть» — убрать, вернуть можно кнопкой внизу.</span>
          <span className="text-ink-500">Раскладка сохраняется на пользователя и подтягивается в любом браузере.</span>
        </div>
      )}

      {error && (
        <div className="panel border-crit/40 p-3 text-sm text-crit">
          {error}{" "}
          <button className="underline underline-offset-2" onClick={load}>
            повторить
          </button>
        </div>
      )}

      <WidgetGrid defs={defs} state={layout} edit={edit} onChange={changeLayout} />

      <p className="text-[11px] text-ink-500 leading-relaxed">
        Источник цифр — 1С, витрина кабинета обновляется автоматически (последняя загрузка:{" "}
        {asOf(data?.summary.data_as_of ?? null)}). Если данные расходятся с 1С, сообщите
        администратору кабинета — проверим выгрузку.
      </p>
    </AppShell>
  );
}

/**
 * Виджеты описаны кодом, а не конфигом: рендер живёт рядом с данными.
 * Порядок и ширина — пользовательские, см. mergeLayout.
 */
function buildWidgets(data: Data | null): WidgetDef[] {
  const s = data?.summary;
  const util = s?.utilization ?? 0;
  const utilTone = util >= 0.95 ? "crit" : util >= 0.85 ? "warn" : "ok";
  const delta = s?.delta_pct ?? null;

  return [
    {
      id: "kpi_cargo",
      title: "Остаток грузов",
      defaultSpan: 3,
      render: () => (
        <KpiCard
          label="Остаток на складах"
          value={nf(s?.cargo_weight_t ?? 0, 0)}
          unit="т"
          sub={
            <>
              просрочено хранение:{" "}
              <span className={s && s.overdue_weight_t > 0 ? "text-crit num" : "num"}>
                {nf(s?.overdue_weight_t ?? 0, 0)} т
              </span>
            </>
          }
        />
      ),
    },
    {
      id: "kpi_load",
      title: "Загрузка складов",
      defaultSpan: 3,
      render: () => (
        <KpiCard
          label="Средняя загрузка"
          value={pct(util, 1)}
          tone={utilTone}
          sub={`${s?.warehouses ?? 0} складов в контуре`}
        />
      ),
    },
    {
      id: "kpi_processed",
      title: "Обработка",
      defaultSpan: 3,
      render: () => (
        <KpiCard
          label="Обработано за 7 суток"
          value={nf(s?.processed_7d_t ?? 0, 0)}
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
      ),
    },
    {
      id: "kpi_dwell",
      title: "Хранение",
      defaultSpan: 3,
      render: () => (
        <KpiCard
          label="Среднее время хранения"
          value={nf(s?.dwell_hours ?? 0, 1)}
          unit="ч"
          sub={`сегодня обработано ${nf(s?.processed_today_t ?? 0, 0)} т`}
        />
      ),
    },
    {
      id: "dynamics",
      title: "Динамика обработки · 14 суток",
      defaultSpan: 8,
      hint: `всего ${nf(data?.dynamics.processed_total ?? 0, 0)} т`,
      render: () => <DynamicsChart points={data?.dynamics.points ?? []} />,
    },
    {
      id: "capacity",
      title: "Ёмкость складов",
      defaultSpan: 4,
      render: () => <CapacityPanel rows={data?.capacity.rows ?? []} />,
    },
    {
      id: "cargo_table",
      title: "Остатки грузов по складам",
      defaultSpan: 12,
      hint: `срез ${asOf(data?.cargo.snapshot_at ?? null)}`,
      render: () => <CargoTable rows={data?.cargo.rows ?? []} />,
    },
  ];
}

// Описание виджетов для начального слияния раскладки (до появления данных).
const WIDGETS_PLACEHOLDER = buildWidgets(null);
