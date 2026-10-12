import { useState } from "react";
import type { WidgetState } from "../api";

export type WidgetDef = {
  id: string;
  title: string;
  hint?: string;
  /** ширина в 12-колоночной сетке: 3, 4, 6, 8 или 12 */
  defaultSpan: number;
  render: () => React.ReactNode;
};

export const SPANS = [
  { v: 3, label: "¼" },
  { v: 4, label: "⅓" },
  { v: 6, label: "½" },
  { v: 8, label: "⅔" },
  { v: 12, label: "во всю" },
] as const;

/**
 * Склеиваем сохранённую раскладку с набором виджетов текущей версии:
 * новые виджеты доезжают в конец, удалённые — исчезают, порядок сохраняется.
 */
export function mergeLayout(defs: WidgetDef[], saved?: WidgetState[] | null): WidgetState[] {
  const savedMap = new Map((saved ?? []).map((w) => [w.id, w]));
  const known = new Set(defs.map((d) => d.id));
  const order = (saved ?? []).filter((w) => known.has(w.id)).map((w) => w.id);

  const states = defs.map((d) => {
    const prev = savedMap.get(d.id);
    const span = prev && SPANS.some((s) => s.v === prev.span) ? prev.span : d.defaultSpan;
    return { id: d.id, span, visible: prev ? prev.visible !== false : true };
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
  const [dragId, setDragId] = useState<string | null>(null);
  const byId = new Map(defs.map((d) => [d.id, d]));
  const visible = state.filter((w) => w.visible && byId.has(w.id));
  const hidden = state.filter((w) => !w.visible && byId.has(w.id));

  return (
    <>
      <div className="grid grid-cols-12 gap-4 widget-grid">
        {visible.map((w) => {
          const def = byId.get(w.id)!;
          return (
            <section
              key={w.id}
              data-widget={w.id}
              className={`panel p-4 relative ${edit ? "border-dashed border-ink-500" : ""} ${
                dragId === w.id ? "opacity-40" : ""
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
              >
                <h2 className="panel-title flex items-center gap-2">
                  {edit && <span className="text-ink-400 cursor-grab select-none">⠿</span>}
                  {def.title}
                </h2>

                {edit ? (
                  <div className="flex items-center gap-1">
                    <button className="chip bg-ink-700 text-ink-300 hover:text-ink-100" title="левее"
                      onClick={() => onChange(shift(state, w.id, -1))}>←</button>
                    <button className="chip bg-ink-700 text-ink-300 hover:text-ink-100" title="правее"
                      onClick={() => onChange(shift(state, w.id, 1))}>→</button>
                    <span className="mx-1 h-4 w-px bg-ink-600" />
                    {SPANS.map((s) => (
                      <button
                        key={s.v}
                        title={`ширина ${s.label}`}
                        onClick={() =>
                          onChange(state.map((x) => (x.id === w.id ? { ...x, span: s.v } : x)))
                        }
                        className={`chip ${w.span === s.v ? "bg-amber2/20 text-amber2" : "bg-ink-700 text-ink-400 hover:text-ink-100"}`}
                      >
                        {s.label}
                      </button>
                    ))}
                    <span className="mx-1 h-4 w-px bg-ink-600" />
                    <button
                      className="chip bg-ink-700 text-ink-400 hover:text-crit"
                      title="убрать с дашборда"
                      onClick={() => onChange(state.map((x) => (x.id === w.id ? { ...x, visible: false } : x)))}
                    >
                      скрыть
                    </button>
                  </div>
                ) : (
                  def.hint && <span className="text-[11px] text-ink-400 num">{def.hint}</span>
                )}
              </div>

              <div className="mt-3">{def.render()}</div>
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
                className="chip bg-ink-700 text-ink-300 hover:text-amber2"
                onClick={() => onChange(state.map((x) => (x.id === w.id ? { ...x, visible: true } : x)))}
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
