/**
 * Оверлей стрелок перешвартовок на «Постановке судов».
 *
 * Вынесен из VesselsPage отдельным файлом по той же причине, что и геометрия в
 * `vesselArrows.ts`: у стрелки теперь четыре части (путь, кружок «откуда»,
 * наконечник «куда», подпись) и три состояния — обычная, своя при наведении,
 * чужая на фоне. Со всем этим в файле сетки он стал бы нечитаемым.
 *
 * Компонент ничего не знает о данных: наружу — только список стрелок с
 * посчитанными координатами. Рисует в пикселях холста `[data-vp-print]`, лежит
 * поверх полос и `pointer-events-none`, чтобы не съедать перетаскивание.
 */
import type { ArrowLabel } from "../vesselArrows";

/** Одна стрелка в том виде, в каком её нарисовали. */
export type ArrowView = {
  /** id копии, к которой ведёт стрелка (у «призрака» под курсором — -1) */
  id: number;
  /** id оригинала — чтобы подсвечивать и полосу, откуда стрелка стартует */
  srcId?: number;
  d: string;
  startX: number;
  startY: number;
  tipX: number;
  tipY: number;
  /** куда смотрит стрелка по горизонтали — от этого зависит, какой стороной класть подпись */
  rightward: boolean;
  color: string;
  /** «шаг 1→2 · РП-6 · 02.11 12:00»; у призрака пустая — ей ещё некуда вести */
  caption: string;
  label: ArrowLabel;
  /** призрак перешвартовки под курсором: пунктир, без подписи */
  draft?: boolean;
  /**
   * base — никто не наведён; focus — стрелка принадлежит цепочке судна под
   * курсором (или в панели деталей); dim — чужая цепочка, уходит на фон.
   */
  tone?: "base" | "focus" | "dim";
};

/** Длина и высота наконечника в px: раньше был 9 px и терялся на сетке. */
const HEAD = 11;

const TONE: Record<NonNullable<ArrowView["tone"]>, { width: number; opacity: number }> = {
  base: { width: 2.5, opacity: 0.9 },
  focus: { width: 3, opacity: 1 },
  dim: { width: 1.5, opacity: 0.15 },
};

export function VesselsArrows({
  arrows,
  width,
  height,
}: {
  arrows: ArrowView[];
  width: number;
  height: number;
}) {
  const colors = [...new Set(arrows.map((a) => a.color))];
  return (
    <svg
      className="absolute left-0 top-0 z-[15] pointer-events-none"
      width={width}
      height={height}
      data-vp-arrows
      aria-hidden="true"
    >
      <defs>
        {/* наконечники — того же цвета, что полоса судна: стрелка читается как
            продолжение конкретной постановки, а не как «ещё одна линия» */}
        {colors.map((c) => (
          <marker
            key={c}
            id={`vp-arrow-${c.slice(1)}`}
            markerWidth={HEAD}
            markerHeight={HEAD}
            refX={HEAD - 3}
            refY={HEAD / 2}
            orient="auto"
            markerUnits="userSpaceOnUse"
            viewBox={`0 0 ${HEAD} ${HEAD}`}
          >
            <path d={`M0 0 L${HEAD} ${HEAD / 2} L0 ${HEAD} Z`} fill={c} />
          </marker>
        ))}
      </defs>
      {arrows.map((ar) => {
        const tone = TONE[ar.tone ?? "base"];
        return (
          <g
            key={ar.id}
            // opacity на группе, а не на пути: затемнять надо и линию, и
            // наконечник, и подпись — иначе «чужая» стрелка остаётся с яркой
            // подписью и всё равно спорит с той, на которую смотрят
            opacity={ar.draft ? 0.65 : tone.opacity}
            data-arrow={ar.id}
            data-arrow-tone={ar.draft ? "draft" : (ar.tone ?? "base")}
          >
            <path
              d={ar.d}
              fill="none"
              stroke={ar.color}
              strokeWidth={ar.draft ? 2 : tone.width}
              strokeLinejoin="round"
              strokeLinecap="round"
              markerEnd={`url(#vp-arrow-${ar.color.slice(1)})`}
              strokeDasharray={ar.draft ? "6 5" : undefined}
            />
            {/* кружок «откуда»: направление читается даже без наконечника —
                на напечатанной странице и в чёрно-белой копии */}
            <circle cx={ar.startX} cy={ar.startY} r={3} fill={ar.color} />
            {ar.caption ? (
              <text
                x={ar.label.x}
                y={ar.label.y}
                textAnchor={ar.label.anchor}
                fontSize={10.5}
                fontWeight={600}
                fill={ar.color}
                stroke="#fff"
                strokeWidth={3}
                // белая обводка вокруг знаков, а не белая плашка под строкой:
                // подпись живёт поверх сетки, серых выходных и чужих полос и
                // обязана читаться, не затирая то, что под ней
                style={{ paintOrder: "stroke", strokeLinejoin: "round" }}
                data-arrow-caption={ar.id}
              >
                {ar.caption}
              </text>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}
