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
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  api,
  type Berth,
  type Placement,
  type PlacementInput,
  type VesselPlan,
} from "../api";
import { useAuth } from "../auth";
import { AppShell } from "../components/AppShell";
import { VesselsArrows, type ArrowView } from "../components/VesselsArrows";
import { Field, FormError, Modal, Notice, errText } from "../components/admin/ui";
import { dmy } from "../format";
import { resolveVesselRights, roleListLabel, usePublicSettings } from "../settings";
import {
  addDays, addSlots, arrowCaption, barColor, barGeometry, barText, cargoColor, chainLinks, chainSteps,
  daysInRange, displayed, isWeekend, isoLocal, lengthGeometry, lengthLabel, M_PER_CELL, PALETTE,
  parseLocal, periodLabel, rulerMarks, slotLabel, slotsCount, SLOT_HOURS, textColorOn, ymd,
} from "../vessels";
import { arrowGeometry, arrowLabel, type ArrowAnchor } from "../vesselArrows";

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
/** Высота одной ячейки вертикальной шкалы = 10 метров длины причала; режим крутится кнопкой. */
const CELL_STEPS = [12, 9, 6];
const CELL_H_DEFAULT = 12;

/** Код виртуального причала «Ожидание»: карман для судов вне причалов (заводит бэкенд). */
const PARK_CODE = "ОЖИДАНИЕ";

// Геометрия стрелки (путь со скруглёнными углами, наконечник, кружок старта и
// место подписи) лежит в src/vesselArrows.ts, рисует их src/components/
// VesselsArrows.tsx. Здесь — только то, что зависит от состояния страницы:
// якоря по полосам, номера цепочек и подсветка.

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
  color: string;
  at_other_terminal: boolean;
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
      at_other_terminal: false,
      color: "",
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
    at_other_terminal: p.at_other_terminal ?? false,
    color: p.color ?? "",
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
  // «⤓ PDF» качает файл с бэкенда, поэтому кнопка обязана показывать, что файл уже летит
  const [pdfBusy, setPdfBusy] = useState(false);
  const [detail, setDetail] = useState<Placement | null>(null);
  // Наведение на полосу или чип: стрелки чужих цепочек уходят на фон. На
  // мобильном hover нет — там ту же роль играет открытая панель деталей.
  const [hoverId, setHoverId] = useState<number | null>(null);
  const [freeHint, setFreeHint] = useState("");
  // WYSIWYG: карандаш превращает полосы в ручки — судно двигают мышью
  const [wysiwyg, setWysiwyg] = useState(false);
  // перетаскивание считает не «дельту от старта», а где курсор сейчас: блок
  // причала под курсором = целевой причал, поэтому судно переезжает с РП-2 на
  // РП-3 одним движением
  const [drag, setDrag] = useState<{
    id: number; startX: number; dx: number; grabY: number; ownTop: number;
    blocks: { id: number; left: number; top: number; height: number }[];
    berthId: number; offsetM: number; topInBlock: number;
  } | null>(null);
  // перешвартовка по правой кнопке: меню у курсора + призрак с целью
  const [menu, setMenu] = useState<{ x: number; y: number; p: Placement } | null>(null);
  const [ghost, setGhost] = useState<{
    p: Placement; x: number; y: number; cx: number; cy: number;
    berthId: number; offsetM: number; slot: number;
  } | null>(null);
  // после перетаскивания клик по полосе не должен открывать карточку
  const suppressClick = useRef(false);
  // плотность шкалы: чем меньше ячейка, тем больше причалов влезает на экран,
  // а значит и в PDF «что видно» попадают они все — стрелки не обрезаются краем
  const [cellH, setCellH] = useState(() => {
    const v = Number(window.localStorage.getItem("vp.cellH"));
    return CELL_STEPS.includes(v) ? v : CELL_H_DEFAULT;
  });
  const cycleCellH = () =>
    setCellH((h) => {
      const next = CELL_STEPS[(CELL_STEPS.indexOf(h) + 1) % CELL_STEPS.length] ?? CELL_H_DEFAULT;
      window.localStorage.setItem("vp.cellH", String(next));
      return next;
    });

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

  // График сдвигается каждый день: перестановка «состоится» на границе слота,
  // а страница у диспетчера открыта сутками. Обновляем план сами: раз в минуту,
  // пока вкладка видна, и сразу при возвращении на вкладку. Во время перетаскивания,
  // призрака или сохранения не лезем — чтобы не вырвать полосу из-под курсора.
  const interactive = useRef(false);
  useEffect(() => {
    interactive.current = !!drag || !!ghost || busy;
  }, [drag, ghost, busy]);
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "visible" && !interactive.current) void load();
    };
    const timer = window.setInterval(refresh, 60_000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
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
  // карман «Ожидание»: виртуальный причал от бэкенда, в сортировке последний
  const parkId = berths.find((b) => b.code === PARK_CODE)?.id;

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

  /* ---------------------------- стрелки оверлей ---------------------------- */

  /** Верх шкалы каждого причала (в координатах холста): от них считают якоря стрелок. */
  const scaleTops = useRef<Record<number, { top: number; height: number }>>({});
  // карман живёт вне шкалы, его чипы меряем отдельным снапшотом
  const parkRects = useRef<Record<number, { x: number; y: number; w: number; h: number }>>({});
  const [layoutTick, setLayoutTick] = useState(0);
  // SVG — replaced-элемент: inset-0 его не растягивает, поэтому размер холста
  // измерен явно, иначе стрелки обрезались бы штатными 300×150
  const [canvasSize, setCanvasSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const print = gridRef.current?.querySelector<HTMLElement>("[data-vp-print]");
    if (!print) return;
    const pr = print.getBoundingClientRect();
    const map: Record<number, { top: number; height: number }> = {};
    for (const el of print.querySelectorAll<HTMLElement>("[data-berth-scale]")) {
      const r = el.getBoundingClientRect();
      map[Number(el.dataset.berthScale)] = { top: r.top - pr.top, height: r.height };
    }
    scaleTops.current = map;
    // карман: якоря стрелок — реальные прямоугольники чипов «Ожидания»
    const rects: Record<number, { x: number; y: number; w: number; h: number }> = {};
    for (const el of print.querySelectorAll<HTMLElement>("[data-vp-bar]")) {
      if (el.closest("[data-berth-block]")?.getAttribute("data-berth-block") !== PARK_CODE) continue;
      const r = el.getBoundingClientRect();
      rects[Number(el.dataset.vpBar)] = { x: r.left - pr.left, y: r.top - pr.top, w: r.width, h: r.height };
    }
    parkRects.current = rects;
    setCanvasSize({ w: pr.width, h: pr.height });
    setLayoutTick((t) => t + 1);
  }, [plan, slotW, availW, cellH]);

  /** Якорь стрелки: те же barGeometry/lengthGeometry, что рисуют саму полосу. */
  const anchorOf = (p: Placement): ArrowAnchor | null => {
    // карман: чипы лежат не на шкале — якорем служит фактический прямоугольник чипа
    if (parkId != null && p.berth_id === parkId) {
      const r = parkRects.current[p.id];
      if (r) return { left: r.x, right: r.x + r.w, y: r.y + r.h / 2 };
    }
    const d = displayed(p);
    if (!d) return null;
    const geo = barGeometry(d, from, totalSlots);
    const st = scaleTops.current[p.berth_id];
    if (!geo || !st) return null;
    const v = lengthGeometry(p, cellH);
    return {
      left: LEFT_W + geo.left * slotW,
      right: LEFT_W + (geo.left + geo.width) * slotW,
      y: st.top + v.top + Math.min(v.height, st.height) / 2,
    };
  };

  /** Полоса под drag'ом: стрелка едет за целью, а не за старым местом. */
  const previewOf = (p: Placement): Placement => {
    if (!drag || drag.id !== p.id) return p;
    const slots = Math.round(drag.dx / slotW);
    return {
      ...p,
      berth_id: drag.berthId,
      offset_m: drag.offsetM,
      starts_at: isoLocal(addSlots(parseLocal(p.starts_at), slots)),
      ends_at: isoLocal(addSlots(parseLocal(p.ends_at), slots)),
    };
  };

  /**
   * Цепочки перешвартовок: id любой постановки → множество id всех строк её
   * цепочки. Нужно для подсветки: наведение на одну полосу обязано оставить
   * яркими ВСЕ стрелки этого судна, иначе цепочка из четырёх постановок
   * распадается на четыре несвязных штриха и снова не видно, какая стрелка
   * куда ведёт.
   */
  const chains = useMemo(() => {
    const rows = plan?.placements ?? [];
    const ids = new Set(rows.map((p) => p.id));
    const kids = new Map<number, number[]>();
    const roots: number[] = [];
    for (const p of rows) {
      // «висячая» ссылка (оригинал за окном периода или удалён) — это начало цепочки
      const src = p.moved_from_id != null && ids.has(p.moved_from_id) ? p.moved_from_id : null;
      if (src === null) roots.push(p.id);
      else kids.set(src, [...(kids.get(src) ?? []), p.id]);
    }
    const byNode = new Map<number, Set<number>>();
    for (const root of roots) {
      const group = new Set<number>([root]);
      const stack = [root];
      while (stack.length) {
        const cur = stack.pop() as number;
        for (const k of kids.get(cur) ?? [])
          if (!group.has(k)) {
            group.add(k);
            stack.push(k);
          }
      }
      for (const id of group) byNode.set(id, group);
    }
    // строка, застрявшая в цикле по moved_from_id, — цепочка из одной полосы
    for (const p of rows) if (!byNode.has(p.id)) byNode.set(p.id, new Set([p.id]));
    return byNode;
  }, [plan]);

  /** Что в фокусе: полоса под курсором, а без hover — открытая карточка. */
  const focusId = hoverId ?? detail?.id ?? null;
  const focusChain = focusId === null ? null : chains.get(focusId) ?? null;

  /** Шифр причала в подпись стрелки; «Ожидание» — не причал, а карман, поэтому словом. */
  const berthCode = (id: number) => {
    const b = berths.find((x) => x.id === id);
    return !b ? "?" : b.code === PARK_CODE ? "Ожидание" : b.code;
  };

  const arrows = useMemo(() => {
    const rows = plan?.placements ?? [];
    const byId = new Map(rows.map((p) => [p.id, p]));
    const steps = chainSteps(rows); // те же номера шагов, что печатает PDF
    const out: ArrowView[] = [];
    for (const link of chainLinks(rows)) {
      const src = byId.get(link.srcId);
      const copy = byId.get(link.dstId);
      if (!src || !copy) continue; // оригинал удалили — стрелка гаснет вместе с ним
      const a = anchorOf(src);
      const b = anchorOf(previewOf(copy));
      if (!a || !b) continue;
      const geom = arrowGeometry(a, b);
      const caption = arrowCaption(steps, src.id, copy.id, berthCode(copy.berth_id), copy.starts_at);
      out.push({
        id: copy.id,
        srcId: src.id,
        ...geom,
        color: barColor(copy),
        caption,
        label: arrowLabel(caption, geom, canvasSize.w),
        tone: !focusChain
          ? "base"
          : focusChain.has(copy.id) || focusChain.has(src.id)
            ? "focus"
            : "dim",
      });
    }
    if (ghost) {
      const a = anchorOf(ghost.p);
      // призрак под курсором — прямая линия: она живёт столько, сколько тянут
      // мышью, и ломаная со скруглениями только мешала бы целиться в ячейку
      if (a)
        out.push({
          id: -1,
          d: `M ${a.right} ${a.y} L ${ghost.cx} ${ghost.cy}`,
          startX: a.right,
          startY: a.y,
          tipX: ghost.cx,
          tipY: ghost.cy,
          rightward: ghost.cx >= a.right,
          color: barColor(ghost.p),
          caption: "",
          label: { x: 0, y: 0, anchor: "start" },
          draft: true,
        });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan, slotW, layoutTick, drag, ghost, from, totalSlots, cellH, focusChain, canvasSize.w]);

  /**
   * Обводка «откуда/куда»: когда цепочка подсвечена, полосы оригинала и цели
   * получают рамку цветом своей стрелки. Без неё яркая линия висит над сеткой,
   * и непонятно, от какой именно полосы она стартует: у плотного причала полосы
   * стоят в полутора миллиметрах друг от друга.
   */
  const ringOf = useMemo(() => {
    const map = new Map<number, string>();
    for (const ar of arrows) {
      if (ar.tone !== "focus" || ar.srcId === undefined) continue;
      map.set(ar.srcId, ar.color);
      map.set(ar.id, ar.color);
    }
    return map;
  }, [arrows]);

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
      at_other_terminal: form.at_other_terminal,
      color: form.color || null,
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
    if (e.button !== 0) return;  // правая кнопка — контекстное меню, не drag
    const bar = e.currentTarget as HTMLElement;
    bar.setPointerCapture?.(e.pointerId);
    const barTop = bar.getBoundingClientRect().top;
    // мериим поля шкалы, а не блоки: у блока сверху строка с названием причала,
    // и отсчёт метров от неё съехал бы на полтора десятка метров
    const blocks = [...document.querySelectorAll<HTMLElement>("[data-berth-scale]")]
      .map((el) => {
        const r = el.getBoundingClientRect();
        return { id: Number(el.dataset.berthScale), left: r.left, top: r.top, height: r.height };
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
      topInBlock: lengthGeometry(p, cellH).top,
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
    const raw = ((e.clientY - block.top - drag.grabY) / cellH) * M_PER_CELL;
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
    // перетаскивание из «Ожидания» к причалу — копия со стрелкой, оригинал
    // ждёт в кармане, пока судно физически не придёт: как в Excel у диспетчера.
    // Дату копия берёт не «свою» (в кармане она формальность), а слот под курсором
    if (parkId && p.berth_id === parkId && berthId !== parkId) {
      const scaleLeft = drag.blocks[0]?.left ?? 0;
      const absoluteSlot = Math.max(0, Math.floor((drag.startX + dx - scaleLeft) / slotW));
      await reberthingCopy(p, { slots: 0, berthId, offsetM, absoluteSlot });
      return;
    }
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

  /* ---------------------------- перешвартовка ---------------------------- */

  /** Абсолютный индекс слота (от начала периода), с которого начинается постановка. */
  const slotIndexOf = (p: Placement): number => {
    const s = parseLocal(p.starts_at);
    const i = days.indexOf(ymd(s));
    if (i < 0) return 0;
    return i * SLOT_HOURS.length + Math.max(0, SLOT_HOURS.indexOf(s.getHours()));
  };

  /** Абсолютный слот → Date: границы копии считают именно так. */
  const slotToDate = (i: number): Date => {
    const day = days[Math.min(Math.max(Math.floor(i / SLOT_HOURS.length), 0), days.length - 1)] ?? from;
    return new Date(`${day}T${String(SLOT_HOURS[i % SLOT_HOURS.length] ?? 0).padStart(2, "0")}:00:00`);
  };

  /** Копия постановки со стрелкой: оригинал остаётся на месте, копия — пунктиром. */
  const reberthingCopy = async (
    p: Placement,
    dest: { slots: number; berthId: number; offsetM: number; absoluteSlot?: number },
  ) => {
    const berth = berths.find((b) => b.id === dest.berthId);
    if (!berth) return;
    let start: Date;
    let end: Date;
    if (dest.absoluteSlot !== undefined) {
      start = slotToDate(dest.absoluteSlot);
      end = slotToDate(Math.min(dest.absoluteSlot + slotsCount(p) - 1, totalSlots - 1));
    } else {
      start = addSlots(parseLocal(p.starts_at), dest.slots);
      end = addSlots(parseLocal(p.ends_at), dest.slots);
    }
    if (start < new Date(`${from}T00:00:00`) || end > new Date(`${to}T23:59:59`)) {
      setError("Нельзя вынести перешвартовку за границы выбранного периода");
      return;
    }
    const offset =
      berth.length_m && p.length_m
        ? Math.min(Math.max(dest.offsetM, 0), Math.max(berth.length_m - p.length_m, 0))
        : Math.max(dest.offsetM, 0);
    try {
      await api.createPlacement({
        berth_id: dest.berthId,
        lane: 1,
        length_m: p.length_m,
        offset_m: offset,
        vessel: p.vessel,
        volume: p.volume,
        cargo: p.cargo,
        note: p.note,
        starts_at: isoLocal(start),
        ends_at: isoLocal(end),
        moved_from_id: p.id,
      });
      setError("");
      setSaved(`«${p.vessel}» → ${berth.code}: копия со стрелкой, оригинал на месте`);
      window.setTimeout(() => setSaved(""), 6000);
      await load();
    } catch (e) {
      await load();
      setError(errText(e, "Не удалось перешвартовать"));
    }
  };

  /** Правая кнопка → «Временное перемещение»: призрак за курсором, по клику — копия в точку. */
  const startReberthing = (p: Placement, at: { clientX: number; clientY: number }) => {
    const scales = [...document.querySelectorAll<HTMLElement>("[data-berth-scale]")].map((el) => {
      const r = el.getBoundingClientRect();
      return { id: Number(el.dataset.berthScale), left: r.left, top: r.top, height: r.height };
    });
    const print = document.querySelector<HTMLElement>("[data-vp-print]");
    if (!scales.length || !print) return;
    const cur = {
      p, x: at.clientX, y: at.clientY, cx: 0, cy: 0,
      berthId: p.berth_id, offsetM: p.offset_m ?? 0, slot: slotIndexOf(p),
    };
    const paint = (e: { clientX: number; clientY: number }) => {
      const pr = print.getBoundingClientRect();
      cur.x = e.clientX;
      cur.y = e.clientY;
      cur.cx = e.clientX - pr.left;
      cur.cy = e.clientY - pr.top;
      setGhost({ ...cur });
    };
    paint(at);
    const move = (e: PointerEvent) => {
      const block = pickBlock(scales, e.clientY);
      if (block) {
        const berth = berths.find((b) => b.id === block.id);
        const room = berth?.length_m && p.length_m ? Math.max(berth.length_m - p.length_m, 0) : 0;
        cur.berthId = block.id;
        const raw = ((e.clientY - block.top - 8) / cellH) * M_PER_CELL;
        cur.offsetM = Math.min(Math.max(Math.round(raw / 5) * 5, 0), room);
      }
      const s = Math.floor((e.clientX - scales[0].left) / slotW);
      cur.slot = Math.min(Math.max(s, 0), Math.max(0, totalSlots - slotsCount(p)));
      paint(e);
    };
    const detach = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("keydown", esc);
    };
    const up = () => {
      detach();
      const target = { ...cur };
      setGhost(null);
      void reberthingCopy(p, { slots: 0, berthId: target.berthId, offsetM: target.offsetM, absoluteSlot: target.slot });
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      detach();
      setGhost(null);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("keydown", esc);
  };

  /** Красная рамка: судно сейчас на другом терминале; без рамки — в проливе. */
  const toggleTerminal = async (p: Placement) => {
    try {
      await api.updatePlacement(p.id, { at_other_terminal: !p.at_other_terminal });
      setDetail((d) => (d && d.id === p.id ? { ...d, at_other_terminal: !p.at_other_terminal } : d));
      await load();
    } catch (e) {
      setError(errText(e, "Не удалось обновить отметку"));
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

  /* ---------------------------------- PDF ---------------------------------- */

  /**
   * «⤓ PDF» забирает у бэкенда готовый файл и отдаёт его браузеру на сохранение.
   * Печать браузера для этого не используется: диспетчеру нужен файл с конкретным
   * куском плана, а не страница кабинета в диалоге печати.
   *
   * Какую часть плана рисовать, решаем здесь: только фронтенд знает, что сейчас
   * в кадре сетки. Берём реальное прокрученное положение — сутки целыми (половину
   * суток в лист класть нельзя) и причалы, хоть частично видимые.
   */
  const exportPdf = async () => {
    const el = gridRef.current;
    if (!el || pdfBusy) return;
    const dayPx = slotW * SLOT_HOURS.length;
    const d0 = Math.max(0, Math.floor((el.scrollLeft - LEFT_W) / dayPx));
    const d1 = Math.min(days.length - 1, Math.ceil((el.scrollLeft + el.clientWidth - LEFT_W) / dayPx) - 1);
    const box = el.getBoundingClientRect();
    const visible = [...el.querySelectorAll<HTMLElement>("[data-berth-block]")]
      .filter((node) => {
        const r = node.getBoundingClientRect();
        return r.bottom > box.top + 1 && r.top < box.bottom - 1;
      })
      .map((node) => Number(node.dataset.berthId))
      .filter(Number.isFinite);
    const berthIds = visible.length ? visible : blocks.map((b) => b.berth.id);
    const dEnd = Math.max(d0, d1);

    setPdfBusy(true);
    try {
      const blob = await api.planPdf(from, to, d0, dEnd, berthIds);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      // имя файла — по окну, а не по периоду: файл описывает кадр, иначе два
      // разных листа одного месяца назывались бы одинаково
      link.download = `план_постановки_${days[d0] ?? from}_${days[dEnd] ?? to}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      // объект живёт, пока браузер не дописал файл на диск
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
      setError("");
      setSaved(`PDF готов: ${dEnd - d0 + 1} сут · ${berthIds.length} причалов`);
      window.setTimeout(() => setSaved(""), 6000);
    } catch (e) {
      setError(errText(e, "PDF не получился"));
    } finally {
      setPdfBusy(false);
    }
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
            disabled={pdfBusy}
            title="Скачать PDF того, что видно в окне плана: ровно эти сутки и эти причалы, крупно. Прокрутите план и нажмите снова, чтобы забрать следующий кусок"
            data-admin-action="export-pdf"
          >
            {pdfBusy ? "PDF…" : "⤓ PDF"}
          </button>
          <button
            className="btn btn-mini btn-ghost"
            onClick={cycleCellH}
            title="Высота ячейки шкалы (10 м): меньше ячейка — больше причалов влезает на экран; значение сохраняется"
            data-cell-toggle
          >
            Ячейка {cellH} px
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
            полосу под фокусом. Правая кнопка по судну — «Временное перемещение»: полупрозрачная
            копия прицепится к курсору, встанет в нужную ячейку, и от оригинала к ней сама
            протянётся стрелка перешвартовки.
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
                className={`border-b border-ink-700 ${berth.code === PARK_CODE ? "bg-ink-800/20" : ""} ${drag && drag.berthId === berth.id && drag.berthId !== items[0]?.berth_id ? "ring-2 ring-inset ring-brand-sky" : ""}`}
                data-berth-block={berth.code}
                data-berth-id={berth.id}
                data-drop-target={drag && drag.berthId === berth.id ? "1" : "0"}
              >
                <div className="flex bg-ink-900/5">
                  <div
                    className="sticky left-0 z-10 bg-white/95 border-r border-ink-700 px-2 py-0.5 text-xs"
                    style={{ width: LEFT_W }}
                  >
                    <span className="font-medium">{berth.code === PARK_CODE ? "Ожидание" : berth.code}</span>
                    {berth.code === PARK_CODE ? (
                      <span className="text-[10px] text-ink-400"> · суда вне причалов</span>
                    ) : berth.depth ? (
                      <span className="text-[10px] text-ink-400">
                        {" "}· осадка {String(berth.depth).replace(".", ",")} м
                      </span>
                    ) : null}
                  </div>
                  <div className="text-[10px] text-ink-400 px-2 py-0.5" style={{ width: totalSlots * slotW }}>
                    {berth.code === PARK_CODE
                      ? "ждут черёды, груза · дата не важна · тяни чип к причалу — копия встанет на дату под курсором"
                      : berth.length_m
                        ? `${berth.length_m} м длины · ${cells} ячеек по ${10} м`
                        : "метраж причала не задан · показываем 100 м"}
                  </div>
                </div>

                {berth.code === PARK_CODE ? (
                  /* карман: шкалы времени нет — дата в «Ожидании» не важна.
                     Суда складываются чипами (можно «друг на друга» рядами),
                     а стрелка перешвартовки стартует прямо от чипа */
                  <div className="flex items-stretch">
                    <div
                      className="sticky left-0 z-10 bg-white/95 border-r border-ink-700 text-[9px] uppercase tracking-wider text-ink-400 px-2 py-1.5"
                      style={{ width: LEFT_W }}
                    >
                      склад · без привязки к дате
                    </div>
                    <div className="relative" style={{ width: totalSlots * slotW, minHeight: 34 }} data-berth-scale={berth.id}>
                      <div className="flex flex-wrap gap-1.5 p-1" style={{ width: Math.max(availW - LEFT_W - 10, 320) }}>
                        {items.map((p) => {
                          const color = barColor(p);
                          const dragging = drag?.id === p.id;
                          const canDrag = wysiwyg && canEdit && !busy;
                          const ring = ringOf.get(p.id); // обводка, если это цепочка под курсором
                          return (
                            <button
                              key={p.id}
                              className={`rounded-md px-2 py-1 text-left text-[11px] text-white shadow-sm hover:brightness-110 focus:outline-none focus:ring-2 focus:ring-brand-sky ${canDrag ? "cursor-move touch-none" : ""} ${dragging ? "z-40 ring-2 ring-brand-sky" : ""}`}
                              style={{
                                width: 148,
                                background: `linear-gradient(180deg, ${color}f0, ${color}cc)`,
                                color: textColorOn(color),
                                ...(p.moved_from_id && !p.committed
                                  ? { outline: "2px dashed rgba(255,255,255,.95)", outlineOffset: -2, opacity: 0.75 }
                                  : {}),
                                // красная рамка «на другом терминале» + обводка своей цепочки
                                boxShadow: [
                                  p.at_other_terminal ? "inset 0 0 0 2px #b3312a" : "",
                                  ring ? `0 0 0 2px ${ring}` : "",
                                ].filter(Boolean).join(", ") || undefined,
                              }}
                              title={`${barText(p)} · ждёт${p.cargo ? ` · ${p.cargo}` : ""}${p.moved_from_id && !p.committed ? "\nперешвартован: копия, стрелка ведёт от оригинала" : ""}`}
                              onClick={() => {
                                if (suppressClick.current) return;
                                setDetail(p);
                              }}
                              // наведение — единственный способ показать «вот эти стрелки мои»;
                              // на мобильном его заменяет открытая панель деталей
                              onMouseEnter={() => setHoverId(p.id)}
                              onMouseLeave={() => setHoverId((id) => (id === p.id ? null : id))}
                              onContextMenu={
                                canDrag
                                  ? (e) => {
                                      e.preventDefault();
                                      setMenu({ x: e.clientX, y: e.clientY, p });
                                    }
                                  : undefined
                              }
                              onPointerDown={canDrag ? (e) => startDrag(e, p) : undefined}
                              onPointerMove={canDrag ? (e) => moveDrag(e, p) : undefined}
                              onPointerUp={canDrag ? () => endDrag(p) : undefined}
                              onPointerCancel={() => setDrag(null)}
                              data-vp-bar={p.id}
                              data-berth={berth.code}
                              data-cargo={(p.cargo || "").toUpperCase()}
                              data-dragging={dragging ? "1" : "0"}
                              data-moved-from={p.moved_from_id ?? ""}
                              data-chain-focus={ring ? "1" : "0"}
                            >
                              <span className="block truncate">{p.vessel}</span>
                              <span className="block truncate text-[10px] opacity-85">
                                {[p.volume, p.cargo].filter(Boolean).join(" · ") || "ждёт"}
                              </span>
                            </button>
                          );
                        })}
                        {items.length === 0 ? (
                          <span className="px-1 py-2 text-[10px] text-ink-400">
                            пусто: перетащи судно сюда, чтобы снять его с линии
                          </span>
                        ) : null}
                      </div>
                    </div>
                  </div>
                ) : (
                <div className="flex">
                  {/* линейка длины: подпись каждой ячейки в метрах */}
                  <div className="sticky left-0 z-10 bg-white/95 border-r border-ink-700" style={{ width: LEFT_W }}>
                    {rulerMarks(cells).map((m) => (
                      <div
                        key={m}
                        className="num text-right pr-2 text-[9px] text-ink-400 border-t border-ink-700/50"
                        style={{ height: cellH, lineHeight: `${cellH}px` }}
                        data-ruler={m}
                      >
                        {m % (cellH < 14 ? 20 : 10) === 0 ? m : ""}
                      </div>
                    ))}
                  </div>

                  <div className="relative" style={{ width: totalSlots * slotW, height: cells * cellH }} data-berth-scale={berth.id}>
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
                        style={{ top: (i + 1) * cellH }}
                      />
                    ))}

                    {items.map((p) => {
                      const d = displayed(p);
                      const geo = d ? barGeometry(d, from, totalSlots) : null;
                      if (!geo) return null;
                      const color = barColor(p);
                      const v = lengthGeometry(p, cellH);
                      // длину не задали — полоса идёт до конца причала; за блок не выходим
                      const height = Math.max(10, Math.min(v.height, cells * cellH - v.top) - 2);
                      const dragging = drag?.id === p.id;
                      const canDrag = wysiwyg && canEdit && !busy;
                      const ring = ringOf.get(p.id); // обводка, если это цепочка под курсором
                      // пока тащим, полоса едет за курсором с тем же шагом, с каким
                      // потом сохранится: слот по горизонтали, 5 м по вертикали
                      const shiftX = dragging ? Math.round(drag.dx / slotW) * slotW : 0;
                      // вертикаль считаем от блока-цели: если это другой причал,
                      // полоса доезжает до него и встаёт на его отметку
                      const shiftY = dragging
                        ? (drag.blocks.find((b) => b.id === drag.berthId)?.top ?? drag.ownTop) -
                          drag.ownTop +
                          (drag.offsetM / M_PER_CELL) * cellH -
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
                            color: textColorOn(color),
                            borderLeft: geo.clippedStart ? "3px dotted rgba(255,255,255,.85)" : "3px solid rgba(0,0,0,.18)",
                            // копия после перешвартовки: пунктирная рамка и полупрозрачность
                            ...(p.moved_from_id && !p.committed
                              ? { outline: "2px dashed rgba(255,255,255,.95)", outlineOffset: -2, opacity: 0.75 }
                              : {}),
                            // красная рамка «на другом терминале» + обводка своей цепочки
                            // (box-shadow, чтобы не спорить с пунктиром перешвартовки)
                            boxShadow: [
                              p.at_other_terminal ? "inset 0 0 0 2px #b3312a" : "",
                              ring ? `0 0 0 2px ${ring}` : "",
                            ].filter(Boolean).join(", ") || undefined,
                          }}
                          title={`${barText(p)} · ${lengthLabel(p)}${p.cargo ? ` · ${p.cargo}` : ""}\n${parseLocal(p.starts_at).toLocaleString("ru-RU")} — ${parseLocal(p.ends_at).toLocaleString("ru-RU")}${p.moved_from_id && !p.committed ? "\nперешвартован: копия, стрелка ведёт от оригинала" : ""}`}
                          onClick={() => {
                            if (suppressClick.current) return;
                            setDetail(p);
                          }}
                          onMouseEnter={() => setHoverId(p.id)}
                          onMouseLeave={() => setHoverId((id) => (id === p.id ? null : id))}
                          onContextMenu={
                            canDrag
                              ? (e) => {
                                  e.preventDefault();
                                  setMenu({ x: e.clientX, y: e.clientY, p });
                                }
                              : undefined
                          }
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
                          data-moved-from={p.moved_from_id ?? ""}
                          data-chain-focus={ring ? "1" : "0"}
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
                )}
              </div>
            ))}

            {/* Стрелки перешвартовок: один SVG поверх холста в координатах полос.
                Якоря считаются теми же barGeometry/lengthGeometry, что и сами
                полосы, поэтому стрелки не разъезжаются ни при каком скролле —
                оверлей скроллится внутри того же контейнера. pointer-events-none,
                чтобы не съедать drag. */}
            <VesselsArrows arrows={arrows} width={canvasSize.w} height={canvasSize.h} />
          </div>
        </div>

      {/* «Судно у мышки» и контекстное меню — порталом в body: у .panel стоит
          backdrop-filter, он создаёт containing block, и fixed-потомок лепился
          бы к панели, а не к окну — призрак уезжал от курсора */}
      {ghost ? createPortal(
        <div
          className="fixed z-50 pointer-events-none select-none rounded-md px-2 py-1 text-[11px] text-white shadow-lg"
          style={{
            left: ghost.x + 12,
            top: ghost.y + 12,
            background: barColor(ghost.p),
            opacity: 0.75,
            border: "2px dashed rgba(255,255,255,.95)",
          }}
          data-vp-ghost={ghost.p.id}
        >
          {ghost.p.vessel} · {berths.find((b) => b.id === ghost.berthId)?.code ?? "?"}
        </div>,
        document.body,
      ) : null}

      {/* Контекстное меню по правой кнопке — то, о чём просил диспетчер:
          «по судну правой кнопкой … временное перемещение» */}
      {menu ? createPortal(
        <div className="fixed inset-0 z-50" onClick={() => setMenu(null)}
             onContextMenu={(e) => { e.preventDefault(); setMenu(null); }} data-vp-menu-backdrop>
          <div
            className="absolute min-w-56 rounded-md border border-ink-700 bg-white py-1 text-sm shadow-xl"
            style={{ left: menu.x, top: menu.y }}
            onClick={(e) => e.stopPropagation()}
            data-vp-menu
          >
            <div className="px-3 py-1 text-[11px] uppercase tracking-wide text-ink-400">{menu.p.vessel}</div>
            <button
              className="block w-full px-3 py-1.5 text-left hover:bg-ink-800/5"
              data-reberthing
              onClick={() => {
                const m = menu;
                setMenu(null);
                startReberthing(m.p, { clientX: m.x, clientY: m.y });
              }}
            >
              ⇄ Временное перемещение
              <span className="block text-[10px] text-ink-400">полупрозрачная копия + стрелка от оригинала</span>
            </button>
            <button
              className="block w-full px-3 py-1.5 text-left hover:bg-ink-800/5"
              data-toggle-terminal
              onClick={() => {
                const m = menu;
                setMenu(null);
                void toggleTerminal(m.p);
              }}
            >
              {menu.p.at_other_terminal ? "⚪ Снять красную рамку" : "🔴 На другом терминале"}
              <span className="block text-[10px] text-ink-400">рамка: судно сейчас на чужом терминале, без неё — в проливе</span>
            </button>
          </div>
        </div>,
        document.body,
      ) : null}

        <p className="mt-3 text-[11px] text-ink-400 no-print">
          Пунктирный край — постановка началась раньше выбранного периода. Пунктирная рамка и полупрозрачность —
          запланированная перестановка: стрелка ведёт от оригинала, а когда её дата наступает, судно
          автоматически передвигается по стрелке (копия становится обычной постановкой). Красная рамка — судно
          сейчас на другом терминале (без рамки — в проливе). Клик по полосе — подробности, правка и удаление.
        </p>
        <p className="mt-1 text-[11px] text-ink-400 no-print" data-arrows-legend>
          Стрелка перешвартовки: кружок — откуда судно уходит, наконечник — куда. Подпись у
          наконечника говорит теми же словами, что печатает PDF: «шаг N→M» — номер перестановки
          в цепочке этого судна, дальше — причал цели и дата/время её старта («Ожидание», если
          судно ставят в карман). Наведите курсор на полосу или откройте карточку: стрелки
          остальных судов уйдут на фон, а полосы этой цепочки получат рамку цвета своей стрелки.
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
            {detail.moved_from_id ? (
              <div className="col-span-2">
                <dt className="panel-title">Перешвартовка</dt>
                <dd data-detail-moved>
                  копия постановки «{plan?.placements.find((pp) => pp.id === detail.moved_from_id)?.vessel ?? "удалённой"}» —{" "}
                  {detail.committed
                    ? "перестановка состоялась: дата наступила, судно стоит там, куда вела стрелка"
                    : "стрелка на плане ведёт от оригинала"}
                </dd>
              </div>
            ) : null}
            <div className="col-span-2">
              <dt className="panel-title">Где судно сейчас</dt>
              <dd data-detail-terminal>
                {detail.at_other_terminal ? (
                  <span className="text-crit">на другом терминале — красная рамка на плане</span>
                ) : (
                  "в проливе (идёт к нам)"
                )}
              </dd>
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
            {canEdit && (
              <button className="btn-ghost text-sm" onClick={() => void toggleTerminal(detail)}
                      data-admin-action="toggle-terminal">
                {detail.at_other_terminal ? "⚪ Снять отметку терминала" : "🔴 На другом терминале"}
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
            <Field label="Цвет полосы" hint="по умолчанию — по грузу">
              <div className="flex flex-wrap items-center gap-1.5" data-field="color">
                <button
                  type="button"
                  onClick={() => setForm({ ...form, color: "" })}
                  className={`h-7 rounded border px-2 text-[11px] ${form.color ? "border-ink-700 text-ink-300 hover:bg-ink-800/10" : "border-brand-sky bg-ink-800/30 text-ink-100"}`}
                >
                  авто
                </button>
                {PALETTE.map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => setForm({ ...form, color: c })}
                    className={`h-7 w-7 rounded border ${form.color === c ? "border-brand-sky ring-2 ring-brand-sky/60" : "border-ink-700"}`}
                    style={{ background: c }}
                    title={c}
                  />
                ))}
                <label className="relative h-7 w-7 overflow-hidden rounded border border-ink-700" title="свой цвет">
                  <input
                    type="color"
                    value={/^#[0-9a-fA-F]{6}$/.test(form.color) ? form.color : "#2f9bff"}
                    onChange={(e) => setForm({ ...form, color: e.target.value })}
                    className="absolute -inset-3 h-14 w-14 cursor-pointer"
                    data-field="color-custom"
                  />
                </label>
                {form.color && (
                  <span className="flex items-center gap-1.5 text-[11px] text-ink-300">
                    <span className="h-4 w-4 rounded" style={{ background: form.color }} />
                    {form.color}
                  </span>
                )}
              </div>
            </Field>
            <Field label="Примечание">
              <input className="field" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} data-field="note" placeholder="(КМП-ЮМФ)" />
            </Field>
            <Field label="Где судно сейчас" hint="красная рамка на плане">
              <label className="flex items-center gap-2 text-sm" data-field="at-other-terminal">
                <input
                  type="checkbox"
                  checked={form.at_other_terminal}
                  onChange={(e) => setForm({ ...form, at_other_terminal: e.target.checked })}
                />
                на другом терминале · без галки — в проливе
              </label>
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
