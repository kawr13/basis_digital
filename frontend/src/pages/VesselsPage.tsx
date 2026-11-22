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
  isAbortError,
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
  addDays, addHours, arrowCaption, barColor, barGeometry, barText, cargoColor, chainLinks, chainSteps,
  daysInRange, displayed, foreignSpills, isWeekend, isoLocal, lengthGeometry, lengthLabel, M_PER_CELL,
  HOUR_MS, PARK_CODE, PALETTE,
  parseLocal, periodLabel, pxPerHour, rulerMarks, slotLabel, slotsCount, SLOT_HOURS, spillChoices,
  spillMeters, spillNeighbour, spillSide, textColorOn, timeAtX, ymd,
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
/**
 * Правая часть той же колонки — линейка метров. Она отделена чертой от подписи
 * причала: цифры шкалы и название лежат в одной колонке, и без черты глаз не
 * понимает, где кончается название и начинается шкала (числа висели ровно над
 * текстом «осадка …»). Разделка обязана продолжаться в шапке «причал / метры»,
 * иначе линия обрывается на первом же ряду.
 */
const RULER_W = 46;

/** Запас, в котором перетаскивание прилипает к краю соседа на том же причале.
 *  Два часа — это ~12 px: палец ещё чувствует промах, но «впритык к 22:00» уже
 *  попадается руками, а не по часам на глаз. Магнит только выбирает момент, он
 *  ничего не запрещает и полосу за курсор не утаскивает. */
const SNAP_HOURS = 2;

/** Высота одной ячейки вертикальной шкалы = 10 метров длины причала; режим крутится кнопкой. */
const CELL_STEPS = [12, 9, 6];
const CELL_H_DEFAULT = 12;
/**
 * Линии поля — «миллиметровка» плана: 1 клетка = 6 часов × 10 метров.
 *
 * Рисует их background поля, а не div-и: месяц — это 124 слота на каждый причал,
 * и узлы на каждую клетку стоили бы нескольких тысяч DOM-элементов (см. TASKS.md
 * про длинные задачи на главной). Порядок слоёв важен: первый слой рисуется поверх
 * остальных, поэтому сутки — первыми, а слоты последними (на границе суток их
 * перекрывает суточная линия).
 *
 * Линия ставится в КОНЕЦ периода, а не в начало: иначе у поля появлялась бы
 * граница сверху и слева, удваивающаяся с рамкой блока.
 *
 * Палитра — в index.css (--vp-*), там же объяснено, почему она плотнее листовой.
 */
const gridBackground = (slotW: number, cellH: number) => {
  const step = (color: string, period: number, dir: string) =>
    `repeating-linear-gradient(${dir}, transparent 0 ${period - 1}px, var(${color}) ${
      period - 1
    }px ${period}px)`;
  return [
    step("--vp-grid-day", slotW * SLOT_HOURS.length, "to right"),
    step("--vp-grid-meter", cellH, "to bottom"),
    step("--vp-grid-slot", slotW, "to right"),
  ].join(", ");
};

/** Код виртуального причала «Ожидание»: карман для судов вне причалов (заводит бэкенд). */

// Геометрия стрелки (прямая «из А в Б», наконечник, кружок старта и
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
  /** причал, на который судно выходит корпусом; "" — стоит целиком у своего */
  spill_berth_id: string;
  vessel: string;
  volume: string;
  cargo: string;
  note: string;
  startDate: string;
  /** «ЧЧ:ММ» — любое время, не только начало слота */
  startTime: string;
  endDate: string;
  endTime: string;
  color: string;
  at_other_terminal: boolean;
};

/** Время из `<input type=time>` (`«ЧЧ:ММ»`) → «ЧЧ:ММ:00»; пусто — полночь. */
const hhmmss = (t: string) => `${(t || "00:00").padStart(5, "0").slice(0, 5)}:00`;

/** Дата + время из формы → «2026-05-03T22:00:00», то, что понимает бэкенд. */
const toIso = (d: string, time: string) => `${d}T${hhmmss(time)}`;

/** Date → «ЧЧ:ММ» для `<input type=time>`: минуты постановки терять некуда. */
const hhmm = (d: Date) =>
  `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

/** Срок по-человечески: «2 сут 8 ч», а не «56 ч» и не «4760 минут». */
function hoursBetween(a: Date, b: Date): string {
  const h = Math.max(0, Math.round((b.getTime() - a.getTime()) / HOUR_MS));
  const d = Math.floor(h / 24);
  const r = h % 24;
  if (!d) return `${h} ч`;
  return r ? `${d} сут ${r} ч` : `${d} сут`;
}

/** Быстрые «00 06 12 18» рядом со свободным полем времени: слоты никуда не
 *  делись — это шкала плана, и поставить судно ровно на слот по-прежнему нужно
 *  чаще всего. Но теперь это подсказка, а не единственный выбор. */
function SlotHints({ value, onPick }: { value: string; onPick: (t: string) => void }) {
  return (
    <div className="mt-1 flex items-center gap-1">
      {SLOT_HOURS.map((h) => {
        const t = `${String(h).padStart(2, "0")}:00`;
        return (
          <button
            key={h}
            type="button"
            className={`btn btn-ghost px-1.5 py-0 text-[10px] ${value === t ? "text-brand-sky" : ""}`}
            onClick={() => onPick(t)}
            title={`поставить на слот ${slotLabel(h)}`}
          >
            {t}
          </button>
        );
      })}
    </div>
  );
}

function formFrom(p: Placement | null, berths: Berth[]): Form {
  const today = ymd(new Date());
  if (!p) {
    return {
      berth_id: berths[0]?.id ?? 0,
      length_m: "",
      offset_m: "0",
      spill_berth_id: "",
      vessel: "",
      volume: "",
      cargo: "",
      note: "",
      startDate: today,
      startTime: "00:00",
      endDate: today,
      endTime: "06:00",
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
    spill_berth_id: p.spill_berth_id == null ? "" : String(p.spill_berth_id),
    vessel: p.vessel,
    volume: p.volume ?? "",
    cargo: p.cargo ?? "",
    note: p.note ?? "",
    startDate: ymd(s),
    startTime: hhmm(s),
    endDate: ymd(e),
    endTime: hhmm(e),
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
  // «Судно-шаблон»: выбор прошлой постановки живёт ВНУТРИ окна создания,
  // а не второй модалкой поверх: Esc у обеих модалок общий, и закрытие
  // выбора съело бы незаполненную форму.
  const [tplOpen, setTplOpen] = useState(false);
  const [tplRows, setTplRows] = useState<Placement[] | null>(null);
  const [tplQuery, setTplQuery] = useState("");
  const [tplError, setTplError] = useState("");
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
    id: number; startX: number; dx: number; grabY: number; grabX: number; ownTop: number;
    /** Момент начала при текущем курсоре: час + магнит к чужому краю. Им живёт
     *  предпросмотр и им же сохраняется: шаг предпросмотра обязан совпадать с
     *  шагом сохранения, иначе в момент отпускания полоса «уплывёт». */
    startAt: Date;
    /** Курсор: под ним подсказка со сроком. */
    cx: number; cy: number;
    blocks: { id: number; left: number; top: number; height: number }[];
    berthId: number; offsetM: number; topInBlock: number;
  } | null>(null);
  // правая кнопка: меню у курсора; «Временное перемещение» — копия за курсором
  const [menu, setMenu] = useState<{ x: number; y: number; p: Placement } | null>(null);
  // «судно у мышки» по правой кнопке: полупрозрачная копия ездит за курсором,
  // стрелка тянется из оригинала, по отпусканию кнопки копия встаёт в точку
  const [ghost, setGhost] = useState<{
    p: Placement; x: number; y: number; cx: number; cy: number;
    berthId: number; offsetM: number; start: Date;
  } | null>(null);
  // окно «Временное перемещение»: куда поставить (причал, нахлёст, отметка) и срок
  const [move, setMove] = useState<Placement | null>(null);
  const [moveForm, setMoveForm] = useState<Form | null>(null);
  const [moveError, setMoveError] = useState("");
  // рука на левом/правом краю полосы: срок правится часом, а не слотом
  const [edge, setEdge] = useState<{
    p: Placement; side: "start" | "end"; id: number;
    start: Date; end: Date; x: number; y: number; scaleLeft: number; moved: boolean;
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

  // Номер запроса + отмена (TASKS2.md п.24, эталон — DrilldownPanel): смена
  // «с»/«по» или пресета во время медленного `/placements` — тот самый
  // сценарий проверки п.24: опоздавший ответ не должен перекрашивать сетку
  // старым планом и гасить спиннер нового. Один контроллер на страницу:
  // отменяем прежний запрос в начале загрузки и при уходе со страницы.
  const seq = useRef(0);
  const ctrl = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    const my = ++seq.current;
    ctrl.current?.abort();
    const ac = new AbortController();
    ctrl.current = ac;
    setBusy(true);
    try {
      const p = await api.vesselPlan(from, to, undefined, ac.signal);
      if (my !== seq.current) return;
      setPlan(p);
      setError("");
    } catch (e) {
      // отмена — не ошибка (TASKS2.md п.24): плашка обязана молчать
      if (isAbortError(e)) return;
      if (my !== seq.current) return;
      setError(errText(e, "Не удалось загрузить план"));
    } finally {
      // спиннер гасит только последний запрос: отменённый не имеет права
      // снять его у того, что ещё летит
      if (my === seq.current) setBusy(false);
    }
  }, [from, to]);

  useEffect(() => {
    void load();
    return () => {
      // смена периода/пресета (пересборка `load`) или уход со страницы:
      // летящий `/placements` больше не нужен (TASKS2.md п.24)
      ctrl.current?.abort();
    };
  }, [load]);

  // График сдвигается каждый день: перестановка «состоится» на границе слота,
  // а страница у диспетчера открыта сутками. Обновляем план сами: раз в минуту,
  // пока вкладка видна, и сразу при возвращении на вкладку. Во время перетаскивания,
  // призрака или сохранения не лезем — чтобы не вырвать полосу из-под курсора.
  const interactive = useRef(false);
  useEffect(() => {
    interactive.current = !!drag || !!edge || !!move || !!ghost || busy;
  }, [drag, edge, move, ghost, busy]);
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
    const dur = parseLocal(p.ends_at).getTime() - parseLocal(p.starts_at).getTime();
    return {
      ...p,
      berth_id: drag.berthId,
      offset_m: drag.offsetM,
      starts_at: isoLocal(drag.startAt),
      ends_at: isoLocal(new Date(drag.startAt.getTime() + dur)),
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
    // «Судно у мышки»: стрелка тянется из оригинала к курсору — откуда уедет
    // копия, видно ещё до того, как отпустили кнопку. Подписи у неё нет: вести
    // ей пока некуда, шаг N→М появится, когда копия станет копией.
    if (ghost) {
      const a = anchorOf(ghost.p);
      if (a) {
        out.push({
          id: -1,
          srcId: ghost.p.id,
          ...arrowGeometry(a, { left: ghost.cx, right: ghost.cx, y: ghost.cy }),
          color: barColor(ghost.p),
          caption: "",
          label: { x: 0, y: 0, anchor: "start" },
          draft: true,
          tone: "focus",
        });
      }
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
    setTplOpen(false);
    setForm(formFrom(null, berths));
  };

  const openEdit = (p: Placement) => {
    setFormError("");
    setDetail(null);
    setForm(formFrom(p, berths));
  };

  // «Судно-шаблон»: та же форма, только данные взяты из прошлой стоянки.
  // Просмотр за 120 дней назад + текущее окно: одного месяца не хватает —
  // суда приходят циклами, а «такое же, но с другой датой» обычно ищут
  // в прошлой навигации. По судну показываем последнюю постановку.
  const openTemplate = async () => {
    setTplOpen(true);
    setTplError("");
    setTplRows(null);
    try {
      const wide = await api.vesselPlan(addDays(from, -120), to);
      const last = new Map<string, Placement>();
      for (const p of [...wide.placements].sort((a, b) => (a.starts_at < b.starts_at ? -1 : 1))) {
        last.set(p.vessel.trim().toLocaleUpperCase("ru-RU"), p);
      }
      setTplRows([...last.values()].sort((a, b) => (a.starts_at < b.starts_at ? 1 : -1)));
    } catch (e) {
      if (isAbortError(e)) return;
      setTplError(errText(e));
    }
  };

  const applyTemplate = (p: Placement) => {
    setForm((f) => {
      if (!f) return f;
      const t = formFrom(p, berths);
      // Длительность стоянки бережём (навигация грузится циклами), начало
      // ставим на сегодня: шаблон затем и нужен, чтобы дата была новая.
      const dur = Math.max(parseLocal(p.ends_at).getTime() - parseLocal(p.starts_at).getTime(), 0);
      const end = new Date(Date.now() + dur);
      // Время шаблон тащит за собой целиком, вместе с минутами: цикл «стоит с 22:00
      // по 06:30» повторяется таким же, а не «округлённым до слота».
      return {
        ...t,
        id: undefined,
        startDate: ymd(new Date()),
        startTime: "00:00",
        endDate: ymd(end),
        endTime: hhmm(end),
      };
    });
    setTplOpen(false);
  };

  const submit = async () => {
    if (!form) return;
    setFormError("");
    const body: PlacementInput = {
      berth_id: form.berth_id,
      lane: 1,
      length_m: form.length_m.trim() ? Number(form.length_m) : null,
      offset_m: form.offset_m.trim() ? Number(form.offset_m) : 0,
      // поле отправляем всегда: «пусто» здесь — осознанное «не выходит», а не
      // «не трогали». Иначе снятый нахлёст молча остался бы в базе
      spill_berth_id: form.spill_berth_id.trim() ? Number(form.spill_berth_id) : null,
      vessel: form.vessel.trim(),
      volume: form.volume.trim() || null,
      cargo: form.cargo.trim() || null,
      note: form.note.trim() || null,
      starts_at: toIso(form.startDate, form.startTime),
      ends_at: toIso(form.endDate, form.endTime),
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
      if (isAbortError(e)) return;
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
        toIso(form.startDate, form.startTime),
        toIso(form.endDate, form.endTime),
        len,
      );
      if (spot.offset_m === null || !spot.fits) {
        setFormError(`На причале ${spot.berth_length_m} м нет свободного куска в ${len} м на это время`);
        return;
      }
      setForm({ ...form, offset_m: String(spot.offset_m) });
      setFreeHint(`свободно с ${spot.offset_m} м`);
    } catch (e) {
      if (isAbortError(e)) return;
      setFormError(errText(e, "Не удалось подобрать место"));
    }
  };

  /* -------------------------- WYSIWYG: судно мышью -------------------------- */

  /** Поле шкалы под курсором; в шапке причала (между полями) — ближайший по центру. */
  const pickBlock = (blocks: { id: number; left: number; top: number; height: number }[], y: number) => {
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
    const barRect = bar.getBoundingClientRect();
    const barTop = barRect.top;
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
      grabX: e.clientX - barRect.left,
      ownTop: blocks.find((b) => b.id === p.berth_id)?.top ?? barTop,
      startAt: parseLocal(p.starts_at),
      cx: e.clientX,
      cy: e.clientY,
      blocks,
      berthId: p.berth_id,
      offsetM: p.offset_m ?? 0,
      topInBlock: lengthGeometry(p, cellH).top,
    });
  };

  /**
   * Куда встанет полоса под курсором: причал под курсором и отметка на его шкале,
   * без всякого «а разрешаю ли я тебе туда встать».
   *
   * Граница причала — не стена: это метры соседа, и корпус обязан проходить через
   * неё рукой. Любая проверка «тут тебе не влезть» внутри перетаскивании даёт ровно
   * то, что диспетчер называет «примагничивается к границам»: кабинет отвечает 400,
   * план перезагружается, полоса возвращается на место, а судно длиннее своего
   * причала вообще нельзя сдвинуть ни на метр. Про метры и пересечения спросим
   * бэкенд при сохранении — он для этого и существует.
   *
   * Отметка свободна в обе стороны: минус — нос ушёл на причал выше по фронту,
   * больше длины причала — хвост ушёл ниже. Судно без длины занимает причал
   * целиком, «Ожидание» — карман без метража: там всегда «с нуля».
   */
  const dropAt = (
    p: Placement,
    target: Berth | undefined,
    rawTarget: number,
  ): { berthId: number; offsetM: number } | null => {
    if (!target) return null;
    if (!p.length_m || target.code === PARK_CODE) return { berthId: target.id, offsetM: 0 };
    return { berthId: target.id, offsetM: Math.round(rawTarget / 5) * 5 };
  };

  /**
   * Магнит к чужому краю. Придвинуть судно вплотную к соседу, уходящему в 22:00,
   * глазом по часовой сетке тяжело: час — это ~6 px. А «впритык» и есть рабочий
   * случай разгрузки: ушло в 22:00 — пришло в 22:00. Прилипает в пределах двух
   * часов к краю соседа на том же причале. Ничего не запрещает и сам ничего не
   * передвигает: магнит выбирает ближайший кандидат, а не ближайшее разрешённое
   * место — промахнулся мимо всех краёв, значит встаёт туда, куда привёл курсор.
   */
  const snapStart = (p: Placement, want: Date, berthId: number): Date => {
    const dur = parseLocal(p.ends_at).getTime() - parseLocal(p.starts_at).getTime();
    let best = want.getTime();
    let bestD = SNAP_HOURS * HOUR_MS;
    for (const o of plan?.placements ?? []) {
      if (o.id === p.id || o.berth_id !== berthId) continue;
      const shown = displayed(o);  // край соседа — тот, что видно на плане
      if (!shown) continue;
      for (const cand of [
        parseLocal(shown.ends_at).getTime(),          // следом за соседом
        parseLocal(shown.starts_at).getTime() - dur,  // прямо перед соседом
      ]) {
        if (Math.abs(cand - want.getTime()) < bestD) {
          bestD = Math.abs(cand - want.getTime());
          best = cand;
        }
      }
    }
    return new Date(best);
  };

  const moveDrag = (e: React.PointerEvent, p: Placement) => {
    if (!drag || drag.id !== p.id) return;
    const block = pickBlock(drag.blocks, e.clientY);
    const base = { dx: e.clientX - drag.startX, cx: e.clientX, cy: e.clientY };
    let spot = { berthId: drag.berthId, offsetM: drag.offsetM };
    if (block) {
      const raw = ((e.clientY - block.top - drag.grabY) / cellH) * M_PER_CELL;
      spot = dropAt(p, berths.find((b) => b.id === block.id), raw) ?? spot;
    }
    // начало берётся по левому краю полосы, а не по курсору: тащить за середину
    // — не значит тащить начало
    const scaleLeft = block?.left ?? drag.blocks[0]?.left ?? 0;
    const want = timeAtX(e.clientX - drag.grabX, scaleLeft, pxPerHour(slotW), from);
    setDrag({
      ...drag, ...base, berthId: spot.berthId, offsetM: spot.offsetM,
      startAt: snapStart(p, want, spot.berthId),
    });
  };

  const endDrag = async (p: Placement) => {
    if (!drag) return;
    const { startAt, berthId, offsetM } = drag;
    setDrag(null);
    const same =
      +startAt === +parseLocal(p.starts_at) &&
      berthId === p.berth_id &&
      offsetM === (p.offset_m ?? 0);
    if (same) return;
    suppressClick.current = true;
    window.setTimeout(() => (suppressClick.current = false), 0);
    // перетаскивание из «Ожидания» к причалу — копия со стрелкой, оригинал
    // ждёт в кармане, пока судно физически не придёт: как в Excel у диспетчера.
    // Дату копия берёт не «свою» (в кармане она формальность), а час под курсором
    if (parkId && p.berth_id === parkId && berthId !== parkId) {
      const dur = parseLocal(p.ends_at).getTime() - parseLocal(p.starts_at).getTime();
      const end = new Date(startAt.getTime() + (dur || HOUR_MS));
      await reberthingCopy(p, { berthId, offsetM, start: startAt, end });
      return;
    }
    await movePlacement(p, { start: startAt, berthId, offsetM });
  };

  /** Клавиатура вместо мыши: стрелки — час по времени (Shift — слот) и 5 м по причалу. */
  const nudge = async (p: Placement, e: React.KeyboardEvent) => {
    const step: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -5], ArrowDown: [0, 5],
    };
    const move = step[e.key];
    if (!move) return;
    e.preventDefault();
    // шаг — час, как у мыши; слот (6 ч) остаётся на Shift, чтобы перекидывать
    // судно через сутки не двадцатью нажатиями
    const hours = move[0] * (e.shiftKey ? SLOT_HOURS.length : 1);
    await movePlacement(p, {
      start: addHours(parseLocal(p.starts_at), hours),
      berthId: p.berth_id,
      offsetM: (p.offset_m ?? 0) + move[1],
    });
  };

  /** Переставить: начало в `start`, на причал `berthId` и на отметку `offsetM`. */
  const movePlacement = async (
    p: Placement,
    dest: { start: Date; berthId: number; offsetM: number },
  ) => {
    const berth = berths.find((b) => b.id === dest.berthId);
    const dur = parseLocal(p.ends_at).getTime() - parseLocal(p.starts_at).getTime();
    const start = dest.start;
    const end = new Date(start.getTime() + dur);
    if (start < new Date(`${from}T00:00:00`) || end > new Date(`${to}T23:59:59`)) {
      setError("Судно нельзя вынести за границы выбранного периода");
      return;
    }
    // Отметку держим внутри причала (начать судно обязано у своей стенки), а вот
    // конец корпуса — нет: 120-метровый теплоход у 100-метрового причала встаёт
    // хвостом на соседний. Раньше полосу «втискивали» обратным клампом, и план
    // врал: судно выглядело вставшим в свои метры, а по факту мешало соседу.
    // «Ожидание» — карман без метража: отметка там не имеет смысла, а курсор,
    // уехавший за нижний край сетки, попадает именно в него (ближайший блок)
    // отметка свободная, в обе стороны: минус — нос ушёл на причал выше по фронту
    const offset = p.length_m && berth?.code !== PARK_CODE ? dest.offsetM : 0;
    const side = spillSide(p.length_m, offset, berth?.length_m ?? null);
    const spill = spillNeighbour(berths, berth, side);
    try {
      await api.updatePlacement(p.id, {
        starts_at: isoLocal(start),
        ends_at: isoLocal(end),
        offset_m: offset,
        berth_id: dest.berthId,
        // null сюда идёт открыто: «не выходит». Если поле не прислать вовсе,
        // бэкенд оставил бы старый нахлёст и судно продолжало бы занимать метры
        // причала, с которого его уже увели
        spill_berth_id: spill?.id ?? null,
      });
      setError("");
      const moved = dest.berthId !== p.berth_id ? ` → ${berth?.code}` : "";
      setSaved(`${p.vessel}${moved}: ${start.toLocaleDateString("ru-RU")} · ${offset} м`);
      window.setTimeout(() => setSaved(""), 5000);
      await load();
    } catch (e) {
      if (isAbortError(e)) return;
      // 409 — наехали на другое судно: откат и текст бэкенда, где именно засада.
      // сначала перезагрузка (она чистит error), потом сообщение
      await load();
      setError(errText(e, "Не удалось переставить судно"));
    }
  };

  /* ---------------------------- перешвартовка ---------------------------- */

  /** Копия постановки со стрелкой: оригинал остаётся на месте, копия — пунктиром. */
  const reberthingCopy = async (
    p: Placement,
    dest: {
      berthId: number; offsetM: number;
      /** точные границы: час под курсором либо срок из окна «Временное перемещение» */
      start: Date; end: Date;
    },
  ) => {
    const berth = berths.find((b) => b.id === dest.berthId);
    if (!berth) return;
    const { start, end } = dest;
    if (start < new Date(`${from}T00:00:00`) || end > new Date(`${to}T23:59:59`)) {
      setError("Нельзя вынести перешвартовку за границы выбранного периода");
      return;
    }
    const offset = p.length_m && berth.code !== PARK_CODE ? dest.offsetM : 0;
    const side = spillSide(p.length_m, offset, berth.length_m);
    const spill = spillNeighbour(berths, berth, side);
    try {
      await api.createPlacement({
        berth_id: dest.berthId,
        lane: 1,
        length_m: p.length_m,
        offset_m: offset,
        spill_berth_id: spill?.id ?? null,
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
      if (isAbortError(e)) return;
      await load();
      setError(errText(e, "Не удалось перешвартовать"));
    }
  };

  /**
   * Правая кнопка → «Временное перемещение»: окно, а не «судно у мышки».
   *
   * Ручной вариант (тащим полупрозрачную копию за курсором) заказчик попросил
   * убрать: им нельзя сказать ни «вот на этот причал, хвостом на соседа», ни
   * «вот с 22:00». В окне есть и причал, и нахлёст, и срок — то, что человек
   * всё равно досказывал руками после «приземления» копии.
   */
  const openMove = (p: Placement) => {
    setMenu(null);
    setMove(p);
    setMoveError("");
    setMoveForm(formFrom(p, berths));
  };

  const moveBerth = berths.find((b) => b.id === moveForm?.berth_id);
  const moveSpillChoices = spillChoices(berths, moveBerth);
  const moveSide = moveForm ? spillSide(moveForm.length_m ? Number(moveForm.length_m) : null, Number(moveForm.offset_m || 0), moveBerth?.length_m ?? null) : null;
  const moveNeighbour = spillNeighbour(berths, moveBerth, moveSide);

  /** Окно «Временное перемещение»: копия со стрелкой в выбранную точку и срок. */
  const submitMove = async () => {
    if (!move || !moveForm) return;
    const berth = berths.find((b) => b.id === moveForm.berth_id);
    if (!berth) {
      setMoveError("Выберите причал");
      return;
    }
    const start = new Date(`${moveForm.startDate}T${hhmmss(moveForm.startTime)}`);
    const end = new Date(`${moveForm.endDate}T${hhmmss(moveForm.endTime)}`);
    if (end < start) {
      setMoveError("Конец раньше начала стоянки");
      return;
    }
    const offset = Number(moveForm.offset_m || 0);
    // Нахлёст окно называет прямо: «выйдет на РП-2» — это не догадка кабинета,
    // а слово диспетчера. Для «Ожидания» метража нет, там отметка и нахлёст не нужны.
    const spill = berth.code === PARK_CODE ? null : (moveForm.spill_berth_id ? Number(moveForm.spill_berth_id) : null);
    setMoveError("");
    try {
      await api.createPlacement({
        berth_id: berth.id,
        lane: 1,
        length_m: moveForm.length_m ? Number(moveForm.length_m) : null,
        offset_m: berth.code === PARK_CODE ? 0 : offset,
        spill_berth_id: spill,
        vessel: move.vessel,
        volume: move.volume,
        cargo: move.cargo,
        note: move.note,
        color: move.color,
        starts_at: isoLocal(start),
        ends_at: isoLocal(end),
        moved_from_id: move.id,
      });
      setMove(null);
      setMoveForm(null);
      setSaved(`«${move.vessel}» → ${berth.code}: копия со стрелкой, оригинал на месте`);
      window.setTimeout(() => setSaved(""), 6000);
      await load();
    } catch (e) {
      if (isAbortError(e)) return;
      await load();
      setMoveError(errText(e, "Не удалось переместить"));
    }
  };

  /**
   * Правая кнопка → «Временное перемещение» мышью: полупрозрачная копия ездит за
   * курсором, по отпусканию кнопки она становится копией со стрелкой (`moved_from_id`),
   * оригинал стоит. Причал — тот, над чьим полем шкалы стоит курсор, отметка — метр
   * под курсором (шаг 5 м), срок — час под курсором с тем же магнитом к чужому краю,
   * что и у левого перетаскивания. Запретов здесь нет: законность скажет бэкенд, а
   * его 409 будет прочитан текстом. Точный срок и «хвостом на соседний причал» —
   * окном, оно живёт в карточке судна (кнопка по левой кнопке).
   */
  const startReberthing = (p: Placement, at: { clientX: number; clientY: number }) => {
    const scales = [...document.querySelectorAll<HTMLElement>("[data-berth-scale]")].map((el) => {
      const r = el.getBoundingClientRect();
      return { id: Number(el.dataset.berthScale), left: r.left, top: r.top, height: r.height };
    });
    const print = document.querySelector<HTMLElement>("[data-vp-print]");
    if (!scales.length || !print) return;
    const dur = parseLocal(p.ends_at).getTime() - parseLocal(p.starts_at).getTime();
    const cur = {
      p, x: at.clientX, y: at.clientY, cx: 0, cy: 0,
      berthId: p.berth_id, offsetM: p.offset_m ?? 0, start: parseLocal(p.starts_at),
    };
    const paint = (e: { clientX: number; clientY: number }) => {
      const pr = print.getBoundingClientRect();
      cur.x = e.clientX;
      cur.y = e.clientY;
      cur.cx = e.clientX - pr.left;
      cur.cy = e.clientY - pr.top;
      setGhost({ ...cur });
    };
    const move = (e: PointerEvent) => {
      const block = pickBlock(scales, e.clientY);
      let scaleLeft = scales[0].left;
      if (block) {
        scaleLeft = block.left;
        const where = dropAt(p, berths.find((b) => b.id === block.id),
          ((e.clientY - block.top - 8) / cellH) * M_PER_CELL);
        if (where) {
          cur.berthId = where.berthId;
          cur.offsetM = where.offsetM;
        }
      }
      cur.start = snapStart(p, timeAtX(e.clientX, scaleLeft, pxPerHour(slotW), from), cur.berthId);
      paint(e);
    };
    const detach = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("keydown", esc);
    };
    const up = () => {
      detach();
      const t = { ...cur };
      setGhost(null);
      void reberthingCopy(p, {
        berthId: t.berthId,
        offsetM: t.offsetM,
        start: t.start,
        end: new Date(t.start.getTime() + dur),
      });
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      detach();
      setGhost(null);
    };
    paint(at);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("keydown", esc);
  };

  /* ---------------------------- срок за край полосы ---------------------------- */

  /**
   * Потянуть за левый или правый край полосы — поправить срок.
   *
   * Шаг — час, а не слот: слот в 6 ч для срока слишком крупный, диспетчер правит
   * «до 15:00», а не «до следующего столбца». Курсор считается от левого края
   * шкалы времени того же причала, поэтому линейка и прокрутка не сбивают счёт.
   */
  const startResize = (e: React.PointerEvent, p: Placement, side: "start" | "end") => {
    if (e.button !== 0 || !wysiwyg || !canEdit || busy) return;
    e.stopPropagation();
    e.preventDefault();
    // тянем край — карточку по завершении открывать не надо
    suppressClick.current = true;
    window.setTimeout(() => (suppressClick.current = false), 0);
    const scale = document.querySelector<HTMLElement>(`[data-berth-scale="${p.berth_id}"]`);
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    setEdge({
      p, side, id: p.id,
      start: parseLocal(p.starts_at), end: parseLocal(p.ends_at),
      x: e.clientX, y: e.clientY,
      scaleLeft: scale?.getBoundingClientRect().left ?? 0,
      moved: false,
    });
  };

  const moveResize = (e: React.PointerEvent) => {
    setEdge((cur) => {
      if (!cur) return cur;
      // пикселей в час: слот делится на 6 часов (SLOT_HOURS.length — это 4,
      // перепутать их значит считать курсор в 1,5 раза левее, чем он есть)
      const t = timeAtX(e.clientX, cur.scaleLeft, pxPerHour(slotW), from);
      const lo = new Date(`${from}T00:00:00`);
      const hi = new Date(`${to}T23:59:59`);
      let start = cur.start;
      let end = cur.end;
      if (cur.side === "start") start = new Date(Math.min(Math.max(t.getTime(), lo.getTime()), end.getTime()));
      else end = new Date(Math.max(Math.min(t.getTime(), hi.getTime()), start.getTime()));
      if (
        start.getTime() === cur.start.getTime() &&
        end.getTime() === cur.end.getTime() &&
        e.clientX === cur.x
      )
        return cur;
      return { ...cur, start, end, x: e.clientX, y: e.clientY, moved: true };
    });
  };

  /** Отпустили край — срок уходит на бэкенд; он скажет, если эти часы уже заняты. */
  const endResize = async () => {
    const cur = edge;
    setEdge(null);
    if (!cur || !cur.moved) return;
    try {
      await api.updatePlacement(cur.p.id, { starts_at: isoLocal(cur.start), ends_at: isoLocal(cur.end) });
      setError("");
      setSaved(`«${cur.p.vessel}»: ${isoLocal(cur.start)} — ${isoLocal(cur.end)}`);
      window.setTimeout(() => setSaved(""), 6000);
      await load();
    } catch (e) {
      if (isAbortError(e)) return;
      await load();
      setError(errText(e, "Не удалось изменить срок"));
    }
  };

  /** Ручки по краям полосы: слева — начало стоянки, справа — окончание. */
  const edgeHandles = (p: Placement, canDrag: boolean, height: number) =>
    canDrag && height > 14 ? (
      <>
        <span
          data-vp-edge="start"
          className="absolute left-0 top-0 h-full w-2 cursor-ew-resize rounded-l-md hover:bg-white/30"
          title="Потянуть: начало стоянки, шаг — час"
          onPointerDown={(e) => startResize(e, p, "start")}
          onPointerMove={moveResize}
          onPointerUp={() => void endResize()}
          onPointerCancel={() => setEdge(null)}
          onClick={(e) => e.stopPropagation()}
        />
        <span
          data-vp-edge="end"
          className="absolute right-0 top-0 h-full w-2 cursor-ew-resize rounded-r-md hover:bg-white/30"
          title="Потянуть: окончание стоянки, шаг — час"
          onPointerDown={(e) => startResize(e, p, "end")}
          onPointerMove={moveResize}
          onPointerUp={() => void endResize()}
          onPointerCancel={() => setEdge(null)}
          onClick={(e) => e.stopPropagation()}
        />
      </>
    ) : null;

  /** Красная рамка: судно сейчас на другом терминале; без рамки — в проливе. */
  const toggleTerminal = async (p: Placement) => {
    try {
      await api.updatePlacement(p.id, { at_other_terminal: !p.at_other_terminal });
      setDetail((d) => (d && d.id === p.id ? { ...d, at_other_terminal: !p.at_other_terminal } : d));
      await load();
    } catch (e) {
      if (isAbortError(e)) return;
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
      if (isAbortError(e)) return;
      setError(errText(e, "Не удалось удалить"));
    }
  };

  const formBerth = berths.find((b) => b.id === form?.berth_id);
  /* Нахлёст на соседний причал. Окно постановки обязано показывать его сразу при
     выборе причала: «судно длиннее причала» — не ошибка ввода, а рабочий случай,
     и диспетчер решает, чьи метры он для этого занимает. Направление задаёт фронт
     (sort_order), поэтому выбор здесь ограничен одним соседом. */
  const formSide = form
    ? spillSide(form.length_m.trim() ? Number(form.length_m) : null,
        Number(form.offset_m) || 0, formBerth?.length_m ?? null)
    : null;
  const formOverhang = form
    ? spillMeters(form.length_m.trim() ? Number(form.length_m) : null,
        Number(form.offset_m) || 0, formBerth?.length_m ?? null)
    : 0;
  /* Причал нахлёста выбирает человек, а не порядок сортировки: `sort_order`
     показывает блоки на экране, а не то, что стоит у стенки по факту. Соседей
     подписываем («выше/ниже по фронту») и подсказываем тот, куда лезет корпус по
     геометрии, но список не ограничен ими — и предыдущий, и следующий, и любой
     живой причал фронта. */
  const formSpillChoices = spillChoices(berths, formBerth);
  const formNeighbour = spillNeighbour(berths, formBerth, formSide);
  const formSpillId = form?.spill_berth_id.trim() ? Number(form.spill_berth_id) : null;
  const formSpillBerth = berths.find((b) => b.id === formSpillId);
  const spillHint = (() => {
    if (!form) return "";
    if (!formBerth?.length_m) return "задайте длину судна и причала — тогда будет видно, хватает ли метров";
    if (!formSide) {
      if (formSpillBerth) return `судно встаёт целиком у ${formBerth.code} — причал для нахлёста не нужен`;
      return "судно стоит целиком у своего причала";
    }
    if (formSpillBerth?.length_m && formOverhang > formSpillBerth.length_m)
      return `${formSpillBerth.code}: его длина ${formSpillBerth.length_m} м, а нахлёсту нужно ${formOverhang} м`;
    const side = formSide === "bow" ? "нос" : "хвост";
    if (!formSpillBerth)
      return `${side} выходит за ${formBerth.code} на ${formOverhang} м` +
        (formNeighbour ? ` — по фронту это ${formNeighbour.code}` : " — выберите, какой причал принимает корпус");
    const where = formSpillBerth.id === formNeighbour?.id ? "" : ` (по фронту здесь ${formNeighbour?.code ?? "никто"})`;
    return `${side} стоит на ${formSpillBerth.code}${where}: ${formOverhang} м его стенки заняты`;
  })();
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
      if (isAbortError(e)) return;
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
            Режим перетаскивания: тяните полосу мышью — горизонталь это время (шаг
            час, у края соседа полоса прилипает к нему), вертикаль — место у причала
            (шаг 5 м). Отпустили — сохранилось. Стрелки двигают полосу под фокусом
            (час, с Shift — слот). Правая кнопка по судну — «Временное перемещение»
            мышью: полупрозрачная копия прицепится к курсору, а от оригинала к ней
            сама протянется стрелка перешвартовки. Точный срок и «хвостом на соседний
            причал» — кнопка «Временное перемещение» в карточке судна (левая кнопка
            по полосе).
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
        <div ref={gridRef} className="mt-4 overflow-auto max-h-[68vh] border vp-frame rounded-md" data-vp-grid>
          <div style={{ width: gridWidth }} className="relative" data-vp-print>
            {/* шапка: сутки + подписи слотов */}
            <div className="flex sticky top-0 z-20 bg-white/90 backdrop-blur border-b-2 vp-frame-strong">
              <div
                className="sticky left-0 z-30 bg-white/95 border-r vp-frame flex items-stretch text-[11px] uppercase tracking-wider text-ink-400"
                style={{ width: LEFT_W }}
              >
                <div className="flex-1 px-2 py-1">Причал</div>
                {/* те же RULER_W и та же черта, что в полосах причалов ниже */}
                <div
                  className="border-l vp-frame text-right pr-2 py-1 text-[9px]"
                  style={{ width: RULER_W }}
                >
                  метры
                </div>
              </div>
              {days.map((d) => (
                <div
                  key={d}
                  className={`border-r vp-line-day ${isWeekend(d) ? "bg-ink-800" : ""}`}
                  style={{ width: slotW * 4 }}
                  data-day={d}
                >
                  <div className="text-[11px] text-center text-ink-300 py-0.5">{dmy(d).slice(0, 5)}</div>
                  <div className="flex">
                    {SLOT_HOURS.map((h) => (
                      <div
                        key={h}
                        className="text-[9px] text-center text-ink-400 border-l vp-line-slot first:border-l-0"
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
            {blocks.map(({ berth, cells, items }) => {
              /* чужие корпуса, вставшие на метры этого причала (см. foreignSpills) */
              const foreignOf = foreignSpills(plan?.placements ?? [], berths, berth.id);
              return (
              <div
                key={berth.id}
                className={`border-b-2 vp-frame-strong ${berth.code === PARK_CODE ? "bg-ink-800/20" : ""} ${drag && drag.berthId === berth.id && drag.berthId !== items[0]?.berth_id ? "ring-2 ring-inset ring-brand-sky" : ""}`}
                data-berth-block={berth.code}
                data-berth-id={berth.id}
                data-drop-target={drag && drag.berthId === berth.id ? "1" : "0"}
              >
                {/* Шапка-строка осталась только у кармана: у него нет шкалы, и
                     подписать «что это за склад» больше негде. У обычных причалов
                     подпись переехала внутрь полосы (см. ниже): строка-шапка
                     отнимала ~20 px на каждый причал, и на экран влезало три
                     причала вместо шести — при том что читают план по горизонтали. */}
                {berth.code === PARK_CODE ? (
                  <div className="flex bg-ink-900/5">
                    <div
                      className="sticky left-0 z-10 bg-white/95 border-r vp-frame px-2 py-0.5 text-xs"
                      style={{ width: LEFT_W }}
                    >
                      <span className="font-medium">Ожидание</span>
                      <span className="text-[10px] text-ink-400"> · суда вне причалов</span>
                    </div>
                    <div className="text-[10px] text-ink-400 px-2 py-0.5" style={{ width: totalSlots * slotW }}>
                      ждут черёды, груза · дата не важна · тяни чип к причалу — копия встанет на дату под курсором
                    </div>
                  </div>
                ) : null}

                {berth.code === PARK_CODE ? (
                  /* карман: шкалы времени нет — дата в «Ожидании» не важна.
                     Суда складываются чипами (можно «друг на друга» рядами),
                     а стрелка перешвартовки стартует прямо от чипа */
                  <div className="flex items-stretch">
                    <div
                      className="sticky left-0 z-10 bg-white/95 border-r vp-frame text-[9px] uppercase tracking-wider text-ink-400 px-2 py-1.5"
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
                  <div
                    className="sticky left-0 z-10 bg-white/95 border-r vp-frame flex items-stretch"
                    style={{ width: LEFT_W }}
                    data-berth-col={berth.id}
                  >
                    {/* Колонка разделена чертой на две зоны: слева подпись причала,
                         справа — шкала метров. sticky — уже позиционированный предок,
                         высота колонки равна высоте поля, поэтому ряды шкалы сидят
                         ровно на своих десятках метров и ни одна клетка поля не
                         съезжает. */}
                    <div className="flex-1 self-center pl-2 leading-tight">
                      <div className="text-xs font-medium">{berth.code}</div>
                      <div className="text-[9px] text-ink-400">
                        {berth.length_m
                          ? berth.length_m +
                            " м" +
                            (berth.depth
                              ? " · осадка " + String(berth.depth).replace(".", ",") + " м"
                              : "")
                          : "метраж не задан · 100 м"}
                      </div>
                    </div>
                    <div className="border-l vp-frame" style={{ width: RULER_W }}>
                      {rulerMarks(cells).map((m) => (
                        <div
                          key={m}
                          className="num text-right pr-2 text-[9px] text-ink-400 border-t vp-line-meter"
                          style={{ height: cellH, lineHeight: `${cellH}px` }}
                          data-ruler={m}
                        >
                          {m % (cellH < 14 ? 20 : 10) === 0 ? m : ""}
                        </div>
                      ))}
                    </div>
                  </div>

                  <div
                    className="relative"
                    style={{
                      width: totalSlots * slotW,
                      height: cells * cellH,
                      backgroundImage: gridBackground(slotW, cellH),
                    }}
                    data-berth-scale={berth.id}
                  >
                    {/* Выходные — серые полосы. Подложка полупрозрачная:
                       линии сетки лежат в background поля и обязаны читаться
                       сквозь закрашенный столбец; раньше столбец был непрозрачным
                       и съедал их целиком. Границу суток столбец больше не рисует
                       — её рисует background, иначе линия вставала вдвое. */}
                    {days
                      .filter((d) => isWeekend(d))
                      .map((d) => (
                        <div
                          key={d}
                          className="absolute top-0 bottom-0 vp-weekend"
                          style={{ left: days.indexOf(d) * slotW * 4, width: slotW * 4 }}
                        />
                    ))}

                    {/* Конец причала. Когда метраж кратен 10 м он совпадает с нижней
                       рамкой блока; когда нет (135 м) — поле дорисовано до полной
                       ячейки, и без этой линии не было бы видно, где причал кончается
                       на самом деле: судно в таком «хвосте» уже стоит на берегу. */}
                    {berth.length_m && berth.length_m % M_PER_CELL !== 0 ? (
                      <div
                        className="pointer-events-none absolute left-0 right-0 vp-berth-end"
                        style={{ top: (berth.length_m / M_PER_CELL) * cellH }}
                        data-berth-end={berth.length_m}
                      />
                    ) : null}

                    {/* Чужие корпуса на этих метрах. Причал отдаёт соседу часть
                        своей стенки — фронт обязан показывать занятые метры и у
                        хозяина, и у гостя: иначе два борта встанут в одни и те же
                        10 метров, потому что у каждого «свои» были свободны. */}
                    {foreignOf.map(({ p, from: mFrom, to: mTo, side }) => {
                      const d = displayed(p);
                      const geo = d ? barGeometry(d, from, totalSlots) : null;
                      if (!geo) return null;
                      const color = barColor(p);
                      const top = (Math.max(0, mFrom) / M_PER_CELL) * cellH;
                      const bottom = ((mTo == null ? cells * M_PER_CELL : Math.min(mTo, cells * M_PER_CELL))) / M_PER_CELL * cellH;
                      if (bottom - top < 2) return null;
                      return (
                        <div
                          key={`spill-${p.id}`}
                          className="pointer-events-none absolute overflow-hidden rounded-sm vp-spill"
                          style={{
                            left: geo.left * slotW,
                            width: geo.width * slotW - 1.5,
                            top,
                            height: bottom - top,
                            background: `repeating-linear-gradient(135deg, ${color}66 0 4px, ${color}22 4px 8px)`,
                            borderColor: color,
                          }}
                          title={`«${p.vessel}» стоит корпусом на этом причале: ${Math.round(mFrom)}–${mTo == null ? "конец" : Math.round(mTo)} м заняты`}
                          data-spill={p.id}
                        >
                          {/* Подпись та же, что печатает лист: без неё штриховка
                              выглядит случайным пятном, и у окна приходится
                              угадывать, чей это борт и сколько метров */}
                          {bottom - top >= 14 && geo.width * slotW >= 110 && (
                            <span
                              className="absolute left-1 top-0 block max-w-[calc(100%-6px)] truncate text-[10px] font-semibold leading-[14px] text-ink-900"
                              data-spill-label={p.id}
                            >
                              {side === "bow" ? "нос" : "хвост"} «{p.vessel}» ·{" "}
                              {Math.round((mTo == null ? cells * M_PER_CELL : mTo) - Math.max(0, mFrom))} м
                            </span>
                          )}
                        </div>
                      );
                    })}

                    {items.map((p) => {
                      const d = displayed(p);
                      const geo = d ? barGeometry(d, from, totalSlots) : null;
                      if (!geo) return null;
                      const color = barColor(p);
                      const v = lengthGeometry(p, cellH);
                      const dragging = drag?.id === p.id;
                      // За свой блок полоса не заходит ни снизу, ни сверху: метры выше
                      // начала шкалы лежат у причала выше по фронту, ниже конца — у
                      // следующего, и там они уже нарисованы штриховкой чужого корпуса.
                      // Иначе одна и та же часть борта показалась бы дважды — полосой
                      // поверх чужого блока и штриховкой в нём. Пока тянут (dragging),
                      // не ограничиваем: корпус обязан идти за курсором через границу.
                      const topPx = dragging ? v.top : Math.max(0, v.top);
                      const height = Math.max(
                        10,
                        (dragging
                          ? Math.min(v.height, cells * cellH - v.top)
                          : Math.min(v.top + v.height, cells * cellH) - topPx) - 2,
                      );
                      const canDrag = wysiwyg && canEdit && !busy;
                      const ring = ringOf.get(p.id); // обводка, если это цепочка под курсором
                      // пока тащим, полоса едет за курсором с тем же шагом, с каким
                      // потом сохранится: час по горизонтали, 5 м по вертикали
                      const shiftX = dragging
                        ? ((drag.startAt.getTime() - parseLocal(p.starts_at).getTime()) / HOUR_MS)
                          * pxPerHour(slotW)
                        : 0;
                      // вертикаль считаем от блока-цели: если это другой причал,
                      // полоса доезжает до него и встаёт на его отметку
                      // Полоса идёт за курсором через границу блока: то, что
                      // часть корпуса лежит в чужом ряду, и есть нахлёст — рисовать
                      // его «внутри» своего причала значило бы врать.
                      const shiftY = dragging
                        ? (drag.blocks.find((b) => b.id === drag.berthId)?.top ?? drag.ownTop) -
                          drag.ownTop +
                          (drag.offsetM / M_PER_CELL) * cellH -
                          drag.topInBlock
                        : 0;
                      // Пока тянут за край, полоса показывается почасово: сетка рисует
                      // слоты, а срок человек правит часом, и рисовать ему «прыжок на
                      // столбец» вместо тех двух часов, что он отмеряет, — значит
                      // врать в момент, когда он выбирает значение.
                      const rz = edge && edge.id === p.id ? edge : null;
                      const pxH = pxPerHour(slotW);
                      const originT = new Date(`${from}T00:00:00`).getTime();
                      const left = rz
                        ? ((rz.start.getTime() - originT) / HOUR_MS) * pxH
                        : geo.left * slotW;
                      const width = rz
                        ? Math.max(pxH, ((rz.end.getTime() - rz.start.getTime()) / HOUR_MS) * pxH)
                        : geo.width * slotW - 3;
                      return (
                        <button
                          key={p.id}
                          className={`absolute rounded-md text-left text-[11px] px-1.5 text-white shadow-sm hover:brightness-110 focus:outline-none focus:ring-2 focus:ring-brand-sky ${canDrag ? "cursor-move touch-none" : ""} ${dragging ? "z-40 ring-2 ring-brand-sky" : ""}`}
                          style={{
                            left,
                            width,
                            top: topPx + 1,
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
                          {edgeHandles(p, canDrag, height)}
                        </button>
                      );
                    })}
                  </div>
                </div>
                )}
              </div>
            );
            })}

            {/* Стрелки перешвартовок: один SVG поверх холста в координатах полос.
                Якоря считаются теми же barGeometry/lengthGeometry, что и сами
                полосы, поэтому стрелки не разъезжаются ни при каком скролле —
                оверлей скроллится внутри того же контейнера. pointer-events-none,
                чтобы не съедать drag. */}
            <VesselsArrows arrows={arrows} width={canvasSize.w} height={canvasSize.h} />
          </div>
        </div>

      {/* Правка срока за край полосы: подпись у курсора. Полоса на сетке рисует
          слоты, а живёт — часы, поэтому без подписи «по 13.11 15:00» человек не
          видит, что реально сохранится. Портал в body: у .panel стоит
          backdrop-filter, и fixed-потомок лепился бы к панели, а не к окну. */}
      {/* Подсказка при перетаскивании: срок, который получится при текущем
          курсоре. Шаг — час, и без точного времени человек не видит, куда
          реально ставит полосу, и не поймёт, встал ли он вплотную к соседу. */}
      {drag ? createPortal(
        <div
          className="fixed z-50 pointer-events-none select-none rounded-md border border-ink-700 bg-white px-2 py-1 text-[11px] text-ink-100 shadow-lg"
          style={{ left: drag.cx + 12, top: drag.cy + 18 }}
          data-vp-drag-hint
        >
          <b>с {drag.startAt.toLocaleString("ru-RU")}</b>
          <span className="block text-[10px] text-ink-400" data-vp-drag-hint-end>
            {(() => {
              const d = (plan?.placements ?? []).find((x) => x.id === drag.id);
              if (!d) return "";
              const dur = parseLocal(d.ends_at).getTime() - parseLocal(d.starts_at).getTime();
              const end = new Date(drag.startAt.getTime() + dur);
              return `по ${end.toLocaleString("ru-RU")} · весь срок ${hoursBetween(drag.startAt, end)}`;
            })()}
          </span>
          <span className="block text-[10px] text-ink-400">шаг — час, у края соседа липнет</span>
        </div>,
        document.body,
      ) : null}

      {edge ? createPortal(
        <div
          className="fixed z-50 pointer-events-none select-none rounded-md border border-ink-700 bg-white px-2 py-1 text-[11px] text-ink-100 shadow-lg"
          style={{ left: edge.x + 12, top: edge.y + 18 }}
          data-vp-resize-hint
        >
          <b>{edge.side === "start" ? "с" : "по"}</b>{" "}
          {(edge.side === "start" ? edge.start : edge.end).toLocaleString("ru-RU")}
          <span className="block text-[10px] text-ink-400">
            весь срок: {hoursBetween(edge.start, edge.end)}
          </span>
          <span className="block text-[10px] text-ink-400">шаг — час, слоты на сетке не указ</span>
        </div>,
        document.body,
      ) : null}

      {/* «Судно у мышки» и контекстное меню — порталом в body: у .panel стоит
          backdrop-filter, он создаёт containing block, и fixed-потомок лепился
          бы к панели, а не к окну — копия уезжала бы от курсора */}
      {ghost ? createPortal(
        // Подсказка у курсора. У правого и нижнего края окна она разворачивается
        // на другую сторону курсора, а не упирается в стену: без этого «судно у
        // мышки» сжималось в узкую колонну на пол-экрана (текст не влезал в
        // остаток ширины и переносился по слову).
        <div
          className="fixed z-50 pointer-events-none select-none rounded-md px-2 py-1 text-[11px] text-white shadow-lg"
          style={{
            left: ghost.x + 12,
            top: ghost.y + 12,
            transform: [
              ghost.x > window.innerWidth - 340 ? "translateX(calc(-100% - 24px))" : "",
              ghost.y > window.innerHeight - 90 ? "translateY(calc(-100% - 24px))" : "",
            ].filter(Boolean).join(" ") || undefined,
            whiteSpace: "nowrap",
            background: barColor(ghost.p),
            opacity: 0.8,
            border: "2px dashed rgba(255,255,255,.95)",
          }}
          data-vp-ghost={ghost.p.id}
        >
          {ghost.p.vessel} · {berthCode(ghost.berthId)} · {ghost.offsetM} м
          <span className="block text-[10px] opacity-90">
            с {hhmm(ghost.start)} · копия со стрелкой, оригинал стоит
          </span>
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
              ⇄ Временное перемещение мышью
              <span className="block text-[10px] text-ink-400">полупрозрачная копия едет за курсором, стрелка — от оригинала; точный срок — в карточке судна</span>
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
            {detail.spill_berth_id ? (
              <div className="col-span-2">
                <dt className="panel-title">Корпус на соседнем причале</dt>
                <dd data-detail-spill>
                  {(() => {
                    const own = berths.find((b) => b.id === detail.berth_id);
                    const to = berths.find((b) => b.id === detail.spill_berth_id);
                    const m = spillMeters(detail.length_m, detail.offset_m ?? 0, own?.length_m ?? null);
                    return (
                      <>
                        {detail.offset_m < 0 ? "нос" : "хвост"} стоит на {to?.code ?? "?"}
                        {m ? `: ${m} м его стенки заняты этим судном` : ": метры соседа заняты этим судном"}
                        {" — на плане они заштрихованы у хозяина и у гостя"}
                      </>
                    );
                  })()}
                </dd>
              </div>
            ) : null}
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
              <button
                className="btn-ghost text-sm"
                onClick={() => {
                  const d = detail;
                  setDetail(null);
                  openMove(d);
                }}
                data-admin-action="move-placement"
              >
                ⇄ Временное перемещение
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
          title={form.id ? "Правка постановки" : tplOpen ? "Судно-шаблон — выберите прошлую постановку" : "Новая постановка"}
          onClose={() => {
            setForm(null);
            setTplOpen(false);
          }}
          testId="placement-form"
          footer={
            <div className="space-y-3">
              <FormError text={formError} />
              <div className="flex flex-wrap items-center gap-2">
                {tplOpen ? (
                  <button className="btn-ghost text-sm" onClick={() => setTplOpen(false)} data-admin-action="template-back">
                    Назад к форме
                  </button>
                ) : (
                  <>
                    <button className="btn-primary text-sm" onClick={submit} data-admin-action="save-placement">
                      {form.id ? "Сохранить" : "Поставить судно"}
                    </button>
                    <button className="btn-ghost text-sm" onClick={() => setForm(null)}>
                      Отмена
                    </button>
                  </>
                )}
              </div>
            </div>
          }
        >
          {tplOpen ? (
            <TemplatePicker
              rows={tplRows}
              query={tplQuery}
              error={tplError}
              onQuery={setTplQuery}
              codeOf={(id) => berths.find((b) => b.id === id)?.code ?? "причал?"}
              onPick={applyTemplate}
            />
          ) : (
            <>
          {!form.id && (
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <button className="btn-ghost text-xs" onClick={openTemplate} data-admin-action="template-pick">
                Скопировать из предыдущей постановки…
              </button>
              <span className="text-xs text-ink-400">причал, груз, длина и цвет возьмём из прошлой стоянки — дату поставите новую</span>
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Причал">
              <select
                className="field"
                value={form.berth_id}
                onChange={(e) => setForm({ ...form, berth_id: Number(e.target.value), offset_m: "0", spill_berth_id: "" })}
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
            <Field label="Выходит на соседний причал" hint={spillHint}>
              <select
                className="field"
                value={form.spill_berth_id}
                onChange={(e) => setForm({ ...form, spill_berth_id: e.target.value })}
                data-field="spill-berth"
              >
                <option value="">— не выходит —</option>
                {[formSpillChoices.prev, formSpillChoices.next]
                  .filter((b): b is Berth => !!b)
                  .map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.code}
                      {b.length_m ? ` · ${b.length_m} м` : ""} ·{" "}
                      {b.id === formSpillChoices.prev?.id ? "выше по фронту" : "ниже по фронту"}
                      {b.id === formNeighbour?.id ? " ←" : ""}
                    </option>
                  ))}
                {formSpillChoices.others.length ? (
                  <optgroup label="другие причалы">
                    {formSpillChoices.others.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.code}
                        {b.length_m ? ` · ${b.length_m} м` : ""}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
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
                placeholder="Введите длину"
                data-field="length-m"
              />
            </Field>
            <Field
              label="Место у причала, м"
              hint="отметка от начала причала, кратно 10 м; минус — нос встанет на причал выше по фронту"
            >
              <div className="flex gap-2">
                <input
                  className="field num"
                  type="number"
                  min={-500}
                  step={10}
                  value={form.offset_m}
                  onChange={(e) => setForm({ ...form, offset_m: e.target.value })}
                  placeholder="Введите место"
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
              <input className="field" value={form.vessel} onChange={(e) => setForm({ ...form, vessel: e.target.value })} data-field="vessel" placeholder="Введите название судна" />
            </Field>
            <Field label="Объём / тоннаж" hint="как в плане: 5 500 или (100+40) + 4 000">
              <input className="field" value={form.volume} onChange={(e) => setForm({ ...form, volume: e.target.value })} data-field="volume" placeholder="Введите объём или тоннаж" />
            </Field>
            <Field label="Груз" hint="БШ, БАНАНЫ-ПАЛЛЕТ, МЛК (БКК)…">
              <input className="field" value={form.cargo} onChange={(e) => setForm({ ...form, cargo: e.target.value })} data-field="cargo" list="cargo-list" placeholder="Введите или выберите груз" />
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
              <input className="field" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} data-field="note" placeholder="Введите примечание" />
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

            <Field label="Начало стоянки" hint="любое время: судно встаёт, когда пришло, а не когда слот">
              <div className="flex gap-2">
                <input type="date" className="field" value={form.startDate} onChange={(e) => setForm({ ...form, startDate: e.target.value })} data-field="start-date" />
                <input type="time" step={60} className="field w-28" value={form.startTime} onChange={(e) => setForm({ ...form, startTime: e.target.value })} data-field="start-hour" />
              </div>
              <SlotHints value={form.startTime} onPick={(t) => setForm({ ...form, startTime: t })} />
            </Field>
            <Field label="Окончание стоянки" hint="когда судно ушло; слоты на сетке — только шкала, не ограничение">
              <div className="flex gap-2">
                <input type="date" className="field" value={form.endDate} onChange={(e) => setForm({ ...form, endDate: e.target.value })} data-field="end-date" />
                <input type="time" step={60} className="field w-28" value={form.endTime} onChange={(e) => setForm({ ...form, endTime: e.target.value })} data-field="end-hour" />
              </div>
              <SlotHints value={form.endTime} onPick={(t) => setForm({ ...form, endTime: t })} />
            </Field>
          </div>
          <Notice text="Два судна мешают друг другу, только если пересекаются и по времени, и по метрам: 100 м причала — это 10 ячеек по 10 м. Кабинет не даст поставить судно наехавшим на уже стоящее и скажет, какое именно и где именно. Судно длиннее причала встать может — но только если указано, на какой соседний причал выходит корпус: эти метры соседнего причала считаются занятыми." />
            </>
          )}
        </Modal>
      )}
      {move && moveForm ? (
        <Modal
          title={`Временное перемещение · ${move.vessel}`}
          onClose={() => {
            setMove(null);
            setMoveForm(null);
          }}
          testId="move-form"
          footer={
            <>
              <button className="btn btn-primary" onClick={() => void submitMove()} data-admin-action="save-move">
                Переместить
              </button>
              <button
                className="btn btn-ghost text-sm"
                onClick={() => {
                  setMove(null);
                  setMoveForm(null);
                }}
              >
                Отмена
              </button>
            </>
          }
        >
          <p className="text-[11px] leading-snug text-ink-300">
            Оригинал остаётся на месте, копия встанет сюда и получит стрелку. Причал, соседний
            причал под корпус и срок называется здесь же: прежнее «судно у мышки» умело только
            тыкать мышью, а досказывать причал и время всё равно приходилось руками.
          </p>
          {moveError ? <FormError text={moveError} /> : null}
          <div className="mt-3 grid grid-cols-2 gap-3">
            <Field
              label="Причал"
              hint={
                moveBerth?.code === PARK_CODE
                  ? "в кармане «Ожидание» нет метража: судно ждёт очереди, а не стоит у стенки"
                  : moveBerth?.length_m
                    ? `${moveBerth.code} — ${moveBerth.length_m} м`
                    : "метраж причала не задан"
              }
            >
              <select
                className="field"
                value={moveForm.berth_id}
                onChange={(e) => setMoveForm({ ...moveForm, berth_id: Number(e.target.value), spill_berth_id: "" })}
                data-field="move-berth"
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
              label="Выходит на соседний причал"
              hint={
                moveBerth?.code === PARK_CODE
                  ? "у кармана нет стенки — корпус выходит некуда"
                  : moveNeighbour
                    ? `${moveSide === "bow" ? "нос" : "хвост"} выходит за ${moveBerth?.code ?? "?"} — по фронту это ${moveNeighbour.code}`
                    : "корпус встаёт целиком у своего причала"
              }
            >
              <select
                className="field"
                value={moveForm.spill_berth_id}
                onChange={(e) => setMoveForm({ ...moveForm, spill_berth_id: e.target.value })}
                data-field="move-spill"
              >
                <option value="">— не выходит —</option>
                {[moveSpillChoices.prev, moveSpillChoices.next]
                  .filter((b): b is Berth => !!b)
                  .map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.code}
                      {b.length_m ? ` · ${b.length_m} м` : ""} ·{" "}
                      {b.id === moveSpillChoices.prev?.id ? "выше по фронту" : "ниже по фронту"}
                      {b.id === moveNeighbour?.id ? " ←" : ""}
                    </option>
                  ))}
                {moveSpillChoices.others.length ? (
                  <optgroup label="другие причалы">
                    {moveSpillChoices.others.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.code}
                        {b.length_m ? ` · ${b.length_m} м` : ""}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
              </select>
            </Field>
            <Field
              label="Место у причала, м"
              hint="отметка от начала причала, шаг 5 м; минус — нос ушёл на причал выше по фронту"
            >
              <input
                className="field num"
                type="number"
                step={5}
                value={moveForm.offset_m}
                onChange={(e) => setMoveForm({ ...moveForm, offset_m: e.target.value })}
                data-field="move-offset"
              />
            </Field>
            <Field
              label="Длина судна, м"
              hint="с длиной видно, сколько метров уходит соседу; пусто — причал занят до конца"
            >
              <input
                className="field num"
                type="number"
                min={1}
                max={500}
                step={5}
                value={moveForm.length_m}
                onChange={(e) => setMoveForm({ ...moveForm, length_m: e.target.value })}
                data-field="move-length"
              />
            </Field>
            <Field label="Начало стоянки" hint="любое время: судно встаёт, когда пришло">
              <div className="flex gap-2">
                <input
                  type="date"
                  className="field"
                  value={moveForm.startDate}
                  onChange={(e) => setMoveForm({ ...moveForm, startDate: e.target.value })}
                  data-field="move-start-date"
                />
                <input
                  type="time"
                  step={60}
                  className="field w-28"
                  value={moveForm.startTime}
                  onChange={(e) => setMoveForm({ ...moveForm, startTime: e.target.value })}
                  data-field="move-start-hour"
                />
              </div>
              <SlotHints value={moveForm.startTime} onPick={(t) => setMoveForm({ ...moveForm, startTime: t })} />
            </Field>
            <Field label="Окончание стоянки" hint="когда судно уходит; слоты на сетке — только шкала">
              <div className="flex gap-2">
                <input
                  type="date"
                  className="field"
                  value={moveForm.endDate}
                  onChange={(e) => setMoveForm({ ...moveForm, endDate: e.target.value })}
                  data-field="move-end-date"
                />
                <input
                  type="time"
                  step={60}
                  className="field w-28"
                  value={moveForm.endTime}
                  onChange={(e) => setMoveForm({ ...moveForm, endTime: e.target.value })}
                  data-field="move-end-hour"
                />
              </div>
              <SlotHints value={moveForm.endTime} onPick={(t) => setMoveForm({ ...moveForm, endTime: t })} />
            </Field>
          </div>
          <Notice
            text={`Копия: ${moveBerth?.code ?? "?"}${
              moveForm.spill_berth_id
                ? ` + корпус на ${berths.find((b) => b.id === Number(moveForm.spill_berth_id))?.code ?? "?"}`
                : ""
            } · ${toIso(moveForm.startDate, moveForm.startTime).slice(0, 16).replace("T", " ")} — ${toIso(
              moveForm.endDate,
              moveForm.endTime,
            ).slice(0, 16).replace("T", " ")} · ${moveForm.offset_m || 0} м`}
          />
        </Modal>
      ) : null}
    </AppShell>
  );
}

/**
 * «Судно-шаблон»: список прошлых постановок, из которого заполняется форма.
 * По судну — одна строка (последняя стоянка), поиск по названию. Даты здесь
 * не выбираются — шаблон переносит длительность на сегодня, остальное
 * правится в форме: плодить два места выбора даты на одном окне незачем.
 */
function TemplatePicker({
  rows,
  query,
  error,
  onQuery,
  codeOf,
  onPick,
}: {
  rows: Placement[] | null;
  query: string;
  error: string;
  onQuery: (q: string) => void;
  codeOf: (berthId: number) => string;
  onPick: (p: Placement) => void;
}) {
  const q = query.trim().toLocaleLowerCase("ru-RU");
  const shown = (rows ?? []).filter((p) => !q || p.vessel.toLocaleLowerCase("ru-RU").includes(q));
  return (
    <div className="space-y-3">
      <FormError text={error} />
      <input
        className="field"
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        placeholder="Поиск по названию судна…"
        autoFocus
        data-field="template-search"
      />
      {rows === null && !error && <p className="text-sm text-ink-300">Загружаем прошлые постановки…</p>}
      {rows !== null && shown.length === 0 && (
        <p className="text-sm text-ink-300">
          {q
            ? "Ничего не найдено — за последние четыре месяца таких судов у причалов не было."
            : "Прошлых постановок нет — заполните форму с нуля."}
        </p>
      )}
      <div className="max-h-80 space-y-2 overflow-y-auto pr-1" data-modal="template-list">
        {shown.map((p) => (
          <button
            key={p.id}
            className="block w-full rounded-lg border border-ink-600 bg-ink-900 px-3 py-2 text-left hover:bg-ink-800/60"
            onClick={() => onPick(p)}
            data-admin-action="template-pick-one"
          >
            <span className="block text-sm font-semibold text-ink-100">{p.vessel}</span>
            <span className="block text-xs text-ink-400">
              {[
                codeOf(p.berth_id),
                p.cargo,
                p.volume,
                p.length_m ? `${p.length_m} м` : null,
                `последняя стоянка: ${dmy(ymd(parseLocal(p.starts_at)))}`,
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
