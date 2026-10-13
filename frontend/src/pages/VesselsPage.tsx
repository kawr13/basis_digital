/**
 * «Постановка судов» — ручной планировочный документ.
 *
 * План читается как лист миллиметровки: слева направо — время (сутки разбиты на
 * 4 слота по 6 часов), сверху вниз — длина причала, 10 метров на ячейку (100 м —
 * это 10 ячеек). Судно занимает полосу: отметка швартовки + его длина. Два судна
 * мешают друг другу, только если пересекаются и по времени, и по метрам.
 * Данные не из 1С: их заводит диспетчер, поэтому здесь можно создавать и править
 * строки, а посмотреть — любой период.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  addDays, addSlots, barGeometry, barText, cargoColor, daysInRange, isWeekend, isoLocal,
  lengthGeometry, lengthLabel, M_PER_CELL, parseLocal, periodLabel, rulerMarks, slotLabel,
  slotsCount, SLOT_HOURS, ymd,
} from "../vessels";

/**
 * Ширина слота. Минимум — тот, при котором ещё читается «00»; дальше поле
 * растягиваем на всю доступную ширину, чтобы план не обрывался задолго до
 * правого края экрана (недельный период на широком мониторе оставлял ~360 px
 * пустоты).
 */
const SLOT_W_MIN = 22;
const SLOT_W_MAX = 64;
/** Левая липкая колонка с причалом. */
const LEFT_W = 168;
/** Высота одной ячейки вертикальной шкалы = 10 метров длины причала. */
const CELL_H = 12;

const PRESETS = [
  { label: "Неделя", days: 7 },
  { label: "2 недели", days: 14 },
  { label: "Месяц", days: 31 },
];

type Form = {
  id?: number;
  berth_id: number;
  /** длина судна в метрах; пустая строка = не задана (занимает причал до конца) */
  length_m: string;
  /** отметка швартовки в метрах от начала причала */
  offset_m: string;
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
      length_m: "",
      offset_m: "0",
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
    length_m: p.length_m == null ? "" : String(p.length_m),
    offset_m: String(p.offset_m ?? 0),
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
  const [freeHint, setFreeHint] = useState("");
  // WYSIWYG: карандаш превращает полосы в ручки — судно двигают мышью
  const [wysiwyg, setWysiwyg] = useState(false);
  // перетаскивание считает не «дельту от старта», а где курсор сейчас: блок
  // причала под курсором = целевой причал, поэтому судно переезжает с РП-2 на
  // РП-3 одним движением
  const [drag, setDrag] = useState<{
    id: number; startX: number; dx: number; grabY: number; ownTop: number;
    blocks: { id: number; top: number; height: number }[];
    berthId: number; offsetM: number; topInBlock: number;
  } | null>(null);
  // после перетаскивания клик по полосе не должен открывать карточку
  const suppressClick = useRef(false);

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

  // поле плана обязано доставать до правого края: ширину слота считаем от
  // реальной ширины контейнера, а не зашиваем
  const gridRef = useRef<HTMLDivElement>(null);
  const [availW, setAvailW] = useState(0);
  useEffect(() => {
    const el = gridRef.current;
    if (!el) return;
    const measure = () => setAvailW(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [plan]);
  const slotW = useMemo(() => {
    if (!availW || !totalSlots) return SLOT_W_MIN;
    // дробная ширина намеренно: округляя до целых пикселей, справа остаётся
    // незаполненная полоса (до половины слота), а поле должно доставать до края
    const fit = (availW - LEFT_W) / totalSlots;
    return Math.max(SLOT_W_MIN, Math.min(SLOT_W_MAX, fit));
  }, [availW, totalSlots]);
  const berths = plan?.berths.filter((b) => b.is_active) ?? [];

  /** Блоки сетки: по одному на причал. Внутри — шкала длины, 10 м на ячейку. */
  const blocks = useMemo(
    () =>
      berths.map((b) => ({
        berth: b,
        cells: Math.max(b.length_cells ?? 10, 1),
        items: (plan?.placements ?? []).filter((p) => p.berth_id === b.id),
      })),
    [berths, plan],
  );

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
      lane: 1,
      length_m: form.length_m.trim() ? Number(form.length_m) : null,
      offset_m: form.offset_m.trim() ? Number(form.offset_m) : 0,
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
      // 409 — на этом отрезке причала уже стоит судно: текст бэкенда объясняет,
      // какое именно и где именно пересекаются метры
      setFormError(errText(e, "Не удалось сохранить"));
    }
  };

  /** Подсказать свободное место: диспетчер не должен гадать и ловить 409. */
  const findFree = async () => {
    if (!form) return;
    const len = Number(form.length_m);
    if (!len || len <= 0) {
      setFormError("Сначала укажите длину судна — тогда подскажу, где свободно");
      return;
    }
    setFormError("");
    try {
      const spot = await api.freeOffset(
        form.berth_id,
        toIso(form.startDate, form.startHour),
        toIso(form.endDate, form.endHour),
        len,
      );
      if (spot.offset_m === null || !spot.fits) {
        setFormError(`На причале ${spot.berth_length_m} м нет свободного куска в ${len} м на это время`);
        return;
      }
      setForm({ ...form, offset_m: String(spot.offset_m) });
      setFreeHint(`свободно с ${spot.offset_m} м`);
    } catch (e) {
      setFormError(errText(e, "Не удалось подобрать место"));
    }
  };

  /* -------------------------- WYSIWYG: судно мышью -------------------------- */

  /** Поле шкалы под курсором; в шапке причала (между полями) — ближайший по центру. */
  const pickBlock = (blocks: { id: number; top: number; height: number }[], y: number) => {
    if (!blocks.length) return null;
    const hit = blocks.find((b) => y >= b.top && y < b.top + b.height);
    if (hit) return hit;
    let best = blocks[0];
    let bestD = Math.abs(y - (blocks[0].top + blocks[0].height / 2));
    for (const b of blocks) {
      const d = Math.abs(y - (b.top + b.height / 2));
      if (d < bestD) {
        best = b;
        bestD = d;
      }
    }
    return best;
  };

  const startDrag = (e: React.PointerEvent, p: Placement) => {
    const bar = e.currentTarget as HTMLElement;
    bar.setPointerCapture?.(e.pointerId);
    const barTop = bar.getBoundingClientRect().top;
    // мериим поля шкалы, а не блоки: у блока сверху строка с названием причала,
    // и отсчёт метров от неё съехал бы на полтора десятка метров
    const blocks = [...document.querySelectorAll<HTMLElement>("[data-berth-scale]")]
      .map((el) => {
        const r = el.getBoundingClientRect();
        return { id: Number(el.dataset.berthScale), top: r.top, height: r.height };
      })
      .filter((b) => Number.isFinite(b.id));
    setDrag({
      id: p.id,
      startX: e.clientX,
      dx: 0,
      grabY: e.clientY - barTop,
      ownTop: blocks.find((b) => b.id === p.berth_id)?.top ?? barTop,
      blocks,
      berthId: p.berth_id,
      offsetM: p.offset_m ?? 0,
      topInBlock: lengthGeometry(p, CELL_H).top,
    });
  };

  const moveDrag = (e: React.PointerEvent, p: Placement) => {
    if (!drag || drag.id !== p.id) return;
    const block = pickBlock(drag.blocks, e.clientY);
    if (!block) {
      setDrag({ ...drag, dx: e.clientX - drag.startX });
      return;
    }
    const berth = berths.find((b) => b.id === block.id);
    // судно без длины занимает причал целиком — двигать его по метрам нельзя
    const room = berth?.length_m && p.length_m ? Math.max(berth.length_m - p.length_m, 0) : 0;
    const raw = ((e.clientY - block.top - drag.grabY) / CELL_H) * M_PER_CELL;
    const offsetM = Math.min(Math.max(Math.round(raw / 5) * 5, 0), room);
    setDrag({ ...drag, dx: e.clientX - drag.startX, berthId: block.id, offsetM });
  };

  const endDrag = async (p: Placement) => {
    if (!drag) return;
    const { dx, berthId, offsetM } = drag;
    setDrag(null);
    const slots = Math.round(dx / slotW);
    const same =
      !slots && berthId === p.berth_id && offsetM === (p.offset_m ?? 0);
    if (same) return;
    suppressClick.current = true;
    window.setTimeout(() => (suppressClick.current = false), 0);
    await movePlacement(p, { slots, berthId, offsetM });
  };

  /** Клавиатура вместо мыши: стрелки — слот по времени и 5 м по причалу. */
  const nudge = async (p: Placement, e: React.KeyboardEvent) => {
    const step: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -5], ArrowDown: [0, 5],
    };
    const move = step[e.key];
    if (!move) return;
    e.preventDefault();
    await movePlacement(p, { slots: move[0], berthId: p.berth_id, offsetM: (p.offset_m ?? 0) + move[1] });
  };

  /** Переставить: на N слотов по времени, на причал `berthId` и на отметку `offsetM`. */
  const movePlacement = async (
    p: Placement,
    dest: { slots: number; berthId: number; offsetM: number },
  ) => {
    const berth = berths.find((b) => b.id === dest.berthId);
    const start = addSlots(parseLocal(p.starts_at), dest.slots);
    const end = addSlots(parseLocal(p.ends_at), dest.slots);
    if (start < new Date(`${from}T00:00:00`) || end > new Date(`${to}T23:59:59`)) {
      setError("Судно нельзя вынести за границы выбранного периода");
      return;
    }
    const offset =
      berth?.length_m && p.length_m
        ? Math.min(Math.max(dest.offsetM, 0), Math.max(berth.length_m - p.length_m, 0))
        : Math.max(dest.offsetM, 0);
    try {
      await api.updatePlacement(p.id, {
        starts_at: isoLocal(start),
        ends_at: isoLocal(end),
        offset_m: offset,
        berth_id: dest.berthId,
      });
      setError("");
      const moved = dest.berthId !== p.berth_id ? ` → ${berth?.code}` : "";
      setSaved(`${p.vessel}${moved}: ${start.toLocaleDateString("ru-RU")} · ${offset} м`);
      window.setTimeout(() => setSaved(""), 5000);
      await load();
    } catch (e) {
      // 409 — наехали на другое судно: откат и текст бэкенда, где именно засада.
      // сначала перезагрузка (она чистит error), потом сообщение
      await load();
      setError(errText(e, "Не удалось переставить судно"));
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

  const formBerth = berths.find((b) => b.id === form?.berth_id);
  const gridWidth = LEFT_W + totalSlots * slotW;

  /* ------------------------------- PDF / печать ------------------------------ */

  /**
   * PDF — это печать браузера («Сохранить как PDF»). Другого способа дать
   * векторный текст с кириллицёй и поиском по PDF без тяжёлых зависимостей нет:
   * рисовать сетку картинкой (html2canvas) нельзя — панели на backdrop-filter
   * он не умеет, а план бы и поехал по цветам, и стал бы нечитаемым.
   *
   * В файл уходит ровно то, что выбрано сейчас: период, причалы и масштаб.
   * Сетку перед диалогом печатаем без прокрутки и сжимаем до ширины страницы,
   * чтобы правый край не отрезался.
   */
  const exportPdf = () => {
    // A3 landscape с полями 8 мм ≈ 1527 px при 96 dpi
    const printable = 1527;
    const scale = Math.min(1, +(printable / Math.max(gridWidth, 1)).toFixed(3));
    document.documentElement.style.setProperty("--vp-print-scale", String(scale));
    window.print();
  };

  return (
    <AppShell
      asOf={undefined}
      wide
      scopeLabel={`${days.length} сут · ${berths.length} причалов`}
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

        {/* Период и действия — слева, компактно. В шапке эти кнопки были крупнее
            и уезжали в правый угол к «Выйти», а план читают слева направо: управление
            должно стоять там же, где начинается таблица. */}
        <div className="mt-3 flex flex-wrap items-center gap-2" data-vessels-toolbar>
          {PRESETS.map((p) => (
            <button
              key={p.days}
              className={`btn btn-mini ${to === addDays(from, p.days - 1) ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setPreset(p.days)}
              data-preset={p.days}
            >
              {p.label}
            </button>
          ))}
          <input
            type="date"
            className="field-mini w-[132px]"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            aria-label="Начало периода"
            data-period-from
          />
          <input
            type="date"
            className="field-mini w-[132px]"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            aria-label="Конец периода"
            data-period-to
          />
          <button
            className="btn btn-mini btn-ghost"
            onClick={exportPdf}
            title="Сохранить план в PDF: в файл уходит выбранный период целиком"
            data-admin-action="export-pdf"
          >
            ⤓ PDF
          </button>
          <button className="btn btn-mini btn-ghost" onClick={load} disabled={busy}>
            {busy ? "Загрузка…" : "Обновить"}
          </button>
          {canEdit && (
            <button className="btn btn-mini btn-primary" onClick={openCreate} data-admin-action="new-placement">
              Новая постановка
            </button>
          )}
          {canEdit && (
            <button
              className={`btn btn-mini ${wysiwyg ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setWysiwyg((v) => !v)}
              aria-pressed={wysiwyg}
              title="Перетаскивание судов мышью: горизонталь — время, вертикаль — место у причала"
              data-admin-action="wysiwyg"
            >
              ✏ Корректировка
            </button>
          )}
          {saved && (
            <span className="text-[11px] text-ok" data-saved-note>
              {saved}
            </span>
          )}
        </div>

        {wysiwyg && canEdit && (
          <p className="mt-2 text-[11px] text-ink-300" data-wysiwyg-hint>
            Режим перетаскивания: тяните полосу мышью — горизонталь это слоты (шаг 6 часов),
            вертикаль — место у причала (шаг 5 м). Отпустили — сохранилось. Стрелки двигают
            полосу под фокусом.
          </p>
        )}

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
        <div ref={gridRef} className="mt-4 overflow-auto max-h-[68vh] border border-ink-700 rounded-md" data-vp-grid>
          <div style={{ width: gridWidth }} className="relative" data-vp-print>
            {/* шапка: сутки + подписи слотов */}
            <div className="flex sticky top-0 z-20 bg-white/90 backdrop-blur border-b border-ink-700">
              <div
                className="sticky left-0 z-30 bg-white/95 border-r border-ink-700 text-[11px] uppercase tracking-wider text-ink-400 px-2 py-1"
                style={{ width: LEFT_W }}
              >
                Причал / метры
              </div>
              {days.map((d) => (
                <div
                  key={d}
                  className={`border-r border-ink-700 ${isWeekend(d) ? "bg-ink-800" : ""}`}
                  style={{ width: slotW * 4 }}
                  data-day={d}
                >
                  <div className="text-[11px] text-center text-ink-300 py-0.5">{dmy(d).slice(0, 5)}</div>
                  <div className="flex">
                    {SLOT_HOURS.map((h) => (
                      <div
                        key={h}
                        className="text-[9px] text-center text-ink-400 border-l border-ink-700 first:border-l-0"
                        style={{ width: slotW }}
                      >
                        {String(h).padStart(2, "0")}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            {/* один блок на причал: заголовок, затем шкала длины 10 м на ячейку */}
            {blocks.map(({ berth, cells, items }) => (
              <div
                key={berth.id}
                className={`border-b border-ink-700 ${drag && drag.berthId === berth.id && drag.berthId !== items[0]?.berth_id ? "ring-2 ring-inset ring-brand-sky" : ""}`}
                data-berth-block={berth.code}
                data-berth-id={berth.id}
                data-drop-target={drag && drag.berthId === berth.id ? "1" : "0"}
              >
                <div className="flex bg-ink-900/5">
                  <div
                    className="sticky left-0 z-10 bg-white/95 border-r border-ink-700 px-2 py-0.5 text-xs"
                    style={{ width: LEFT_W }}
                  >
                    <span className="font-medium">{berth.code}</span>
                    {berth.depth ? (
                      <span className="text-[10px] text-ink-400">
                        {" "}· осадка {String(berth.depth).replace(".", ",")} м
                      </span>
                    ) : null}
                  </div>
                  <div className="text-[10px] text-ink-400 px-2 py-0.5" style={{ width: totalSlots * slotW }}>
                    {berth.length_m
                      ? `${berth.length_m} м длины · ${cells} ячеек по ${10} м`
                      : "метраж причала не задан · показываем 100 м"}
                  </div>
                </div>

                <div className="flex">
                  {/* линейка длины: подпись каждой ячейки в метрах */}
                  <div className="sticky left-0 z-10 bg-white/95 border-r border-ink-700" style={{ width: LEFT_W }}>
                    {rulerMarks(cells).map((m) => (
                      <div
                        key={m}
                        className="num text-right pr-2 text-[9px] text-ink-400 border-t border-ink-700/50"
                        style={{ height: CELL_H, lineHeight: `${CELL_H}px` }}
                        data-ruler={m}
                      >
                        {m % (CELL_H < 14 ? 20 : 10) === 0 ? m : ""}
                      </div>
                    ))}
                  </div>

                  <div className="relative" style={{ width: totalSlots * slotW, height: cells * CELL_H }} data-berth-scale={berth.id}>
                    {/* фоновые колонки: выходные подсвечены */}
                    {days.map((d) => (
                      <div
                        key={d}
                        className={`absolute top-0 bottom-0 border-r border-ink-700 ${isWeekend(d) ? "bg-ink-800/70" : ""}`}
                        style={{ left: days.indexOf(d) * slotW * 4, width: slotW * 4 }}
                      />
                    ))}
                    {/* горизонтальная шкала: граница каждой десятки метров */}
                    {Array.from({ length: cells - 1 }, (_, i) => (
                      <div
                        key={i}
                        className="absolute left-0 right-0 border-t border-ink-700/40"
                        style={{ top: (i + 1) * CELL_H }}
                      />
                    ))}

                    {items.map((p) => {
                      const geo = barGeometry(p, from, totalSlots);
                      if (!geo) return null;
                      const color = cargoColor(p.cargo);
                      const v = lengthGeometry(p, CELL_H);
                      // длину не задали — полоса идёт до конца причала; за блок не выходим
                      const height = Math.max(10, Math.min(v.height, cells * CELL_H - v.top) - 2);
                      const dragging = drag?.id === p.id;
                      const canDrag = wysiwyg && canEdit && !busy;
                      // пока тащим, полоса едет за курсором с тем же шагом, с каким
                      // потом сохранится: слот по горизонтали, 5 м по вертикали
                      const shiftX = dragging ? Math.round(drag.dx / slotW) * slotW : 0;
                      // вертикаль считаем от блока-цели: если это другой причал,
                      // полоса доезжает до него и встаёт на его отметку
                      const shiftY = dragging
                        ? (drag.blocks.find((b) => b.id === drag.berthId)?.top ?? drag.ownTop) -
                          drag.ownTop +
                          (drag.offsetM / M_PER_CELL) * CELL_H -
                          drag.topInBlock
                        : 0;
                      return (
                        <button
                          key={p.id}
                          className={`absolute rounded-md text-left text-[11px] px-1.5 text-white shadow-sm hover:brightness-110 focus:outline-none focus:ring-2 focus:ring-brand-sky ${canDrag ? "cursor-move touch-none" : ""} ${dragging ? "z-40 ring-2 ring-brand-sky" : ""}`}
                          style={{
                            left: geo.left * slotW,
                            width: geo.width * slotW - 3,
                            top: v.top + 1,
                            height,
                            transform: shiftX || shiftY ? `translate(${shiftX}px, ${shiftY}px)` : undefined,
                            background: `linear-gradient(180deg, ${color}f0, ${color}cc)`,
                            borderLeft: geo.clippedStart ? "3px dotted rgba(255,255,255,.85)" : "3px solid rgba(0,0,0,.18)",
                          }}
                          title={`${barText(p)} · ${lengthLabel(p)}${p.cargo ? ` · ${p.cargo}` : ""}\n${parseLocal(p.starts_at).toLocaleString("ru-RU")} — ${parseLocal(p.ends_at).toLocaleString("ru-RU")}`}
                          onClick={() => {
                            if (suppressClick.current) return;
                            setDetail(p);
                          }}
                          onPointerDown={canDrag ? (e) => startDrag(e, p) : undefined}
                          onPointerMove={canDrag ? (e) => moveDrag(e, p) : undefined}
                          onPointerUp={canDrag ? () => endDrag(p) : undefined}
                          onPointerCancel={() => setDrag(null)}
                          onKeyDown={canDrag ? (e) => nudge(p, e) : undefined}
                          data-vp-bar={p.id}
                          data-berth={berth.code}
                          data-cargo={(p.cargo || "").toUpperCase()}
                          data-length-m={p.length_m ?? ""}
                          data-offset-m={p.offset_m ?? 0}
                          data-dragging={dragging ? "1" : "0"}
                        >
                          <span className="block truncate">{p.vessel}</span>
                          {height >= 24 ? (
                            <span className="block truncate text-[10px] opacity-85">
                              {[p.volume, p.length_m ? `${p.length_m} м` : null].filter(Boolean).join(" · ")}
                            </span>
                          ) : null}
                          {geo.clippedEnd ? <span className="block text-[10px]">…</span> : null}
                        </button>
                      );
                    })}
                  </div>
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
              <dd>
                {berths.find((b) => b.id === detail.berth_id)?.code ?? "—"} · {lengthLabel(detail)}
              </dd>
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
              <dt className="panel-title">Длина судна</dt>
              <dd className="num" data-detail-length>
                {detail.length_m ? `${detail.length_m} м` : "не указана — занимает причал до конца"}
              </dd>
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
                onChange={(e) => setForm({ ...form, berth_id: Number(e.target.value), offset_m: "0" })}
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
            <Field
              label="Длина судна, м"
              hint={formBerth?.length_m ? `причал ${formBerth.code} — ${formBerth.length_m} м` : "метраж причала не задан"}
            >
              <input
                className="field num"
                type="number"
                min={1}
                max={500}
                step={5}
                value={form.length_m}
                onChange={(e) => setForm({ ...form, length_m: e.target.value })}
                placeholder="60"
                data-field="length-m"
              />
            </Field>
            <Field label="Место у причала, м" hint="отметка от начала причала, кратно 10 м">
              <div className="flex gap-2">
                <input
                  className="field num"
                  type="number"
                  min={0}
                  step={10}
                  value={form.offset_m}
                  onChange={(e) => setForm({ ...form, offset_m: e.target.value })}
                  data-field="offset-m"
                />
                <button className="btn-ghost text-xs whitespace-nowrap" onClick={findFree} data-admin-action="find-free">
                  Найти свободное
                </button>
              </div>
              {freeHint ? (
                <span className="mt-1 block text-[11px] text-ok" data-free-hint>{freeHint}</span>
              ) : null}
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

          <Notice text="Два судна мешают друг другу, только если пересекаются и по времени, и по метрам: 100 м причала — это 10 ячеек по 10 м. Кабинет не даст поставить судно так, чтобы оно вылезло за край причала или наехало на уже стоящее, и скажет, какое именно и где именно." />
        </Modal>
      )}
    </AppShell>
  );
}
