import { useEffect, useRef, useState } from "react";
import { api, type DispatcherDay } from "../api";
import { dayFull, nf, pct } from "../format";

/**
 * Панель детализации суток по отчёту диспетчера: что происходило в порту
 * в выбранный день — смены, обработка судов, вагоны, автомашины, подходы.
 *
 * Открывается кликом по столбцу графика. Данные — `/api/dispatcher/day`,
 * то есть те же строки витрины, из которых считаются графики: цифра в панели
 * и цифра на графике физически одна и та же.
 */
export function DispatcherDayPanel({
  day, shift, onClose,
}: {
  day: string;
  shift: string;
  onClose: () => void;
}) {
  const [data, setData] = useState<DispatcherDay | null>(null);
  const [error, setError] = useState("");
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError("");
    api
      .dispatcherDay(day, shift)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Не удалось загрузить день"));
    return () => {
      alive = false;
    };
  }, [day, shift]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const tone = (v: number | null) => (v === null ? "" : v >= 100 ? "text-ok" : v >= 80 ? "text-accent" : "text-crit");

  return (
    <aside
      ref={ref}
      tabIndex={-1}
      data-drilldown="dispatcher-day"
      data-drilldown-day={day}
      className="dd-panel panel fixed z-30 outline-none inset-x-0 bottom-0 max-h-[72vh] overflow-y-auto rounded-b-none p-4
        md:inset-x-auto md:right-6 md:top-[calc(var(--header-h,89px)+8px)] md:bottom-6 md:w-[min(760px,calc(100vw_-_3rem))] md:max-h-none md:rounded-b-lg
        md:shadow-[0_30px_70px_-25px_rgba(15,27,45,.55)]"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="panel-title">Смена диспетчера</div>
          <h2 className="text-lg font-semibold text-ink-100">{dayFull(day)}</h2>
        </div>
        <button className="btn-ghost btn-mini" data-drilldown-close onClick={onClose}>
          Закрыть ✕
        </button>
      </div>

      {error && <div className="mt-3 panel border-crit/40 p-2 text-xs text-crit">{error}</div>}
      {!error && !data && <div className="mt-4 text-sm text-ink-300">Загрузка…</div>}

      {data && (
        <div className="mt-3 space-y-4 text-xs">
          {/* ---- смены ---- */}
          {data.shifts.map((s) => (
            <section key={s.shift} className="rounded-md border border-ink-700 p-2.5">
              <div className="flex items-baseline justify-between">
                <h3 className="text-[13px] font-semibold text-ink-100">{s.label}</h3>
                <span className="text-ink-300">
                  {s.dispatcher || "диспетчер не указан"}
                  {s.crew_no ? ` · бригада №${s.crew_no}` : ""}
                </span>
              </div>
              <div className="mt-1.5 grid grid-cols-4 gap-2">
                <Metric label="вышло" value={nf(s.present, 0)} />
                <Metric label="штат" value={nf(s.staff, 0)} />
                <Metric label="больничных" value={nf(s.sick, 0)} tone={s.sick > 0 ? "text-warn" : undefined} />
                <Metric label="отпуск" value={nf(s.vacation, 0)} />
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1">
                {s.contractors.filter((c) => c.count > 0).map((c) => (
                  <span key={c.name} className="chip bg-ink-800 text-ink-300">
                    {c.name} · <b className="num text-ink-100">{nf(c.count, 0)}</b>
                  </span>
                ))}
                <span className="chip bg-ink-800 text-ink-300">
                  ричстакеры: <b className="num text-ok">{nf(s.rs_ok_start, 0)}</b>→
                  <b className="num text-ok">{nf(s.rs_ok_end, 0)}</b> испр.,{" "}
                  <b className="num text-warn">{nf(s.rs_repair_end, 0)}</b> ремонт
                </span>
              </div>
            </section>
          ))}
          {!data.shifts.length && <p className="text-ink-300">Смен за этот день нет.</p>}

          {/* ---- суда ---- */}
          <section>
            <h3 className="panel-title">Суда · {data.vessels.length}</h3>
            <table className="mt-1.5 w-full text-[11px]">
              <thead>
                <tr className="text-ink-400">
                  <th className="py-1 text-left font-normal">причал / судно</th>
                  <th className="py-1 text-right font-normal">план</th>
                  <th className="py-1 text-right font-normal">факт</th>
                  <th className="py-1 text-right font-normal">%</th>
                  <th className="py-1 text-right font-normal">т/ч</th>
                </tr>
              </thead>
              <tbody>
                {data.vessels.map((v, i) => (
                  <tr key={`${v.vessel}-${i}`} className="border-t border-ink-700 align-top">
                    <td className="py-1.5 pr-2">
                      <div className="text-ink-100">{v.vessel}</div>
                      <div className="text-[10px] text-ink-300">
                        {v.berth} · {v.cargo_group} · {v.shift}
                        {v.downtime_h ? ` · потери ${nf(v.downtime_h, 1)} ч` : ""}
                      </div>
                      {v.note && <div className="text-[10px] text-ink-300 italic">{v.note}</div>}
                    </td>
                    <td className="py-1.5 text-right num">{nf(v.plan_shift_t, 0)}</td>
                    <td className="py-1.5 text-right num font-semibold">{nf(v.fact_shift_t, 0)}</td>
                    <td className={`py-1.5 text-right num ${tone(v.plan_pct)}`}>
                      {v.plan_pct === null ? "—" : pct(v.plan_pct / 100, 0)}
                    </td>
                    <td className="py-1.5 text-right num">{nf(v.tph, 0)}</td>
                  </tr>
                ))}
                {!data.vessels.length && (
                  <tr>
                    <td colSpan={5} className="py-2 text-ink-300">Судов не обрабатывали</td>
                  </tr>
                )}
              </tbody>
            </table>
          </section>

          {/* ---- вагоны и машины ---- */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <section>
              <h3 className="panel-title">Вагоны</h3>
              <ul className="mt-1.5 space-y-1">
                {data.rails.map((r, i) => (
                  <li key={`${r.cargo_group}-${i}`} className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-ink-100">{r.cargo_group}</span>
                    <span className="num shrink-0 text-ink-300">
                      <b className="text-ink-100">{nf(r.processed, 0)}</b> обр. · {nf(r.port_start, 0)}→
                      {nf(r.port_end, 0)}
                    </span>
                  </li>
                ))}
                {!data.rails.length && <li className="text-ink-300">Нет данных</li>}
              </ul>
            </section>
            <section>
              <h3 className="panel-title">Автомашины</h3>
              <ul className="mt-1.5 space-y-1">
                {data.trucks.map((t, i) => (
                  <li key={`${t.cargo_group}-${i}`} className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-ink-100">{t.cargo_group}</span>
                    <span className="num shrink-0 text-ink-300">
                      <b className="text-ink-100">{nf(t.total, 0)}</b>
                      <span className="ml-1">
                        и{nf(t.imp_unload + t.imp_load, 0)}/э{nf(t.exp_unload + t.exp_load, 0)}
                      </span>
                    </span>
                  </li>
                ))}
                {!data.trucks.length && <li className="text-ink-300">Нет данных</li>}
              </ul>
            </section>
          </div>

          {/* ---- выводка / постановка вагонов (блоки листа «ВЫВОД» и «ВВОД») ---- */}
          {(data.rail_out.length > 0 || data.rail_in.length > 0) && (
            <section>
              <h3 className="panel-title">Выводка и постановка вагонов</h3>
              <ul className="mt-1.5 space-y-1">
                {data.rail_out.map((r, i) => (
                  <li key={`out-${i}`} className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-ink-100">
                      вывод
                      <span className="text-ink-300">
                        {" "}· п/в {nf(r.semi_wagons, 0)}, крыт. {nf(r.covered, 0)}, платф. {nf(r.flatcars, 0)}
                        {r.accepted_by ? ` · принял ${r.accepted_by}` : ""}
                      </span>
                    </span>
                    <span className="num shrink-0 text-ink-300">
                      <b className="text-ink-100">{nf(r.total, 0)}</b> ваг. · план {r.plan_time || "—"}
                      {r.fact_time ? ` → ${r.fact_time}` : ""}
                      {r.delay_min !== null && (
                        <span className={r.delay_min > 0 ? " text-crit" : " text-ok"}>
                          {" "}
                          {r.delay_min > 0 ? "+" : ""}
                          {nf(r.delay_min, 0)} мин
                        </span>
                      )}
                    </span>
                  </li>
                ))}
                {data.rail_in.map((r, i) => (
                  <li key={`in-${i}`} className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-ink-100">
                      ввод
                      <span className="text-ink-300">
                        {" "}· груж. {nf(r.load_semi + r.load_covered + r.load_flat, 0)}, пор.{" "}
                        {nf(r.empty_semi + r.empty_covered + r.empty_flat, 0)}
                      </span>
                    </span>
                    <span className="num shrink-0 text-ink-300">
                      <b className="text-ink-100">{nf(r.total, 0)}</b> ваг. · план {r.plan_time || "—"}
                      {r.settle_time ? ` · расст. ${r.settle_time}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {data.platforms.length > 0 && (
            <section>
              <h3 className="panel-title">Платформы, выведенные из аренды</h3>
              <ul className="mt-1.5 space-y-1">
                {data.platforms.map((pl, i) => (
                  <li key={`${pl.owner}-${i}`} className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-ink-100">
                      {pl.owner}
                      <span className="text-ink-300"> · {pl.shift === "08-20" ? "день" : "ночь"}</span>
                    </span>
                    <span className="num shrink-0 text-ink-300">
                      остаток <b className="text-ink-100">{nf(pl.remaining, 0)}</b> · всего {nf(pl.total_cars, 0)} ·
                      терминал {nf(pl.terminal, 0)} · отправлено {nf(pl.sent, 0)}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* ---- подходы ---- */}
          {data.arrivals.length > 0 && (
            <section>
              <h3 className="panel-title">Суда на подходе</h3>
              <ul className="mt-1.5 space-y-1">
                {data.arrivals.map((a, i) => (
                  <li key={`${a.vessel}-${i}`} className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-ink-100">
                      {a.vessel}
                      <span className="text-ink-300"> · {a.berth} · {a.cargo_group}</span>
                    </span>
                    <span className="num shrink-0 text-ink-300">
                      {a.eta ? new Date(a.eta).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"}{" "}
                      · {a.plan_raw || "—"}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </aside>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded bg-ink-800/70 px-2 py-1">
      <div className="text-[10px] uppercase tracking-wide text-ink-400">{label}</div>
      <div className={`num text-sm font-semibold ${tone ?? "text-ink-100"}`}>{value}</div>
    </div>
  );
}
