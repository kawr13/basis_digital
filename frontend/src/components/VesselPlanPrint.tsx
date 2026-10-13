/**
 * Печатный лист «Постановки судов» — то, что уходит в PDF.
 *
 * От экранной сетки он отличается одним: в лист попадает **только видимая
 * область**. Экран показывает кусок плана (месяц в любую ширину не влезает), и
 * пользователь хочет получить на бумаге ровно тот кадр, который смотрит, —
 * крупные сутки и причалы, а не весь период, сжатый до нечитаемого кегля.
 *
 * Границы окна берёт родитель: сутки целыми (печатать половину суток нельзя),
 * причал — целый блок, если он хоть частично в кадре. Полоса судна, переходящая
 * за границу кадра, дорисовывается пунктиром — тот же приём, что и в экранных
 * постановках, начатых раньше периода.
 *
 * Никакой интерактивности: это статичный лист, его рисует браузер при печати.
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

export function VesselPlanPrint({
  days,
  from,
  blocks,
  win,
  slotW,
  leftW,
  cellH,
}: {
  days: string[];
  from: string;
  blocks: Block[];
  win: PrintWindow;
  slotW: number;
  leftW: number;
  cellH: number;
}) {
  const daySlice = days.slice(win.d0, win.d1 + 1);
  const winSlots = daySlice.length * SLOT_HOURS.length;
  const shown = blocks.filter((b) => win.berthIds.includes(b.berth.id));

  return (
    <div className="vp-print-sheet">
      <div className="flex items-baseline justify-between gap-4 pb-1 mb-2 border-b border-ink-700">
        <div className="text-sm font-medium">План постановки судов · видимая область</div>
        <div className="text-[11px] num">
          {dmy(daySlice[0])} — {dmy(daySlice[daySlice.length - 1])}
          {daySlice.length < days.length ? ` (из ${days.length} сут выбранного периода)` : ""}
        </div>
      </div>

      <div className="border border-ink-700">
        {/* шапка: сутки и слоты */}
        <div className="flex border-b border-ink-700">
          <div
            className="border-r border-ink-700 text-[10px] uppercase tracking-wider px-2 py-1"
            style={{ width: leftW }}
          >
            Причал / метры
          </div>
          {daySlice.map((d) => (
            <div
              key={d}
              className={`border-r border-ink-700 ${isWeekend(d) ? "bg-ink-800" : ""}`}
              style={{ width: slotW * 4 }}
              data-print-day={d}
            >
              <div className="text-[10px] text-center py-0.5">{dmy(d).slice(0, 5)}</div>
              <div className="flex">
                {SLOT_HOURS.map((h) => (
                  <div
                    key={h}
                    className="text-[8px] text-center border-l border-ink-700 first:border-l-0"
                    style={{ width: slotW }}
                  >
                    {slotLabel(h)}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {shown.map(({ berth, cells }) => (
          <div key={berth.id} className="flex border-b border-ink-700 last:border-b-0">
            <div className="border-r border-ink-700 px-2 py-0.5 text-[11px]" style={{ width: leftW }}>
              <span className="font-medium">{berth.code}</span>
              {berth.depth ? (
                <span className="text-[9px]"> · осадка {String(berth.depth).replace(".", ",")} м</span>
              ) : null}
            </div>
            <div className="text-[9px] px-2 py-0.5" style={{ width: winSlots * slotW }}>
              {berth.length_m
                ? `${berth.length_m} м длины · ${cells} ячеек по ${M_PER_CELL} м`
                : "метраж причала не задан · показываем 100 м"}
            </div>
          </div>
        ))}

        {/* поля шкалы: линейка метров + полосы судов */}
        {shown.map(({ berth, cells, items }) => {
          const origin = win.d0 * SLOT_HOURS.length;
          return (
            <div key={`scale-${berth.id}`} className="flex border-b border-ink-700 last:border-b-0">
              <div className="border-r border-ink-700" style={{ width: leftW }}>
                {rulerMarks(cells).map((m) => (
                  <div
                    key={m}
                    className="num text-right pr-2 text-[8px] border-t border-ink-700/50"
                    style={{ height: cellH, lineHeight: `${cellH}px` }}
                  >
                    {m % (cellH < 14 ? 20 : 10) === 0 ? m : ""}
                  </div>
                ))}
              </div>
              <div className="relative" style={{ width: winSlots * slotW, height: cells * cellH }} data-print-field={berth.id}>
                {daySlice.map((d, i) => (
                  <div
                    key={d}
                    className={`absolute top-0 bottom-0 border-r border-ink-700 ${isWeekend(d) ? "bg-ink-800/70" : ""}`}
                    style={{ left: i * slotW * 4, width: slotW * 4 }}
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
                  // окно кадра в слотах периода
                  const wStart = origin;
                  const wEnd = origin + winSlots;
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
                      className="absolute rounded-[3px] text-left text-[9px] px-1 text-white"
                      data-print-bar={p.id}
                      data-berth={berth.code}
                      style={{
                        left: (left - wStart) * slotW,
                        width: (right - left) * slotW - 2,
                        top: v.top + 1,
                        height,
                        background: `linear-gradient(180deg, ${color}f0, ${color}cc)`,
                        borderLeft: cutStart ? "3px dotted rgba(255,255,255,.85)" : "3px solid rgba(0,0,0,.18)",
                        borderRight: cutEnd ? "3px dotted rgba(255,255,255,.85)" : undefined,
                      }}
                      title={`${p.vessel} · ${lengthLabel(p)} · ${parseLocal(p.starts_at).toLocaleString("ru-RU")}`}
                    >
                      <span className="block truncate">{p.vessel}</span>
                      {height >= 22 ? (
                        <span className="block truncate text-[8px] opacity-85">
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

      <p className="mt-1 text-[9px]">
        Пунктирный край — постановка продолжается за границей кадра. Лист собран по тому, что
        было видно в окне сетки: {daySlice.length} сут из {days.length} и {shown.length} причалов.
      </p>
    </div>
  );
}
