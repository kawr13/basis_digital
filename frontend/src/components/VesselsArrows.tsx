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
 *
 * Почему линия не «цвет полосы», а её тёмная версия: палитра светлая (у
 * `#ffb020` контраст к белому 1.9:1), а стрелка живёт поверх сетки, серых
 * выходных колонок и залитых полос судов — та же синяя линия на синей полосе
 * МАРИНЫ просто исчезала. Оттенок стрелки обязан остаться оттенком судна (по
 * нему и читают «чьи это стрелки»), поэтому светлоту режет `arrowInk`, а не
 * другая палитра. Лист делает ровно то же (`_ink` в plan_pdf.py).
 */
import type { ArrowLabel } from "../vesselArrows";
import { arrowInk } from "../vessels";

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

/** Длина и высота наконечника в px: 9 px терялся на сетке, 11 px всё ещё
 *  спорил с полосой, за которую цеплялся. */
const HEAD = 13;

/**
 * Толщина и прозрачность. Наведённая обязана быть толще обычной, а не только
 * «на 10% ярче»: на сетке в 12 px на причал разницу в 0,5 px глаз не ловит.
 * Прозрачность «своей» — 1, «чужой» — 0.15: смок сверяет именно эти границы.
 */
const TONE: Record<NonNullable<ArrowView["tone"]>, { width: number; opacity: number }> = {
  base: { width: 3.2, opacity: 1 },
  focus: { width: 4.4, opacity: 1 },
  dim: { width: 1.5, opacity: 0.15 },
};

/** На сколько пикселей белая подложка шире линии. */
const CASING = 3.4;

export function VesselsArrows({
  arrows,
  width,
  height,
}: {
  arrows: ArrowView[];
  width: number;
  height: number;
}) {
  const inks = [...new Set(arrows.map((a) => arrowInk(a.color)))];
  // Белая подложка — одним слоем ПОД все стрелки: у каждой своей подложкой
  // рисовать нельзя, подложка второй затёрла бы линию первой. У «чужих»
  // стрелок подложки нет — она оставила бы белую царапину там, где должен быть
  // только намёк на линию.
  const casings = arrows.filter((a) => (a.tone ?? "base") !== "dim");
  return (
    <svg
      className="absolute left-0 top-0 z-[15] pointer-events-none"
      width={width}
      height={height}
      data-vp-arrows
      aria-hidden="true"
    >
      <defs>
        {/* наконечники — тёмная версия цвета полосы: стрелка читается как
            продолжение конкретной постановки, но не тонет в ней самой */}
        {inks.map((c) => (
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
      <g data-arrow-casings>
        {casings.map((a) => (
          <path
            key={`casing-${a.id}`}
            d={a.d}
            fill="none"
            stroke="#ffffff"
            strokeWidth={TONE[a.tone ?? "base"].width + CASING}
            strokeLinejoin="round"
            strokeLinecap="round"
            opacity={0.9}
          />
        ))}
      </g>
      {arrows.map((ar) => {
        const tone = TONE[ar.tone ?? "base"];
        const ink = arrowInk(ar.color);
        return (
          <g
            key={ar.id}
            // opacity на группе, а не на пути: затемнять надо и линию, и
            // наконечник, и подпись — иначе «чужая» стрелка остаётся с яркой
            // подписью и всё равно спорит с той, на которую смотрят
            opacity={ar.draft ? 0.75 : tone.opacity}
            data-arrow={ar.id}
            data-arrow-tone={ar.draft ? "draft" : (ar.tone ?? "base")}
          >
            <path
              d={ar.d}
              fill="none"
              stroke={ink}
              strokeWidth={ar.draft ? 2.6 : tone.width}
              strokeLinejoin="round"
              strokeLinecap="round"
              markerEnd={`url(#vp-arrow-${ink.slice(1)})`}
              strokeDasharray={ar.draft ? "7 5" : undefined}
            />
            {/* кружок «откуда»: направление читается даже без наконечника —
                на напечатанной странице и в чёрно-белой копии. Он, наоборот,
                светлый: это не линия, а метка, и белое кольцо отделяет его от
                полосы, на которую он сел */}
            <circle cx={ar.startX} cy={ar.startY} r={4} fill={ar.color} stroke="#ffffff" strokeWidth={1.4} />
            {ar.caption ? (
              <text
                x={ar.label.x}
                y={ar.label.y}
                textAnchor={ar.label.anchor}
                fontSize={11}
                fontWeight={700}
                fill={ink}
                stroke="#fff"
                strokeWidth={3.6}
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
