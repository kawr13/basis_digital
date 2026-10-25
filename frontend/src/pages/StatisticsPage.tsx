import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type DispatcherMeta,
  type DispatcherOverview,
  type DispatcherRowKind,
  type DashboardLayout,
  type WidgetState,
} from "../api";
import { AppShell } from "../components/AppShell";
import { DispatcherDayPanel } from "../components/DispatcherDayPanel";
import { DispatcherRowsPanel } from "../components/DispatcherRowsPanel";
import {
  BarsChart, BulletChart, DonutChart, GaugeChart, HBarsChart, HeatmapChart, RadarChart,
  ScatterChart, TableLite, Td, Tr, type BarDatum, type BarSeries, type HBarItem, type Slice,
} from "../components/charts";
import { KpiCard } from "../components/KpiCard";
import { mergeLayout, WidgetGrid, type WidgetDef } from "../components/WidgetGrid";
import { asOf, dayLabel, dmy, nf } from "../format";
import { CHART } from "../components/chartKit";

/**
 * «Статистика» — страница по суточному отчёту диспетчера порта.
 *
 * Источник цифр — витрина `fact_dispatcher_*`: сегодня её наполняет файл
 * `Отчет диспетчера.xlsm` (импорт в админке), завтра — тот же по контракту
 * срез отдаст 1С по HTTP. Все виджеты считаются из ОДНОГО ответа
 * `/api/dispatcher/overview`, поэтому цифры в карточке, на графике и в панели
 * деталей расходятся не могут по построению.
 *
 * Период по умолчанию считает бэкенд от ПОСЛЕДНЕЙ даты данных, а не от «сегодня»:
 * отчёт диспетчера — документ исторический, и открывать пустой экран «на сегодня»
 * бессмысленно.
 */

/**
 * Два режима страницы. «Подробно» — все блоки и разные виды графиков.
 * «Консервативно» — один визуальный язык (столбцы, линии, таблицы) и только то,
 * без чего статистика не читается. Раскладка и режим — персональные настройки,
 * причём раскладка своя для каждого режима: наборы блоков разные.
 */
const LAYOUT_KEYS: Record<Mode, string> = {
  detailed: "statistics_layout",
  conservative: "statistics_layout_lite",
};
const MODE_KEY = "statistics_mode";

/** Что скрывается в консервативном режиме: кольца, радар, пузырьки, тепловая карта. */
const LITE_HIDE = new Set([
  "cargo_mix", "cumulative", "pareto", "vessels_scatter", "shift_compare",
  "weekday_heat", "direction", "gauges",
]);
/** Чем заменяем скрытое: те же данные, но столбцами. */
const LITE_ONLY = new Set(["cargo_bars", "shift_bars", "weekday_bars", "direction_bars"]);
const REFRESH_MS = 300_000;
const SHIFTS = [
  { v: "all", label: "Все смены" },
  { v: "08-20", label: "День 08–20" },
  { v: "20-08", label: "Ночь 20–08" },
];

type Mode = "detailed" | "conservative";

type Period = { from: string; to: string };

const iso = (d: Date) => d.toISOString().slice(0, 10);
const shiftDays = (isoDate: string, days: number) => {
  const d = new Date(`${isoDate}T00:00:00`);
  d.setDate(d.getDate() + days);
  return iso(d);
};
/**
 * Границы периода: лимит длины задаёт бэкенд (`dispatcher.max_period_days`,
 * приходит в `meta.max_period_days`) — годовой запрос агрегация не тянет и
 * отвечает на него 400. Панель строит `min`/`max` полей и пресеты по этому
 * числу, а не по догадкам. ISO-строки `YYYY-MM-DD` сравнимы лексикографически.
 */
const minIso = (a: string, b: string) => (a <= b ? a : b);
const maxIso = (a: string, b: string) => (a >= b ? a : b);
const isIso = (v: string | undefined | null): v is string => !!v && /^\d{4}-\d{2}-\d{2}$/.test(v);
const pc = (v: number | null | undefined, digits = 0) => (v === null || v === undefined ? "—" : `${nf(v, digits)}%`);
const toneOf = (v: number | null | undefined) =>
  v === null || v === undefined ? "default" : v >= 100 ? "ok" : v >= 80 ? "default" : v >= 60 ? "warn" : "crit";

export default function StatisticsPage() {
  const [meta, setMeta] = useState<DispatcherMeta | null>(null);
  const [data, setData] = useState<DispatcherOverview | null>(null);
  const [period, setPeriod] = useState<Period | null>(null);
  const [shift, setShift] = useState("all");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [edit, setEdit] = useState(false);
  const [layout, setLayout] = useState<WidgetState[]>([]);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("detailed");
  // клик по графику → таблица строк витрины, из которых он посчитан
  const [rowsQuery, setRowsQuery] = useState<{ kind: DispatcherRowKind; key: string | null } | null>(null);
  const saveTimer = useRef<number | undefined>(undefined);

  // ---- раскладка (та же механика, что на дашборде: порядок и размеры — на человеке)
  useEffect(() => {
    api
      .preferences()
      .then((prefs) => {
        if (prefs?.[MODE_KEY] === "conservative" || prefs?.[MODE_KEY] === "detailed") {
          setMode(prefs[MODE_KEY] as Mode);
        }
        const key = LAYOUT_KEYS[(prefs?.[MODE_KEY] as Mode) ?? "detailed"];
        const stored = prefs?.[key] as DashboardLayout | undefined;
        setLayout(() =>
          mergeLayout(buildWidgets(null, null, () => undefined, () => undefined),
                      stored?.widgets ?? null),
        );
      })
      .catch(() => setLayout(mergeLayout(buildWidgets(null, null, () => undefined, () => undefined), null)));
  }, []);

  const persist = useCallback((next: WidgetState[]) => {
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      api
        .savePreference(LAYOUT_KEYS[mode], { version: 1, widgets: next } satisfies DashboardLayout)
        .catch(() => setError("Раскладку не удалось сохранить — изменения останутся до перезагрузки"));
    }, 400);
  }, [mode]);

  const changeLayout = useCallback(
    (next: WidgetState[]) => {
      setLayout(next);
      persist(next);
    },
    [persist],
  );

  // ---- данные --------------------------------------------------------------
  const load = useCallback(
    async (quiet = false) => {
      if (!quiet) setBusy(true);
      try {
        const ov = await api.dispatcherOverview({ from: period?.from, to: period?.to, shift });
        setData(ov);
        if (!period) setPeriod({ from: ov.period.from, to: ov.period.to });
        setError("");
      } catch (e) {
        setError(e instanceof Error ? e.message : "Не удалось загрузить статистику");
      } finally {
        if (!quiet) setBusy(false);
      }
    },
    [period, shift],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    api.dispatcherMeta().then(setMeta).catch(() => undefined);
  }, []);

  useEffect(() => {
    const t = window.setInterval(() => {
      if (!document.hidden) void load(true);
    }, REFRESH_MS);
    return () => window.clearInterval(t);
  }, [load]);

  // лимит длины периода — тот же, что проверяет бэкенд; 0 = мету ещё не получили
  const maxDays = meta?.max_period_days ?? 0;

  const pickPreset = useCallback(
    (days: number) => {
      const anchor = meta?.date_max ?? data?.period.to;
      if (!anchor) return;
      setSelectedDay(null);
      // пресет длиннее лимита кнопка не вызывает, но и тут не разъезжается
      const span = maxDays > 0 ? Math.min(days, maxDays) : days;
      setPeriod({ from: shiftDays(anchor, -(span - 1)), to: anchor });
    },
    [meta, data, maxDays],
  );

  /** «с»: значение не дальше лимита назад от выбранной «по». */
  const pickFrom = useCallback(
    (value: string) => {
      setPeriod((p) => {
        const other = p?.to ?? "";
        // нативный `min` бережёт календарь, но не ввод с клавиатуры
        const next = maxDays > 0 && isIso(value) && isIso(other)
          ? maxIso(value, shiftDays(other, -(maxDays - 1)))
          : value;
        return { from: next, to: other || value };
      });
    },
    [maxDays],
  );

  /** «по»: значение не дальше лимита вперёд от выбранной «с». */
  const pickTo = useCallback(
    (value: string) => {
      setPeriod((p) => {
        const other = p?.from ?? "";
        const next = maxDays > 0 && isIso(value) && isIso(other)
          ? minIso(value, shiftDays(other, maxDays - 1))
          : value;
        return { from: other || value, to: next };
      });
    },
    [maxDays],
  );

  const selectDay = useCallback((day: string) => {
    // панель смены спрашивает дату у бэка; подпись вида «17 нояб.» открыла бы
    // 422 вместо панели, поэтому принимаем только ISO-дату
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return;
    setSelectedDay((cur) => (cur === day ? null : day));
  }, []);

  const openRows = useCallback((kind: DispatcherRowKind, key: string | null = null) => {
    setSelectedDay(null);
    setRowsQuery((cur) => (cur && cur.kind === kind && cur.key === key ? null : { kind, key }));
  }, []);

  const chooseMode = useCallback((next: Mode) => {
    setMode(next);
    api.savePreference(MODE_KEY, next).catch(() => undefined);
  }, []);

  const defs = useMemo<WidgetDef[]>(
    () =>
      buildWidgets(data, selectedDay, selectDay, openRows).filter((w) =>
        mode === "detailed" ? !LITE_ONLY.has(w.id) : !LITE_HIDE.has(w.id),
      ),
    [data, selectedDay, selectDay, mode, openRows],
  );

  const presets = [7, 14, 30, 90];

  // границы полей: с одной стороны — витрина (`meta.date_min`/`date_max`), с
  // другой — лимит длины периода, отсчитанный от второй даты выбранного периода
  const dateFrom = period?.from ?? "";
  const dateTo = period?.to ?? "";
  const minFrom = maxDays > 0 && isIso(dateTo)
    ? maxIso(shiftDays(dateTo, -(maxDays - 1)), meta?.date_min ?? "")
    : meta?.date_min ?? "";
  const maxTo = maxDays > 0 && isIso(dateFrom)
    ? minIso(shiftDays(dateFrom, maxDays - 1), meta?.date_max ?? "")
    : meta?.date_max ?? "";

  return (
    <AppShell
      wide
      asOf={asOf(data?.meta.updated_at ?? null)}
      scopeLabel={data ? `${nf(data.kpi.shifts, 0)} смен · ${nf(data.kpi.days, 0)} суток` : "отчёт диспетчера"}
      actions={
        <>
          {edit && (
            <button
              className="btn-ghost text-xs"
              onClick={() => {
                setLayout(mergeLayout(buildWidgets(data, null, selectDay, () => undefined), null));
                api.resetPreference(LAYOUT_KEYS[mode]).catch(() => undefined);
              }}
            >
              Сбросить
            </button>
          )}
          <div className="inline-flex rounded-md border border-ink-600 p-0.5" data-stats-mode>
            {([["detailed", "Подробно"], ["conservative", "Консервативно"]] as [Mode, string][]).map(([m, label]) => (
              <button
                key={m}
                className={`rounded px-2.5 py-1 text-xs transition ${
                  mode === m ? "bg-ink-700 text-ink-100" : "text-ink-300 hover:text-ink-100"
                }`}
                onClick={() => chooseMode(m)}
              >
                {label}
              </button>
            ))}
          </div>
          <button className="btn-ghost text-xs" onClick={() => load()} disabled={busy}>
            {busy ? "Обновление…" : "Обновить"}
          </button>
          <button className={`btn text-xs ${edit ? "btn-primary" : "btn-ghost"}`} onClick={() => setEdit((v) => !v)}>
            {edit ? "Готово" : "Настроить"}
          </button>
        </>
      }
    >
      {/* ---------------- панель периода ---------------- */}
      <div className="panel p-3" data-stats-toolbar>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex flex-wrap items-center gap-1">
            {presets.map((d) => {
              const anchor = meta?.date_max ?? data?.period.to;
              const active =
                !!period && !!anchor && period.to === anchor && period.from === shiftDays(anchor, -(d - 1));
              // пресет длиннее лимита бэкенд не примет — он неактивен, а не молча врёт
              const tooLong = maxDays > 0 && d > maxDays;
              return (
                <button
                  key={d}
                  className={`btn btn-mini ${active ? "btn-primary" : "btn-ghost"}${
                    tooLong ? " opacity-40 cursor-not-allowed" : ""
                  }`}
                  disabled={tooLong}
                  title={tooLong ? `Максимум ${maxDays} сут — лимит меняет администратор` : undefined}
                  onClick={() => pickPreset(d)}
                >
                  {d} сут
                </button>
              );
            })}
          </div>

          <label className="flex items-center gap-1.5 text-xs text-ink-300">
            с
            <input
              type="date"
              className="field-mini"
              value={period?.from ?? ""}
              min={minFrom || undefined}
              max={period?.to ?? undefined}
              onChange={(e) => pickFrom(e.target.value)}
            />
            по
            <input
              type="date"
              className="field-mini"
              value={period?.to ?? ""}
              min={period?.from ?? undefined}
              max={maxTo || undefined}
              onChange={(e) => pickTo(e.target.value)}
            />
          </label>

          <div className="flex flex-wrap items-center gap-1">
            {SHIFTS.map((s) => (
              <button
                key={s.v}
                className={`btn btn-mini ${shift === s.v ? "btn-primary" : "btn-ghost"}`}
                onClick={() => {
                  setShift(s.v);
                  setSelectedDay(null);
                }}
              >
                {s.label}
              </button>
            ))}
          </div>

          {data && (
            <span className="ml-auto text-[11px] text-ink-300">
              данные за {dmy(data.period.from)} — {dmy(data.period.to)}
              {meta?.date_min && meta.date_max ? ` · в витрине с ${dmy(meta.date_min)} по ${dmy(meta.date_max)}` : ""}
              {data.meta.updated_at ? ` · выгружено ${asOf(data.meta.updated_at)}` : ""}
            </span>
          )}
        </div>
      </div>

      {edit && (
        <div className="panel p-3 text-xs text-ink-300 flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="text-amber2">Режим настройки</span>
          <span>тяни блок за ⠿, чтобы переставить; правый край — ширина, нижний — высота;</span>
          <span>«скрыть» — убрать график со страницы, вернуть — кнопкой внизу;</span>
          <span>раскладка сохраняется на пользователя и подтягивается в любом браузере.</span>
        </div>
      )}

      {error && (
        <div className="panel border-crit/40 p-3 text-sm text-crit">
          {error}{" "}
          <button className="underline underline-offset-2" onClick={() => load()}>
            повторить
          </button>
        </div>
      )}

      {!busy && !data && !error && <div className="panel p-6 text-center text-sm text-ink-300">Нет данных</div>}

      {data && !data.dynamics.length && (
        <div className="panel p-4 text-sm text-ink-300">
          За выбранный период смен нет. Измените период или импортируйте отчёт диспетчера
          в администрировании (вкладка «1С и загрузка»).
        </div>
      )}

      <WidgetGrid defs={defs} state={layout} edit={edit} onChange={changeLayout} />

      {selectedDay && (
        <DispatcherDayPanel day={selectedDay} shift={shift} onClose={() => setSelectedDay(null)} />
      )}
      {rowsQuery && period && (
        <DispatcherRowsPanel
          kind={rowsQuery.kind}
          rowKey={rowsQuery.key}
          from={period.from}
          to={period.to}
          shift={shift}
          onClose={() => setRowsQuery(null)}
        />
      )}

      <p className="text-[11px] text-ink-400 leading-relaxed">
        Источник — суточный отчёт диспетчера порта (витрина кабинета, последняя загрузка:{" "}
        {asOf(data?.meta.updated_at ?? null)}). Тоннаж — факт за смену по строкам обработки судов;
        «план» — план на смену из того же отчёта. Если цифры расходятся с отчётом диспетчера,
        сообщите администратору — проверим загрузку файла.
      </p>
    </AppShell>
  );
}

/* =============================== виджеты ================================== */

function buildWidgets(
  data: DispatcherOverview | null,
  selectedDay: string | null,
  onSelectDay: (day: string) => void,
  openRows: (kind: DispatcherRowKind, key?: string | null) => void,
): WidgetDef[] {
  const k = data?.kpi;
  const dyn = data?.dynamics ?? [];

  const truckDays = data?.trucks.by_day ?? [];

  const throughput = dyn.map<BarDatum>((p) => ({
    key: p.bucket_date,
    label: dayLabel(p.bucket_date),
    values: { throughput: p.throughput_t, plan: p.plan_shift_t, calls: p.calls },
    tip: [
      { label: "судозаходов", value: `${nf(p.calls, 0)}` },
      { label: "потери времени", value: `${nf(p.downtime_h, 1)} ч` },
      { label: "вагонов", value: nf(p.rail_cars, 0) },
      { label: "автомашин", value: nf(p.trucks, 0) },
    ],
  }));

  const cargoMix: Slice[] = (data?.cargo_mix ?? []).map((c) => ({ label: c.cargo_group, value: c.tons }));

  // Парето: тоннаж по грузам + накопленная доля
  const paretoSource = (data?.cargo_mix ?? []).filter((c) => c.tons > 0).slice(0, 12);
  const paretoTotal = paretoSource.reduce((a, c) => a + c.tons, 0) || 1;
  let acc = 0;
  const pareto = paretoSource.map<BarDatum>((c) => {
    acc += c.tons;
    return {
      key: c.cargo_group,
      label: c.cargo_group.length > 9 ? `${c.cargo_group.slice(0, 8)}…` : c.cargo_group,
      values: { tons: c.tons, cum: (acc / paretoTotal) * 100 },
    };
  });

  const cumulative = dyn.map<BarDatum>((p, i) => {
    const sum = dyn.slice(0, i + 1).reduce((a, x) => a + x.throughput_t, 0);
    return { key: p.bucket_date, label: dayLabel(p.bucket_date), values: { sum } };
  });

  const personnel = dyn.map<BarDatum>((p) => ({
    key: p.bucket_date,
    label: dayLabel(p.bucket_date),
    values: { crew: p.crew, day_off: p.day_off, contractors: p.contractors, rate: p.crew_rate },
    tip: [
      { label: "вышло всего", value: nf(p.present, 1) },
      { label: "штат", value: nf(p.staff, 0) },
    ],
  }));

  const absence = dyn.map<BarDatum>((p) => ({
    key: p.bucket_date,
    label: dayLabel(p.bucket_date),
    values: { sick: p.sick, vacation: p.vacation, other: p.other_absent },
    tip: [{ label: "всего выбыло", value: nf(p.sick + p.vacation + p.other_absent, 1) }],
  }));

  const equipment = dyn.map<BarDatum>((p) => ({
    key: p.bucket_date,
    label: dayLabel(p.bucket_date),
    values: { ok: p.rs_ok_end, repair: p.rs_repair_end, availability: p.rs_availability },
    tip: [
      { label: "на начало смены", value: `${nf(p.rs_ok_start, 0)} испр. / ${nf(p.rs_repair_start, 0)} рем.` },
      { label: "парк", value: nf(p.rs_total, 0) },
    ],
  }));

  const railDay = dyn.map<BarDatum>((p) => ({
    key: p.bucket_date,
    label: dayLabel(p.bucket_date),
    values: { rail: p.rail_cars, trucks: p.trucks },
  }));

  const truckDay = truckDays.map<BarDatum>((t) => ({
    key: t.bucket_date,
    label: dayLabel(t.bucket_date),
    values: {
      imp_unload: t.imp_unload, exp_unload: t.exp_unload, imp_load: t.imp_load,
      exp_load: t.exp_load, direct: t.direct_vessel,
    },
    tip: [{ label: "всего машин", value: nf(t.total, 0) }],
  }));

  const planFact = [...dyn].reverse().slice(0, 14).reverse().map((p) => ({
    key: p.bucket_date,
    label: dayLabel(p.bucket_date),
    fact: p.throughput_t,
    plan: p.plan_shift_t,
    sub: p.calls ? `${nf(p.calls, 0)} судозаходов · потери ${nf(p.downtime_h, 1)} ч` : undefined,
  }));

  const berthItems: HBarItem[] = (data?.berths ?? []).map((b) => ({
    label: b.berth,
    value: b.throughput_t,
    sub: `${nf(b.calls, 0)} сущ. · потери ${pc(b.downtime_pct, 0)}`,
  }));

  const vesselItems: HBarItem[] = (data?.vessels_top ?? []).slice(0, 14).map((v) => ({
    label: v.vessel,
    value: v.tons,
    sub: `${nf(v.calls, 0)} смен · ${nf(v.tph, 0)} т/ч`,
  }));

  const railCargoItems: HBarItem[] = (data?.rail.by_cargo ?? [])
    .filter((r) => r.processed > 0)
    .map((r) => ({ label: r.cargo_group, value: r.processed, sub: `остаток ${nf(r.end_loaded + r.end_empty, 0)}` }));

  const truckCargoItems: HBarItem[] = (data?.trucks.by_cargo ?? [])
    .filter((t) => t.total > 0)
    .map((t) => ({ label: t.cargo_group, value: t.total, sub: `и ${nf(t.imp_unload + t.imp_load, 0)} / э ${nf(t.exp_unload + t.exp_load, 0)}` }));

  const stationItems: HBarItem[] = (data?.rail_arrivals ?? []).map((s) => ({
    label: s.station,
    value: s.cars,
    sub: `${nf(s.trains, 0)} поездов · ${s.cargos.slice(0, 2).join(", ")}`,
  }));

  // Пузырьковая: машинное время × темп, размер = тоннаж
  const scatter = (data?.vessels_top ?? [])
    .filter((v) => v.machine_h > 0 && v.tons > 0)
    .slice(0, 24)
    .map((v) => ({
      x: v.machine_h,
      y: v.tph,
      size: v.tons,
      label: v.vessel,
      tip: `${v.vessel}: ${nf(v.machine_h, 1)} маш. ч, ${nf(v.tph, 0)} т/ч, ${nf(v.tons, 0)} т, потери ${nf(v.downtime_h, 1)} ч`,
    }));

  // Радар «день против ночи»: каждая ось нормирована на лучшее из двух смен
  const split = data?.shift_split ?? [];
  const radarAxes = [
    { key: "tons", label: "тоннаж" },
    { key: "calls", label: "судозаходы" },
    { key: "tph", label: "темп т/ч" },
    { key: "plan", label: "план %" },
    { key: "calm", label: "без потерь" },
  ];
  const best = {
    tons: Math.max(1, ...split.map((s) => s.throughput_t)),
    calls: Math.max(1, ...split.map((s) => s.calls)),
    tph: Math.max(1, ...split.map((s) => s.avg_tph)),
    plan: Math.max(1, ...split.map((s) => s.plan_pct ?? 0)),
    calm: Math.max(1, ...split.map((s) => 100 - (s.downtime_pct ?? 0))),
  };
  const radarSeries = split.map((s, i) => ({
    name: s.label,
    color: i === 0 ? CHART.accent : CHART.amber,
    values: {
      tons: s.throughput_t / best.tons,
      calls: s.calls / best.calls,
      tph: s.avg_tph / best.tph,
      plan: (s.plan_pct ?? 0) / best.plan,
      calm: (100 - (s.downtime_pct ?? 0)) / best.calm,
    },
  }));

  // Теплокарта «день недели × неделя периода»: недели нумеруем от первой даты
  // периода, а не от месяца — период произвольный (7…366 суток).
  const heatWeeks = weekColumns(data?.period.from ?? null, data?.period.to ?? null);
  const heatValue = (row: string, col: string) => {
    const p = dyn.find(
      (x) => weekdayLabel(x.bucket_date) === row && weekOf(x.bucket_date, data?.period.from ?? null, heatWeeks) === col,
    );
    return p ? p.throughput_t : null;
  };

  const truckOps: BarSeries[] = [
    { key: "imp_unload", label: "принял / выгрузка, импорт" },
    { key: "exp_unload", label: "растарка / выгрузка, экспорт" },
    { key: "imp_load", label: "затарка / погрузка, импорт" },
    { key: "exp_load", label: "затарка / погрузка, экспорт" },
    { key: "direct", label: "с судна в машину" },
  ];

  // ---- выводка / постановка вагонов и платформы из аренды -------------------
  const moves = data?.rail_moves;
  const moveDay: BarDatum[] = (moves?.by_day ?? []).map((d) => ({
    key: d.bucket_date,
    label: dayLabel(d.bucket_date),
    values: { out: d.out_cars, in: d.in_cars },
    tip: [
      { label: "поставлено гружёных", value: `${nf(d.in_loaded, 0)} ваг.` },
      { label: "поставлено порожних", value: `${nf(d.in_empty, 0)} ваг.` },
      {
        label: "задержка вывода",
        value: d.avg_delay_min === null ? "нет данных" : `${nf(d.avg_delay_min, 0)} мин`,
      },
    ],
  }));
  const wagonMix: BarDatum[] = (moves?.by_wagon ?? []).map((w) => ({
    key: w.wagon_type,
    label: w.wagon_type,
    values: { out: w.out, in: w.in },
  }));
  // остаток платформ бывает нулевым (весь парк отправлен) — полосы молчат,
  // поэтому блок сделан таблицей: в ней видно и нуль, и сдвиг за период
  const platRows = data?.platforms ?? [];

  // ---- консервативные замены колец/радара/тепловой карты -------------------
  const cargoBars: HBarItem[] = (data?.cargo_mix ?? []).map((c) => ({
    label: c.cargo_group,
    value: c.tons,
    sub: `${nf(c.calls, 0)} судозаходов · ${nf(c.tph, 0)} т/ч`,
  }));
  const shiftBars: BarDatum[] = [
    { key: "tons", label: "тоннаж, т", values: Object.fromEntries(split.map((sp) => [sp.shift, sp.throughput_t])) },
    { key: "calls", label: "судозаходы", values: Object.fromEntries(split.map((sp) => [sp.shift, sp.calls])) },
    { key: "tph", label: "темп, т/ч", values: Object.fromEntries(split.map((sp) => [sp.shift, sp.avg_tph])) },
    { key: "downtime", label: "потери, %", values: Object.fromEntries(split.map((sp) => [sp.shift, sp.downtime_pct])) },
  ];
  const shiftBarSeries: BarSeries[] = split.map((sp, i) => ({
    key: sp.shift,
    label: sp.label,
    color: i === 0 ? CHART.navy : CHART.sky,
  }));
  const weekdayBars: BarDatum[] = (data?.weekday ?? []).map((w) => ({
    key: String(w.weekday),
    label: w.label,
    values: { tons: w.avg_throughput_t },
    tip: [{ label: "суток в периоде", value: nf(w.days, 0) }],
  }));
  const directionBars: HBarItem[] = [
    { label: "экспорт (погрузка)", value: data?.direction.export_t ?? 0, color: CHART.navy },
    { label: "импорт (выгрузка)", value: data?.direction.import_t ?? 0, color: CHART.sky },
  ];

  const widgets: WidgetDef[] = [
    {
      id: "kpi_throughput",
      title: "Грузопоток",
      defaultSpan: 3,
      render: () => (
        <KpiCard
          label="Грузопоток за период"
          value={nf(k?.throughput_t ?? 0, 0)}
          unit="т"
          spark={dyn.map((p) => p.throughput_t)}
          sub={
            k?.delta_pct === null || k?.delta_pct === undefined ? (
              `${nf(k?.days ?? 0, 0)} суток · ${nf(k?.shifts ?? 0, 0)} смен`
            ) : (
              <span className={k.delta_pct >= 0 ? "text-ok" : "text-crit"}>
                {k.delta_pct >= 0 ? "+" : ""}
                {nf(k.delta_pct, 1)}% к предыдущему периоду
              </span>
            )
          }
        />
      ),
    },
    {
      id: "kpi_plan",
      title: "Выполнение плана",
      defaultSpan: 3,
      render: () => (
        <KpiCard
          label="Факт к плану смены"
          value={pc(k?.plan_pct, 1)}
          tone={toneOf(k?.plan_pct) as "default" | "ok" | "warn" | "crit"}
          sub={`план ${nf(k?.plan_shift_t ?? 0, 0)} т`}
        />
      ),
    },
    {
      id: "kpi_calls",
      title: "Судозаходы",
      defaultSpan: 3,
      render: () => (
        <KpiCard
          label="Строк обработки судов"
          value={nf(k?.calls ?? 0, 0)}
          sub={`${nf(k?.vessels_unique ?? 0, 0)} уникальных судов`}
          spark={dyn.map((p) => p.calls)}
        />
      ),
    },
    {
      id: "kpi_tph",
      title: "Производительность",
      defaultSpan: 3,
      render: () => (
        <KpiCard
          label="Средний темп обработки"
          value={nf(k?.avg_tph ?? 0, 1)}
          unit="т/ч"
          sub={`${nf(k?.machine_h ?? 0, 0)} машинных часов`}
        />
      ),
    },
    {
      id: "kpi_downtime",
      title: "Потери времени",
      defaultSpan: 3,
      render: () => (
        <KpiCard
          label="Доля потерь в сменном времени"
          value={pc(k?.downtime_pct, 1)}
          tone={(k?.downtime_pct ?? 0) > 35 ? "crit" : (k?.downtime_pct ?? 0) > 25 ? "warn" : "ok"}
          sub={`${nf(k?.downtime_h ?? 0, 0)} ч простоев`}
        />
      ),
    },
    {
      id: "kpi_rail",
      title: "Вагоны",
      defaultSpan: 3,
      render: () => (
        <KpiCard
          label="Вагонов обработано"
          value={nf(k?.rail_cars ?? 0, 0)}
          spark={dyn.map((p) => p.rail_cars)}
          sub={`в порту на конец: ${nf((data?.rail.totals.end_loaded ?? 0) + (data?.rail.totals.end_empty ?? 0), 0)}`}
        />
      ),
    },
    {
      id: "kpi_trucks",
      title: "Автомашины",
      defaultSpan: 3,
      render: () => (
        <KpiCard
          label="Автомашин обработано"
          value={nf(k?.trucks ?? 0, 0)}
          spark={truckDays.map((t) => t.total)}
          sub={`импорт ${nf((data?.trucks.totals.imp_unload ?? 0) + (data?.trucks.totals.imp_load ?? 0), 0)} · экспорт ${nf(
            (data?.trucks.totals.exp_unload ?? 0) + (data?.trucks.totals.exp_load ?? 0),
            0,
          )}`}
        />
      ),
    },
    {
      id: "kpi_crew",
      title: "Явка персонала",
      defaultSpan: 3,
      render: () => (
        <KpiCard
          label="Явка (средняя смена)"
          value={pc(k?.crew_rate, 0)}
          tone={toneOf(k?.crew_rate) as "default" | "ok" | "warn" | "crit"}
          sub={`${nf(k?.crew_present ?? 0, 1)} из ${nf(k?.crew_staff ?? 0, 0)} по штату`}
        />
      ),
    },
    {
      id: "kpi_rs",
      title: "Ричстакеры",
      defaultSpan: 3,
      render: () => (
        <KpiCard
          label="Исправность ричстакеров"
          value={pc(k?.rs_availability, 0)}
          tone={((k?.rs_availability ?? 0) >= 85 ? "ok" : (k?.rs_availability ?? 0) >= 70 ? "warn" : "crit") as "ok" | "warn" | "crit"}
          sub="среднее за смену: начало + конец"
        />
      ),
    },
    {
      id: "dynamics",
      title: "Грузопоток по суткам",
      defaultSpan: 8,
      defaultHeight: 280,
      hint: k ? `${nf(k.throughput_t, 0)} т · план ${pc(k.plan_pct, 0)}` : undefined,
      render: ({ height }) => (
        <BarsChart
          data={throughput}
          height={height ?? 280}
          unit=" т"
          series={[
            { key: "throughput", label: "факт, т" },
            { key: "plan", label: "план смены, т", color: CHART.plan, line: true },
            { key: "calls", label: "судозаходов", color: CHART.sky, line: true, right: true },
          ]}
          onSelectKey={onSelectDay}
          selectedKey={selectedDay}
          ariaLabel="Грузопоток по суткам"
        />
      ),
    },
    {
      id: "cargo_mix",
      title: "Структура грузопотока",
      defaultSpan: 4,
      defaultHeight: 280,
      render: ({ height }) => (
        <DonutChart items={cargoMix} height={height ?? 280} onSelect={(label) => openRows("cargo", label)} />
      ),
    },
    {
      id: "cumulative",
      title: "Нарастающий итог",
      defaultSpan: 6,
      defaultHeight: 220,
      hint: k ? `${nf(k.throughput_t, 0)} т` : undefined,
      render: ({ height }) => (
        <BarsChart
          data={cumulative}
          height={height ?? 220}
          unit=" т"
          series={[{ key: "sum", label: "нарастающим итогом, т", line: true, color: CHART.navy }]}
          ariaLabel="Нарастающий итог грузопотока"
        />
      ),
    },
    {
      id: "pareto",
      title: "Парето грузов",
      defaultSpan: 6,
      defaultHeight: 220,
      hint: "80 % тоннажа дают не все грузы",
      render: ({ height }) => (
        <BarsChart
          data={pareto}
          height={height ?? 220}
          unit=" т"
          series={[
            { key: "tons", label: "тоннаж, т" },
            { key: "cum", label: "накопленная доля, %", color: CHART.amber, line: true, right: true },
          ]}
          ariaLabel="Парето грузов"
        />
      ),
    },
    {
      id: "plan_fact",
      title: "План и факт по суткам",
      defaultSpan: 5,
      defaultHeight: 280,
      hint: "последние 14 суток периода",
      render: ({ height }) => (
        <BulletChart items={planFact} height={height ?? 280} onSelectKey={onSelectDay} selectedKey={selectedDay} />
      ),
    },
    {
      id: "berths",
      title: "Причалы",
      defaultSpan: 4,
      defaultHeight: 280,
      render: ({ height }) => (
        <HBarsChart
          items={berthItems}
          height={height ?? 280}
          unit="т"
          color={CHART.navy}
          onSelectKey={(label) => openRows("berth", label)}
        />
      ),
    },
    {
      id: "vessels_top",
      title: "Топ судов по тоннажу",
      defaultSpan: 6,
      defaultHeight: 300,
      render: ({ height }) => (
        <HBarsChart items={vesselItems} height={height ?? 300} unit="т" onSelectKey={(label) => openRows("vessel", label)} />
      ),
    },
    {
      id: "vessels_scatter",
      title: "Темп против машинного времени",
      defaultSpan: 6,
      defaultHeight: 300,
      hint: "размер круга — тоннаж",
      render: ({ height }) => (
        <ScatterChart
          points={scatter}
          height={height ?? 300}
          xLabel="машинное время, ч"
          yLabel="темп, т/ч"
          xUnit=" ч"
          yUnit=" т/ч"
        />
      ),
    },
    {
      id: "shift_compare",
      title: "День против ночи",
      defaultSpan: 5,
      defaultHeight: 300,
      hint: split.length ? "фильтр «все смены» показывает оба ряда" : undefined,
      render: ({ height }) => <RadarChart axes={radarAxes} series={radarSeries} height={height ?? 300} />,
    },
    {
      id: "weekday_heat",
      title: "Тепловая карта: день недели × неделя",
      defaultSpan: 4,
      defaultHeight: 240,
      render: ({ height }) => (
        <HeatmapChart
          rows={["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]}
          cols={heatWeeks}
          value={heatValue}
          format={(v) => nf(v, 0)}
          height={height ?? 240}
        />
      ),
    },
    {
      id: "personnel",
      title: "Выход персонала",
      defaultSpan: 7,
      defaultHeight: 260,
      hint: "средняя смена, человек",
      render: ({ height }) => (
        <BarsChart
          data={personnel}
          height={height ?? 260}
          stack
          unit=" чел."
          series={[
            { key: "crew", label: "основная бригада" },
            { key: "day_off", label: "вышедшие в выходной" },
            { key: "contractors", label: "подрядчики" },
            { key: "rate", label: "явка, %", color: CHART.amber, line: true, right: true, digits: 1 },
          ]}
          onSelectKey={onSelectDay}
          selectedKey={selectedDay}
        />
      ),
    },
    {
      id: "absence",
      title: "Выбытия",
      defaultSpan: 5,
      defaultHeight: 260,
      hint: "больничные, отпуска, прочее",
      render: ({ height }) => (
        <BarsChart
          data={absence}
          height={height ?? 260}
          stack
          unit=" чел."
          series={[
            { key: "sick", label: "больничный", color: CHART.crit },
            { key: "vacation", label: "отпуск", color: CHART.amber },
            { key: "other", label: "прогул / МОБ / учеба / донор", color: CHART.plan },
          ]}
        />
      ),
    },
    {
      id: "equipment",
      title: "Ричстакеры",
      defaultSpan: 5,
      defaultHeight: 240,
      render: ({ height }) => (
        <BarsChart
          data={equipment}
          height={height ?? 240}
          stack
          unit=" шт."
          series={[
            { key: "ok", label: "исправно", color: CHART.ok },
            { key: "repair", label: "в ремонте", color: CHART.warn },
            { key: "availability", label: "готовность, %", color: CHART.accent, line: true, right: true, digits: 1 },
          ]}
        />
      ),
    },
    {
      id: "rail_day",
      title: "Вагоны и автомашины по суткам",
      defaultSpan: 7,
      defaultHeight: 240,
      render: ({ height }) => (
        <BarsChart
          data={railDay}
          height={height ?? 240}
          unit=" шт."
          series={[
            { key: "rail", label: "вагонов обработано" },
            { key: "trucks", label: "автомашин", color: CHART.sky, line: true },
          ]}
          onSelectKey={onSelectDay}
          selectedKey={selectedDay}
        />
      ),
    },
    {
      id: "rail_cargo",
      title: "Вагоны по номенклатуре",
      defaultSpan: 6,
      defaultHeight: 260,
      render: ({ height }) => (
        <HBarsChart
          items={railCargoItems}
          height={height ?? 260}
          unit="ваг."
          color={CHART.accent}
          onSelectKey={(label) => openRows("rail_cargo", label)}
        />
      ),
    },
    {
      id: "trucks_day",
      title: "Автомашины по направлениям работ",
      defaultSpan: 7,
      defaultHeight: 260,
      render: ({ height }) => (
        <BarsChart
          data={truckDay}
          height={height ?? 260}
          stack
          unit=" машин"
          series={truckOps}
          onSelectKey={onSelectDay}
          selectedKey={selectedDay}
        />
      ),
    },
    {
      id: "trucks_cargo",
      title: "Топ грузов автообработки",
      defaultSpan: 5,
      defaultHeight: 260,
      render: ({ height }) => (
        <HBarsChart
          items={truckCargoItems}
          height={height ?? 260}
          unit="маш."
          color={CHART.sky}
          onSelectKey={(label) => openRows("truck_cargo", label)}
        />
      ),
    },
    {
      id: "direction",
      title: "Импорт и экспорт",
      defaultSpan: 4,
      defaultHeight: 240,
      hint: "факт смены разложен по плану судна",
      render: ({ height }) => (
        <div className="flex h-full flex-col justify-center gap-3">
          <DonutChart
            items={[
              { label: "экспорт (погрузка)", value: data?.direction.export_t ?? 0, color: CHART.navy },
              { label: "импорт (выгрузка)", value: data?.direction.import_t ?? 0, color: CHART.sky },
            ]}
            height={(height ?? 240) - 8}
            centerLabel="всего"
          />
        </div>
      ),
    },
    {
      id: "gauges",
      title: "Показатели смены",
      defaultSpan: 4,
      defaultHeight: 240,
      render: () => (
        <div className="grid grid-cols-2 gap-2">
          <GaugeChart value={k?.plan_pct ?? null} max={120} label="выполнение плана, %" thresholds={[80, 100]} />
          <GaugeChart value={k?.crew_rate ?? null} max={110} label="явка, %" thresholds={[75, 90]} />
          <GaugeChart
            value={k?.downtime_pct ?? null}
            max={60}
            label="потери, %"
            thresholds={[25, 35]}
            invert
            sub={`${nf(k?.downtime_h ?? 0, 0)} ч за период`}
          />
          <GaugeChart value={k?.rs_availability ?? null} max={100} label="готовность техники, %" thresholds={[70, 85]} />
        </div>
      ),
    },
    {
      id: "arrivals",
      title: "Суда на подходе",
      defaultSpan: 6,
      defaultHeight: 300,
      hint: data ? `${data.arrivals.length} плановых заходов` : undefined,
      render: ({ height }) => (
        <div style={{ maxHeight: height ?? 300 }} className="overflow-auto">
          <TableLite
            head={[
              { label: "заход" },
              { label: "судно" },
              { label: "причал" },
              { label: "груз" },
              { label: "план, т", align: "right" },
              { label: "агент" },
            ]}
          >
            {(data?.arrivals ?? []).slice(0, 40).map((a, i) => (
              <Tr key={`${a.vessel}-${a.eta}-${i}`}>
                <Td className="text-ink-300" title={a.eta ?? ""}>
                  {a.eta ? new Date(a.eta).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"}
                </Td>
                <Td className="text-ink-100">{a.vessel}</Td>
                <Td>{a.berth || "—"}</Td>
                <Td className="text-ink-300">{a.cargo_group}</Td>
                <Td right>{a.plan_raw || nf(a.plan_t, 0)}</Td>
                <Td className="text-ink-300">{a.agent}</Td>
              </Tr>
            ))}
            {!data?.arrivals.length && (
              <Tr>
                <Td className="text-ink-300" colSpan={6}>Плановых заходов в периоде нет</Td>
              </Tr>
            )}
          </TableLite>
        </div>
      ),
    },
    {
      id: "rail_arrivals",
      title: "Ж/Д на подходе",
      defaultSpan: 6,
      defaultHeight: 260,
      hint: "вагонов по станциям дислокации",
      render: ({ height }) => (
        <HBarsChart
          items={stationItems}
          height={height ?? 260}
          unit="ваг."
          color={CHART.accent}
          onSelectKey={(label) => openRows("station", label)}
        />
      ),
    },
    {
      id: "rail_moves",
      title: "Выводка и постановка вагонов",
      defaultSpan: 7,
      defaultHeight: 260,
      hint: moves
        ? `выведено ${nf(moves.totals.out_cars, 0)} · поставлено ${nf(moves.totals.in_cars, 0)} ваг.` +
          (moves.totals.avg_delay_min !== null
            ? ` · вывод в среднем +${nf(moves.totals.avg_delay_min, 0)} мин к плану`
            : "")
        : undefined,
      render: ({ height }) => (
        <BarsChart
          data={moveDay}
          height={height ?? 260}
          unit=" ваг."
          series={[
            { key: "in", label: "поставлено в порт" },
            { key: "out", label: "выведено из порта", color: CHART.amber },
          ]}
          onSelectKey={onSelectDay}
          selectedKey={selectedDay}
          ariaLabel="Выводка и постановка вагонов по суткам"
        />
      ),
    },
    {
      id: "rail_wagon_mix",
      title: "Вагоны по типам: вывод и ввод",
      defaultSpan: 5,
      defaultHeight: 260,
      hint: "клик — список составов",
      render: ({ height }) => (
        <BarsChart
          data={wagonMix}
          height={height ?? 260}
          unit=" ваг."
          series={[
            { key: "in", label: "поставлено" },
            { key: "out", label: "выведено", color: CHART.amber },
          ]}
          onSelectKey={() => openRows("rail_move")}
          ariaLabel="Вагоны по типам: вывод и ввод"
        />
      ),
    },
    {
      id: "platforms",
      title: "Платформы, выведенные из аренды",
      defaultSpan: 5,
      defaultHeight: 220,
      hint: "остаток на конец периода",
      render: ({ height }) => (
        <div style={{ maxHeight: height ?? 220 }} className="overflow-auto">
          <TableLite
            head={[
              { label: "владелец" },
              { label: "всего", align: "right" },
              { label: "остаток", align: "right" },
              { label: "на терминале", align: "right" },
              { label: "отправлено", align: "right" },
              { label: "сдвиг за период", align: "right" },
            ]}
          >
            {platRows.map((pl) => (
              <Tr key={pl.owner}>
                <Td
                  className="text-ink-100 cursor-pointer underline decoration-dotted underline-offset-2"
                  title="показать строки витрины"
                  onClick={() => openRows("platform", pl.owner)}
                >
                  {pl.owner}
                </Td>
                <Td right>{nf(pl.total_cars, 0)}</Td>
                <Td right>{nf(pl.remaining, 0)}</Td>
                <Td right>{nf(pl.terminal, 0)}</Td>
                <Td right>{nf(pl.sent, 0)}</Td>
                <Td right className={pl.remaining_delta < 0 ? "text-crit" : pl.remaining_delta > 0 ? "text-ok" : "text-ink-300"}>
                  {pl.remaining_delta > 0 ? "+" : ""}
                  {nf(pl.remaining_delta, 0)}
                </Td>
              </Tr>
            ))}
            {!platRows.length && (
              <Tr>
                <Td className="text-ink-300" colSpan={6}>В отчёте нет блока про платформы</Td>
              </Tr>
            )}
          </TableLite>
        </div>
      ),
    },
    {
      id: "cargo_bars",
      title: "Грузы по тоннажу",
      defaultSpan: 5,
      defaultHeight: 280,
      hint: "клик — строки обработки по грузу",
      render: ({ height }) => (
        <HBarsChart
          items={cargoBars}
          height={height ?? 280}
          unit="т"
          onSelectKey={(label) => openRows("cargo", label)}
        />
      ),
    },
    {
      id: "shift_bars",
      title: "День против ночи",
      defaultSpan: 5,
      defaultHeight: 280,
      render: ({ height }) => (
        <BarsChart
          data={shiftBars}
          height={height ?? 280}
          series={shiftBarSeries}
          ariaLabel="Сравнение дневной и ночной смен"
        />
      ),
    },
    {
      id: "weekday_bars",
      title: "Средний грузопоток по дням недели",
      defaultSpan: 4,
      defaultHeight: 240,
      render: ({ height }) => (
        <BarsChart
          data={weekdayBars}
          height={height ?? 240}
          unit=" т"
          series={[{ key: "tons", label: "средний грузопоток, т" }]}
          ariaLabel="Средний грузопоток по дням недели"
        />
      ),
    },
    {
      id: "direction_bars",
      title: "Импорт и экспорт",
      defaultSpan: 4,
      defaultHeight: 200,
      hint: "факт смены разложен по плану судна",
      render: ({ height }) => (
        <HBarsChart items={directionBars} height={height ?? 200} unit="т" />
      ),
    },
    {
      id: "vessels_table",
      title: "Обработка судов за период",
      defaultSpan: 12,
      defaultHeight: 340,
      hint: data ? `${data.vessels_top.length} судов` : undefined,
      render: ({ height }) => (
        <div style={{ maxHeight: height ?? 340 }} className="overflow-auto">
          <TableLite
            head={[
              { label: "судно" },
              { label: "грузы" },
              { label: "смен обработки", align: "right" },
              { label: "тоннаж, т", align: "right" },
              { label: "маш. время, ч", align: "right" },
              { label: "потери, ч", align: "right" },
              { label: "темп, т/ч", align: "right" },
            ]}
          >
            {(data?.vessels_top ?? []).map((v) => (
              <Tr key={v.vessel} tone={v.tph >= 200 ? "ok" : v.tph > 0 && v.tph < 30 ? "warn" : undefined}>
                <Td className="text-ink-100">{v.vessel}</Td>
                <Td className="text-ink-300">{v.cargos.join(", ")}</Td>
                <Td right>{nf(v.calls, 0)}</Td>
                <Td right>{nf(v.tons, 0)}</Td>
                <Td right>{nf(v.machine_h, 1)}</Td>
                <Td right>{nf(v.downtime_h, 1)}</Td>
                <Td right>{nf(v.tph, 1)}</Td>
              </Tr>
            ))}
            {!data?.vessels_top.length && (
              <Tr>
                <Td className="text-ink-300">Судов в периоде не было</Td>
              </Tr>
            )}
          </TableLite>
        </div>
      ),
    },
  ];

  // пустой экран (первый рендер до данных) — те же id, что и с данными
  if (!data) return widgets.map((w) => ({ ...w, render: () => <div className="text-xs text-ink-300">Загрузка…</div> }));
  return widgets;
}

/* =============================== мелочь =================================== */

const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

function weekdayLabel(isoDate: string) {
  const d = new Date(`${isoDate}T00:00:00`);
  return WEEKDAYS[(d.getDay() + 6) % 7];
}

const DAY_MS = 86400_000;

/** Колонки теплокарты: «1-я», «2-я», … недели от начала периода (не больше 8). */
function weekColumns(from: string | null, to: string | null): string[] {
  if (!from || !to) return [];
  const days = Math.round((new Date(`${to}T00:00:00`).getTime() - new Date(`${from}T00:00:00`).getTime()) / DAY_MS) + 1;
  return Array.from({ length: Math.min(Math.max(Math.ceil(days / 7), 1), 8) }, (_, i) => `${i + 1}-я`);
}

function weekOf(isoDate: string, from: string | null, weeks: string[]): string {
  if (!from || !weeks.length) return "";
  const index = Math.floor(
    (new Date(`${isoDate}T00:00:00`).getTime() - new Date(`${from}T00:00:00`).getTime()) / (7 * DAY_MS),
  );
  return weeks[Math.max(0, Math.min(weeks.length - 1, index))] ?? "";
}
