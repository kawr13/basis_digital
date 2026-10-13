/**
 * «Постановка судов» — ручной планировочный документ.
 *
 * Это тот же план, что ведут на бумаге/в Excel: строки — причалы (у каждого
 * своя «дорожка»), колонки — сутки, разбитые на 4 слота по 6 часов, полосы —
 * суда. Отличие от остального кабинета: данные не из 1С, их заводит диспетчер,
 * поэтому здесь можно создавать и править строки, а посмотреть — любой период.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type Berth,
  type Placement,
  type PlacementInput,
  type VesselPlan,
} from "../api";
import { useAuth } from "../auth";
import { AppShell } from "../components/AppShell";
import { Field, FormError, Modal, Notice, errText } from "../components/admin/ui";
import { dmy } from "../format";
import { resolveVesselRights, roleListLabel, usePublicSettings } from "../settings";
import {
  addDays, barGeometry, barText, cargoColor, daysInRange, isWeekend, parseLocal,
  periodLabel, slotLabel, slotsCount, SLOT_HOURS, ymd,
} from "../vessels";

/** Ширина одного слота в пикселях: 4 слота = сутки ≈ 132px, читается и на ноутбуке. */
const SLOT_W = 33;
/** Левая липкая колонка с причалом. */
const LEFT_W = 168;
const ROW_H = 34;

const PRESETS = [
  { label: "Неделя", days: 7 },
  { label: "2 недели", days: 14 },
  { label: "Месяц", days: 31 },
];

type Form = {
  id?: number;
  berth_id: number;
  lane: number;
  vessel: string;
  volume: string;
  cargo: string;
  note: string;
  startDate: string;
  startHour: number;
  endDate: string;
  endHour: number;
};

const toIso = (d: string, hour: number) => `${d}T${String(hour).padStart(2, "0")}:00:00`;

function formFrom(p: Placement | null, berths: Berth[]): Form {
  const today = ymd(new Date());
  if (!p) {
    return {
      berth_id: berths[0]?.id ?? 0,
      lane: 1,
      vessel: "",
      volume: "",
      cargo: "",
      note: "",
      startDate: today,
      startHour: 0,
      endDate: today,
      endHour: 6,
    };
  }
  const s = parseLocal(p.starts_at);
  const e = parseLocal(p.ends_at);
  return {
    id: p.id,
    berth_id: p.berth_id,
    lane: p.lane,
    vessel: p.vessel,
    volume: p.volume ?? "",
    cargo: p.cargo ?? "",
    note: p.note ?? "",
    startDate: ymd(s),
    startHour: s.getHours(),
    endDate: ymd(e),
    endHour: e.getHours(),
  };
}

export default function VesselsPage() {
  const { user } = useAuth();
  // Права на план — из настроек админки (vessels.edit_roles / vessels.delete_roles),
  // те же значения проверяет бэкенд. Кнопки просто так показывать нельзя: пользователь
  // упрётся в 403 и решит, что кабинет сломан.
  const settings = usePublicSettings();
  const rights = resolveVesselRights(user, settings);
  const canEdit = rights.edit;
  const canDelete = rights.delete;

  const [today] = useState(() => ymd(new Date()));
  const [from, setFrom] = useState(today);
  const [to, setTo] = useState(addDays(today, 30));
  const [plan, setPlan] = useState<VesselPlan | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<Form | null>(null);
  const [formError, setFormError] = useState("");
  const [saved, setSaved] = useState("");
  const [detail, setDetail] = useState<Placement | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setPlan(await api.vesselPlan(from, to));
      setError("");
    } catch (e) {
      setError(errText(e, "Не удалось загрузить план"));
    } finally {
      setBusy(false);
    }
  }, [from, to]);

  useEffect(() => {
    void load();
  }, [load]);

  const days = useMemo(() => daysInRange(from, to), [from, to]);
  const totalSlots = days.length * SLOT_HOURS.length;
  const berths = plan?.berths.filter((b) => b.is_active) ?? [];

  /** Строки сетки: причал × дорожка. */
  const rows = useMemo(() => {
    const out: { berth: Berth; lane: number; items: Placement[] }[] = [];
    for (const b of berths) {
      for (let lane = 1; lane <= Math.max(b.lanes, 1); lane += 1) {
        out.push({
          berth: b,
          lane,
          items: (plan?.placements ?? []).filter((p) => p.berth_id === b.id && p.lane === lane),
        });
      }
    }
    return out;
  }, [berths, plan]);

  const legend = useMemo(() => {
    const seen = new Map<string, string>();
    for (const p of plan?.placements ?? []) {
      const key = (p.cargo || "без груза").toUpperCase();
      if (!seen.has(key)) seen.set(key, cargoColor(p.cargo));
    }
    return [...seen.entries()];
  }, [plan]);

  const setPreset = (daysCount: number) => {
    setFrom(ymd(new Date()));
    setTo(addDays(ymd(new Date()), daysCount - 1));
  };

  const openCreate = () => {
    setFormError("");
    setForm(formFrom(null, berths));
  };

  const openEdit = (p: Placement) => {
    setFormError("");
    setDetail(null);
    setForm(formFrom(p, berths));
  };

  const submit = async () => {
    if (!form) return;
    setFormError("");
    const body: PlacementInput = {
      berth_id: form.berth_id,
      lane: form.lane,
      vessel: form.vessel.trim(),
      volume: form.volume.trim() || null,
      cargo: form.cargo.trim() || null,
      note: form.note.trim() || null,
      starts_at: toIso(form.startDate, form.startHour),
      ends_at: toIso(form.endDate, form.endHour),
    };
    try {
      if (form.id) await api.updatePlacement(form.id, body);
      else await api.createPlacement(body);
      setForm(null);
      setSaved(form.id ? "постановка обновлена" : "постановка добавлена");
      window.setTimeout(() => setSaved(""), 6000);
      await load();
    } catch (e) {
      // 409 — занятая дорожка: текст бэкенда объясняет, какое судно мешает
      setFormError(errText(e, "Не удалось сохранить"));
    }
  };

  const remove = async (p: Placement) => {
    try {
      await api.deletePlacement(p.id);
      setDetail(null);
      setSaved("постановка удалена");
      window.setTimeout(() => setSaved(""), 6000);
      await load();
    } catch (e) {
      setError(errText(e, "Не удалось удалить"));
    }
  };

  const laneCount = berths.find((b) => b.id === form?.berth_id)?.lanes ?? 1;
  const gridWidth = LEFT_W + totalSlots * SLOT_W;

  return (
    <AppShell
      asOf={undefined}
      scopeLabel={`${days.length} сут · ${berths.length} причалов`}
      actions={
        <>
          {saved && (
            <span className="text-[11px] text-ok" data-saved-note>{saved}</span>
          )}
          {PRESETS.map((p) => (
            <button
              key={p.days}
              className={`btn text-xs ${to === addDays(from, p.days - 1) ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setPreset(p.days)}
              data-preset={p.days}
            >
              {p.label}
            </button>
          ))}
          <input
            type="date"
            className="field w-[140px]"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            data-period-from
          />
          <input
            type="date"
            className="field w-[140px]"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            data-period-to
          />
          <button className="btn-ghost text-xs" onClick={load} disabled={busy}>
            {busy ? "Загрузка…" : "Обновить"}
          </button>
          {canEdit && (
            <button className="btn btn-primary text-xs" onClick={openCreate} data-admin-action="new-placement">
              Новая постановка
            </button>
          )}
        </>
      }
    >
      <div className="panel p-4" data-vessels-page>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="panel-title">План постановки судов</div>
            <p className="mt-1 text-sm text-ink-300">
              {periodLabel(from, to)} · сутки разбиты на слоты {SLOT_HOURS.map(slotLabel).join(", ")}
            </p>
          </div>
          {legend.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {legend.map(([cargo, color]) => (
                <span key={cargo} className="chip" style={{ background: `${color}1a`, color }}>
                  <span className="inline-block w-2 h-2 rounded-sm mr-1" style={{ background: color }} />
                  {cargo}
                </span>
              ))}
            </div>
          )}
        </div>

        {error && (
          <div className="mt-3 text-sm text-crit" role="alert">
            {error}
          </div>
        )}
        {!canEdit && (
          <p className="mt-3 text-xs text-ink-300">
            План можно смотреть и печатать; ставят и правят его: {roleListLabel(settings, "vessels.edit_roles", "admin,top")}.
          </p>
        )}

        {plan && plan.placements.length === 0 && (
          <p className="mt-4 text-sm text-ink-300">
            За этот период постановок нет. {canEdit ? "Добавьте первую кнопкой «Новая постановка»." : ""}
          </p>
        )}

        {/* Сетка: горизонтальная прокрутка обязательна — месяц это 124 слота */}
        <div className="mt-4 overflow-auto max-h-[68vh] border border-ink-700 rounded-md" data-vp-grid>
          <div style={{ width: gridWidth }} className="relative">
            {/* шапка: сутки + подписи слотов */}
            <div className="flex sticky top-0 z-20 bg-white/90 backdrop-blur border-b border-ink-700">
              <div
                className="sticky left-0 z-30 bg-white/95 border-r border-ink-700 text-[11px] uppercase tracking-wider text-ink-400 px-2 py-1"
                style={{ width: LEFT_W }}
              >
                Причал / дорожка
              </div>
              {days.map((d) => (
                <div
                  key={d}
                  className={`border-r border-ink-700 ${isWeekend(d) ? "bg-ink-800" : ""}`}
                  style={{ width: SLOT_W * 4 }}
                  data-day={d}
                >
                  <div className="text-[11px] text-center text-ink-300 py-0.5">{dmy(d).slice(0, 5)}</div>
                  <div className="flex">
                    {SLOT_HOURS.map((h) => (
                      <div
                        key={h}
                        className="text-[9px] text-center text-ink-400 border-l border-ink-700 first:border-l-0"
                        style={{ width: SLOT_W }}
                      >
                        {String(h).padStart(2, "0")}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            {/* строки */}
            {rows.map(({ berth, lane, items }) => (
              <div key={`${berth.id}-${lane}`} className="flex border-b border-ink-700" data-lane={`${berth.code}|${lane}`}>
                <div
                  className="sticky left-0 z-10 bg-white/95 border-r border-ink-700 px-2 py-1 text-xs"
                  style={{ width: LEFT_W }}
                >
                  <div className="font-medium">
                    {berth.code}
                    {berth.depth ? <span className="text-ink-400"> · {String(berth.depth).replace(".", ",")} м</span> : null}
                  </div>
                  <div className="text-[10px] text-ink-400">
                    дорожка {lane}
                    {berth.length_m ? ` · ${berth.length_m} м` : ""}
                  </div>
                </div>

                <div className="relative" style={{ width: totalSlots * SLOT_W, height: ROW_H }}>
                  {/* фоновые колонки: выходные подсвечены, сетка слотов */}
                  {days.map((d) => (
                    <div
                      key={d}
                      className={`absolute top-0 bottom-0 border-r border-ink-700 ${isWeekend(d) ? "bg-ink-800/70" : ""}`}
                      style={{ left: days.indexOf(d) * SLOT_W * 4, width: SLOT_W * 4 }}
                    />
                  ))}

                  {items.map((p) => {
                    const geo = barGeometry(p, from, totalSlots);
                    if (!geo) return null;
                    const color = cargoColor(p.cargo);
                    return (
                      <button
                        key={p.id}
                        className="absolute top-1 h-[26px] rounded-md text-left text-[11px] px-1.5 truncate text-white shadow-sm hover:brightness-110 focus:outline-none focus:ring-2 focus:ring-brand-sky"
                        style={{
                          left: geo.left * SLOT_W,
                          width: geo.width * SLOT_W - 3,
                          background: `linear-gradient(180deg, ${color}f0, ${color}cc)`,
                          borderLeft: geo.clippedStart ? "3px dotted rgba(255,255,255,.85)" : "3px solid rgba(0,0,0,.18)",
                        }}
                        title={`${barText(p)}${p.cargo ? ` · ${p.cargo}` : ""}\n${parseLocal(p.starts_at).toLocaleString("ru-RU")} — ${parseLocal(p.ends_at).toLocaleString("ru-RU")}`}
                        onClick={() => setDetail(p)}
                        data-vp-bar={p.id}
                        data-berth={berth.code}
                        data-cargo={(p.cargo || "").toUpperCase()}
                      >
                        {barText(p)}
                        {geo.clippedEnd ? " …" : ""}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>

        <p className="mt-3 text-[11px] text-ink-400">
          Пунктирный край — постановка началась раньше выбранного периода. Клик по полосе — подробности,
          правка и удаление.
        </p>
      </div>

      {detail && (
        <Modal title={`Постановка · ${detail.vessel}`} onClose={() => setDetail(null)} testId="placement-detail">
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="panel-title">Причал</dt>
              <dd>{berths.find((b) => b.id === detail.berth_id)?.code ?? "—"} · дорожка {detail.lane}</dd>
            </div>
            <div>
              <dt className="panel-title">Груз</dt>
              <dd>{detail.cargo || "не указан"}</dd>
            </div>
            <div>
              <dt className="panel-title">Начало</dt>
              <dd className="num">{parseLocal(detail.starts_at).toLocaleString("ru-RU")}</dd>
            </div>
            <div>
              <dt className="panel-title">Окончание</dt>
              <dd className="num">{parseLocal(detail.ends_at).toLocaleString("ru-RU")}</dd>
            </div>
            <div>
              <dt className="panel-title">Объём / тоннаж</dt>
              <dd>{detail.volume || "—"}</dd>
            </div>
            <div>
              <dt className="panel-title">Слотов в плане</dt>
              <dd className="num">{slotsCount(detail)}</dd>
            </div>
          </dl>
          {detail.note && (
            <p className="mt-3 text-sm text-ink-300" data-detail-note>{detail.note}</p>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            {canEdit && (
              <button className="btn-primary text-sm" onClick={() => openEdit(detail)} data-admin-action="edit-placement">
                Править
              </button>
            )}
            {canDelete && (
              <button className="btn-ghost text-sm text-crit" onClick={() => remove(detail)} data-admin-action="delete-placement">
                Удалить
              </button>
            )}
            <span className="ml-auto text-[11px] text-ink-400">
              изменил {new Date(`${detail.updated_at}Z`).toLocaleString("ru-RU")}
            </span>
          </div>
        </Modal>
      )}

      {form && (
        <Modal
          title={form.id ? "Правка постановки" : "Новая постановка"}
          onClose={() => setForm(null)}
          testId="placement-form"
          footer={
            <div className="space-y-3">
              <FormError text={formError} />
              <div className="flex flex-wrap items-center gap-2">
                <button className="btn-primary text-sm" onClick={submit} data-admin-action="save-placement">
                  {form.id ? "Сохранить" : "Поставить судно"}
                </button>
                <button className="btn-ghost text-sm" onClick={() => setForm(null)}>
                  Отмена
                </button>
              </div>
            </div>
          }
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Причал">
              <select
                className="field"
                value={form.berth_id}
                onChange={(e) => setForm({ ...form, berth_id: Number(e.target.value), lane: 1 })}
                data-field="berth"
              >
                {berths.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.code}
                    {b.length_m ? ` · ${b.length_m} м` : ""}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Дорожка" hint="сколько судов у причала одновременно">
              <select className="field" value={form.lane} onChange={(e) => setForm({ ...form, lane: Number(e.target.value) })} data-field="lane">
                {Array.from({ length: Math.max(laneCount, 1) }, (_, i) => i + 1).map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </Field>
            <Field label="Судно">
              <input className="field" value={form.vessel} onChange={(e) => setForm({ ...form, vessel: e.target.value })} data-field="vessel" placeholder="CROWN SAPPHIRE" />
            </Field>
            <Field label="Объём / тоннаж" hint="как в плане: 5 500 или (100+40) + 4 000">
              <input className="field" value={form.volume} onChange={(e) => setForm({ ...form, volume: e.target.value })} data-field="volume" />
            </Field>
            <Field label="Груз" hint="БШ, БАНАНЫ-ПАЛЛЕТ, МЛК (БКК)…">
              <input className="field" value={form.cargo} onChange={(e) => setForm({ ...form, cargo: e.target.value })} data-field="cargo" list="cargo-list" />
              <datalist id="cargo-list">
                {["БШ", "БАНАНЫ-ПАЛЛЕТ", "МЛК (БКК)", "сборник", "ГИДРОКАРБ", "НИТРАТ", "СЕЛИТРА"].map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </Field>
            <Field label="Примечание">
              <input className="field" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} data-field="note" placeholder="(КМП-ЮМФ)" />
            </Field>

            <Field label="Начало" hint="слот 00 / 06 / 12 / 18">
              <div className="flex gap-2">
                <input type="date" className="field" value={form.startDate} onChange={(e) => setForm({ ...form, startDate: e.target.value })} data-field="start-date" />
                <select className="field w-24" value={form.startHour} onChange={(e) => setForm({ ...form, startHour: Number(e.target.value) })} data-field="start-hour">
                  {SLOT_HOURS.map((h) => (
                    <option key={h} value={h}>{slotLabel(h)}</option>
                  ))}
                </select>
              </div>
            </Field>
            <Field label="Окончание" hint="последний занятый слот, включительно">
              <div className="flex gap-2">
                <input type="date" className="field" value={form.endDate} onChange={(e) => setForm({ ...form, endDate: e.target.value })} data-field="end-date" />
                <select className="field w-24" value={form.endHour} onChange={(e) => setForm({ ...form, endHour: Number(e.target.value) })} data-field="end-hour">
                  {SLOT_HOURS.map((h) => (
                    <option key={h} value={h}>{slotLabel(h)}</option>
                  ))}
                </select>
              </div>
            </Field>
          </div>

          <Notice text="Если на дорожке уже стоит судно, кабинет не даст поставить второе и скажет, какое именно." />
        </Modal>
      )}
    </AppShell>
  );
}
