/**
 * Печатный лист «Постановки судов» — то, что уходит в PDF.
 *
 * От экранной сетки он отличается двумя вещами.
 *
 * 1. В лист попадает **только видимая область**: те сутки и причалы, которые
 *    сейчас в окне сетки. Экран показывает кусок плана (месяц в любую ширину не
 *    влезает), и пользователь хочет на бумаге тот же кадр, крупно.
 *
 * 2. Ширины — **в процентах**, а не в пикселях. Пиксельный лист ломался: браузер
 *    печатает на бумаге, размер которой из JS не узнать (A4 портрет, A4 альбом,
 *    A3 — что выберут в диалоге). Лист в 1500 px на A4 расползался на три страницы
 *    по горизонтали, и в превью было видно четыре суток из пятнадцати. Проценты
 *    прижимают лист к фактической ширине страницы: что бы ни выбрали, влезает.
 *
 * Колонка причала остаётся в пикселях: она обязана остаться читаемой, а не
 * сжиматься вместе с сеткой.
 *
 * Границы окна берёт родитель: сутки целыми (половину суток печатать нельзя),
 * причал — целый блок, если он хоть частично в кадре. Полоса судна, переходящая
 * за границу кадра, дорисовывается пунктиром.
 */
import type { Berth, Placement } from "../api";
import { dmy } from "../format";
import {
  barGeometry, cargoColor, isWeekend, lengthGeometry, lengthLabel, M_PER_CELL,
  parseLocal, rulerMarks, slotLabel, SLOT_HOURS,
} from "../vessels";

export type PrintWindow = {
  /** первые сутки окна (индекс в массиве дней периода) */
  d0: number;
  /** последние сутки окна, включительно */
  d1: number;
  /** причалы, хоть частично попавшие в кадр */
  berthIds: number[];
};

type Block = { berth: Berth; cells: number; items: Placement[] };

/** Колонка причала в печати. Узче экранной: на A4 168 px съедают слишком много. */
const PRINT_LEFT_W = 118;

export function VesselPlanPrint({
  days,
  from,
  blocks,
  win,
  cellH,
}: {
  days: string[];
  from: string;
  blocks: Block[];
  win: PrintWindow;
  cellH: number;
}) {
  const daySlice = days.slice(win.d0, win.d1 + 1);
  const winSlots = daySlice.length * SLOT_HOURS.length;
  const shown = blocks.filter((b) => win.berthIds.includes(b.berth.id));

  // всё поле времени — это остаток ширины страницы после колонки причала
  const field = `calc(100% - ${PRINT_LEFT_W}px)`;
  const dayW = `calc(${field} / ${daySlice.length})`;
  /** ширина и сдвиг в слотах → проценты поля */
  const frac = (slots: number) => `calc(${field} * ${slots} / ${winSlots})`;

  return (
    <div className="vp-print-sheet">
      <div className="flex items-baseline justify-between gap-4 pb-1 mb-1 border-b border-ink-700">
        <div className="text-[13px] font-medium">План постановки судов · видимая область</div>
        <div className="text-[10px] num">
          {dmy(daySlice[0])} — {dmy(daySlice[daySlice.length - 1])}
          {daySlice.length < days.length ? ` (из ${days.length} сут периода)` : ""}
        </div>
      </div>

      <div className="border border-ink-700 break-inside-avoid">
        {/* шапка: сутки и слоты */}
        <div className="flex border-b border-ink-700">
          <div
            className="border-r border-ink-700 text-[9px] uppercase tracking-wider px-1 py-1"
            style={{ width: PRINT_LEFT_W }}
          >
            Причал / метры
          </div>
          {daySlice.map((d) => (
            <div
              key={d}
              className={`border-r border-ink-700 ${isWeekend(d) ? "bg-ink-800" : ""}`}
              style={{ width: dayW }}
              data-print-day={d}
            >
              <div className="text-[9px] text-center py-0.5">{dmy(d).slice(0, 5)}</div>
              <div className="flex">
                {SLOT_HOURS.map((h) => (
                  <div
                    key={h}
                    className="text-[7px] text-center border-l border-ink-700 first:border-l-0"
                    style={{ width: "25%" }}
                  >
                    {slotLabel(h)}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* строка причала: код, осадка, метраж */}
        {shown.map(({ berth, cells }) => (
          <div key={`head-${berth.id}`} className="flex border-b border-ink-700 break-inside-avoid">
            <div className="border-r border-ink-700 px-1 py-0.5 text-[10px]" style={{ width: PRINT_LEFT_W }}>
              <span className="font-medium">{berth.code}</span>
              {berth.depth ? (
                <span className="text-[8px]"> · {String(berth.depth).replace(".", ",")} м</span>
              ) : null}
            </div>
            <div className="text-[8px] px-1 py-0.5" style={{ width: field }}>
              {berth.length_m
                ? `${berth.length_m} м длины · ${cells} ячеек по ${M_PER_CELL} м`
                : "метраж причала не задан · показываем 100 м"}
            </div>
          </div>
        ))}

        {/* поля шкалы: линейка метров + полосы судов */}
        {shown.map(({ berth, cells, items }) => {
          const wStart = win.d0 * SLOT_HOURS.length;
          const wEnd = wStart + winSlots;
          return (
            <div key={`scale-${berth.id}`} className="flex border-b border-ink-700 last:border-b-0 break-inside-avoid">
              <div className="border-r border-ink-700" style={{ width: PRINT_LEFT_W }}>
                {rulerMarks(cells).map((m) => (
                  <div
                    key={m}
                    className="num text-right pr-1 text-[7px] border-t border-ink-700/50"
                    style={{ height: cellH, lineHeight: `${cellH}px` }}
                  >
                    {m % (cellH < 14 ? 20 : 10) === 0 ? m : ""}
                  </div>
                ))}
              </div>
              <div className="relative" style={{ width: field, height: cells * cellH }} data-print-field={berth.id}>
                {daySlice.map((d, i) => (
                  <div
                    key={d}
                    className={`absolute top-0 bottom-0 border-r border-ink-700 ${isWeekend(d) ? "bg-ink-800/70" : ""}`}
                    style={{ left: frac(i * SLOT_HOURS.length), width: dayW }}
                  />
                ))}
                {Array.from({ length: cells - 1 }, (_, i) => (
                  <div
                    key={i}
                    className="absolute left-0 right-0 border-t border-ink-700/40"
                    style={{ top: (i + 1) * cellH }}
                  />
                ))}

                {items.map((p) => {
                  const geo = barGeometry(p, from, days.length * SLOT_HOURS.length);
                  if (!geo) return null;
                  const left = Math.max(geo.left, wStart);
                  const right = Math.min(geo.left + geo.width, wEnd);
                  if (right <= left) return null;
                  const cutStart = geo.clippedStart || geo.left < wStart;
                  const cutEnd = geo.clippedEnd || geo.left + geo.width > wEnd;
                  const color = cargoColor(p.cargo);
                  const v = lengthGeometry(p, cellH);
                  const height = Math.max(9, Math.min(v.height, cells * cellH - v.top) - 2);
                  return (
                    <div
                      key={p.id}
                      className="absolute rounded-[3px] text-left text-[8px] px-1 text-white leading-tight"
                      data-print-bar={p.id}
                      data-berth={berth.code}
                      style={{
                        left: frac(left - wStart),
                        width: `calc(${field} * ${right - left} / ${winSlots} - 2px)`,
                        top: v.top + 1,
                        height,
                        background: `linear-gradient(180deg, ${color}f0, ${color}cc)`,
                        borderLeft: cutStart ? "2px dotted rgba(255,255,255,.85)" : "2px solid rgba(0,0,0,.18)",
                        borderRight: cutEnd ? "2px dotted rgba(255,255,255,.85)" : undefined,
                      }}
                      title={`${p.vessel} · ${lengthLabel(p)} · ${parseLocal(p.starts_at).toLocaleString("ru-RU")}`}
                    >
                      <span className="block truncate">{p.vessel}</span>
                      {height >= 22 ? (
                        <span className="block truncate text-[7px] opacity-85">
                          {[p.volume, p.length_m ? `${p.length_m} м` : null].filter(Boolean).join(" · ")}
                        </span>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      <p className="mt-1 text-[8px]">
        Пунктирный край — постановка продолжается за границей кадра. Лист собран по тому, что
        было видно в окне сетки: {daySlice.length} сут из {days.length} и {shown.length} причалов.
      </p>
    </div>
  );
}
