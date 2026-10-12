import { useRef, useState } from "react";
import type { WidgetState } from "../api";

export type WidgetDef = {
  id: string;
  title: string;
  hint?: string;
  /** ширина в 12-колоночной сетке: 1..12 */
  defaultSpan: number;
  /** высота тела виджета в px; undefined = по содержимому */
  defaultHeight?: number;
  render: (ctx: { height?: number }) => React.ReactNode;
};

export const SPANS = [
  { v: 3, label: "¼" },
  { v: 4, label: "⅓" },
  { v: 6, label: "½" },
  { v: 8, label: "⅔" },
  { v: 12, label: "во всю" },
] as const;

const GAP = 16; // gap-4 в сетке
const MIN_H = 120;
const MAX_H = 900;
const MIN_W = 120; // минимальная ширина виджета, чтобы таблица не сложилась в столбик

type ResizeMode = "w" | "h" | "both";

/**
 * Склеиваем сохранённую раскладку с набором виджетов текущей версии:
 * новые виджеты доезжают в конец, удалённые — исчезают, порядок и размеры сохраняются.
 */
export function mergeLayout(defs: WidgetDef[], saved?: WidgetState[] | null): WidgetState[] {
  const savedMap = new Map((saved ?? []).map((w) => [w.id, w]));
  const known = new Set(defs.map((d) => d.id));
  const order = (saved ?? []).filter((w) => known.has(w.id)).map((w) => w.id);

  const states = defs.map((d) => {
    const prev = savedMap.get(d.id);
    const span = prev && prev.span >= 1 && prev.span <= 12 ? prev.span : d.defaultSpan;
    const rawH = prev?.h ?? d.defaultHeight;
    const h = rawH ? Math.min(Math.max(rawH, MIN_H), MAX_H) : undefined;
    return { id: d.id, span, visible: prev ? prev.visible !== false : true, h };
  });

  return states.sort((a, b) => {
    const ia = order.indexOf(a.id);
    const ib = order.indexOf(b.id);
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
  });
}

function move(list: WidgetState[], dragId: string, overId: string): WidgetState[] {
  const from = list.findIndex((w) => w.id === dragId);
  const to = list.findIndex((w) => w.id === overId);
  if (from < 0 || to < 0 || from === to) return list;
  const next = [...list];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

function shift(list: WidgetState[], id: string, delta: number): WidgetState[] {
  const i = list.findIndex((w) => w.id === id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= list.length) return list;
  const next = [...list];
  const [item] = next.splice(i, 1);
  next.splice(j, 0, item);
  return next;
}

export function WidgetGrid({
  defs,
  state,
  edit,
  onChange,
}: {
  defs: WidgetDef[];
  state: WidgetState[];
  edit: boolean;
  onChange: (next: WidgetState[]) => void;
}) {
  const gridRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{
    id: string;
    mode: ResizeMode;
    x: number;
    y: number;
    span: number;
    h: number;
    colStep: number;
    minSpan: number;
  } | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [badge, setBadge] = useState<string | null>(null);

  const byId = new Map(defs.map((d) => [d.id, d]));
  const visible = state.filter((w) => w.visible && byId.has(w.id));
  const hidden = state.filter((w) => !w.visible && byId.has(w.id));

  const patch = (id: string, next: Partial<WidgetState>) =>
    onChange(state.map((w) => (w.id === id ? { ...w, ...next } : w)));

  function startResize(e: React.PointerEvent, w: WidgetState, mode: ResizeMode) {
    e.preventDefault();
    e.stopPropagation();
    const grid = gridRef.current?.getBoundingClientRect();
    if (!grid) return;
    const colStep = (grid.width + GAP) / 12;
    const el = (e.currentTarget as HTMLElement).closest("section");
    const currentH = el?.querySelector<HTMLElement>(".widget-body")?.offsetHeight ?? w.h ?? 240;
    drag.current = {
      id: w.id,
      mode,
      x: e.clientX,
      y: e.clientY,
      span: w.span,
      h: currentH,
      colStep,
      minSpan: Math.max(1, Math.ceil((MIN_W * 12) / grid.width)),
    };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    setDragId(w.id);
  }

  function onResizeMove(e: React.PointerEvent) {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    const next: Partial<WidgetState> = {};

    if (d.mode === "w" || d.mode === "both") {
      const span = Math.min(12, Math.max(d.minSpan, d.span + Math.round(dx / d.colStep)));
      if (span !== undefined) next.span = span;
    }
    if (d.mode === "h" || d.mode === "both") {
      next.h = Math.min(MAX_H, Math.max(MIN_H, d.h + dy));
    }
    if (next.span === undefined && next.h === undefined) return;

    patch(d.id, next);
    setBadge(
      `${next.span ?? state.find((s) => s.id === d.id)?.span ?? d.span} из 12 · ${Math.round(
        next.h ?? d.h,
      )} px`,
    );
  }

  function endResize(e: React.PointerEvent) {
    if (!drag.current) return;
    (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
    drag.current = null;
    setDragId(null);
    setBadge(null);
  }

  return (
    <>
      {badge && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-30 chip bg-amber2 text-ink-100 num px-3 py-1.5 shadow-panel">
          {badge}
        </div>
      )}

      <div ref={gridRef} className="grid grid-cols-12 gap-4 widget-grid">
        {visible.map((w) => {
          const def = byId.get(w.id)!;
          return (
            <section
              key={w.id}
              data-widget={w.id}
              data-span={w.span}
              className={`panel p-4 relative ${edit ? "border-dashed border-ink-500" : ""} ${
                dragId === w.id ? "opacity-60" : ""
              }`}
              style={{ gridColumn: `span ${w.span}` }}
              onDragEnter={() => dragId && dragId !== w.id && onChange(move(state, dragId, w.id))}
              onDragOver={(e) => edit && e.preventDefault()}
            >
              <div
                data-handle={w.id}
                className="flex items-baseline justify-between gap-3"
                draggable={edit}
                onDragStart={() => setDragId(w.id)}
                onDragEnd={() => setDragId(null)}
                onDoubleClick={() =>
                  edit && patch(w.id, { span: def.defaultSpan, h: def.defaultHeight })
                }
                title={edit ? "двойной клик — вернуть размер" : undefined}
              >
                <h2 className="panel-title flex items-center gap-2">
                  {edit && <span className="text-ink-300 cursor-grab select-none">⠿</span>}
                  {def.title}
                </h2>

                {edit ? (
                  <div className="flex items-center gap-1">
                    <button className="chip bg-ink-800 border border-ink-700 text-ink-300 hover:text-ink-100" title="левее"
                      onClick={() => onChange(shift(state, w.id, -1))}>←</button>
                    <button className="chip bg-ink-800 border border-ink-700 text-ink-300 hover:text-ink-100" title="правее"
                      onClick={() => onChange(shift(state, w.id, 1))}>→</button>
                    <span className="mx-1 h-4 w-px bg-ink-600" />
                    {SPANS.map((s) => (
                      <button
                        key={s.v}
                        title={`ширина ${s.label}`}
                        onClick={() => patch(w.id, { span: s.v })}
                        className={`chip ${w.span === s.v ? "bg-amber2/20 text-amber2" : "bg-ink-800 border border-ink-700 text-ink-300 hover:text-ink-100"}`}
                      >
                        {s.label}
                      </button>
                    ))}
                    <span className="mx-1 h-4 w-px bg-ink-600" />
                    <button
                      className="chip bg-ink-800 border border-ink-700 text-ink-300 hover:text-crit"
                      title="убрать с дашборда"
                      onClick={() => patch(w.id, { visible: false })}
                    >
                      скрыть
                    </button>
                  </div>
                ) : (
                  def.hint && <span className="text-[11px] text-ink-300 num">{def.hint}</span>
                )}
              </div>

              <div
                className="mt-3 widget-body"
                style={w.h ? { height: w.h, overflowY: "auto" } : undefined}
              >
                {def.render({ height: w.h })}
              </div>

              {edit && (
                <>
                  <button
                    type="button"
                    data-resize="w"
                    className="rz rz-e"
                    title="потяни — ширина"
                    onPointerDown={(e) => startResize(e, w, "w")}
                    onPointerMove={onResizeMove}
                    onPointerUp={endResize}
                    onPointerCancel={endResize}
                  />
                  <button
                    type="button"
                    data-resize="h"
                    className="rz rz-s"
                    title="потяни — высота"
                    onPointerDown={(e) => startResize(e, w, "h")}
                    onPointerMove={onResizeMove}
                    onPointerUp={endResize}
                    onPointerCancel={endResize}
                  />
                  <button
                    type="button"
                    data-resize="both"
                    className="rz rz-se"
                    title="потяни — ширина и высота"
                    onPointerDown={(e) => startResize(e, w, "both")}
                    onPointerMove={onResizeMove}
                    onPointerUp={endResize}
                    onPointerCancel={endResize}
                  />
                </>
              )}
            </section>
          );
        })}
      </div>

      {edit && hidden.length > 0 && (
        <div className="panel p-4">
          <h2 className="panel-title">Скрытые блоки · вернуть на дашборд</h2>
          <div className="mt-3 flex flex-wrap gap-2">
            {hidden.map((w) => (
              <button
                key={w.id}
                className="chip bg-ink-800 border border-ink-700 text-ink-300 hover:text-amber2"
                onClick={() => patch(w.id, { visible: true })}
              >
                + {byId.get(w.id)!.title}
              </button>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
