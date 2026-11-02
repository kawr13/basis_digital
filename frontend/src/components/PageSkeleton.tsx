/**
 * Скелон ленивого раздела (TASKS.md, п.7 «Code-splitting»).
 *
 * Почему скелон вместе с шапкой. Страница приезжает отдельным чанком, и если
 * в fallback отдать только «кусок» контента, шапка кабинета с вкладками на
 * время загрузки исчезнет — как раз то мигание, из-за прав на «Постановку»
 * (vessel_rights) и делали запрет. AppShell — статический импорт индексного
 * чанка, поэтому остаётся на месте: вкладки не дёргаются, высота второй строки
 * держится чипом «Загрузка раздела», контент не прыгает.
 *
 * Никаких спиннеров-крутилок: полосы в тех же скруглениях и с той же
 * shadow-panel, что настоящие плашки (bg-ink-700 + animate-pulse — тот же
 * приём, что в DrilldownPanel).
 *
 * Плотности две: `default` — панель инструментов + KPI + широкие плашки
 * («Индикаторы», «Склады 1С», «Постановка»), `table` — панель фильтров и одна
 * плашка со строками («Отчёты», «Администрирование»).
 */
import { AppShell } from "./AppShell";

export type SkeletonVariant = "default" | "table";

/** Полоса-заполнитель: width — доля строки, h — высота в px (как подписи и числа в плашках). */
function Bar({ w, h = 12, className = "" }: { w: string; h?: number; className?: string }) {
  return <div aria-hidden="true" className={`animate-pulse rounded-sm bg-ink-700 ${className}`} style={{ width: w, height: h }} />;
}

function Panel({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <section aria-hidden="true" className={`panel p-4 ${className}`}>
      {children}
    </section>
  );
}

/** Узкая плашка сверху — как панель периода/фильтров на живой странице. */
function Toolbar() {
  return (
    <Panel className="flex flex-wrap items-center gap-2">
      <Bar w="148px" h={12} />
      <span className="ml-auto flex items-center gap-2">
        <Bar w="86px" h={30} className="rounded-md" />
        <Bar w="70px" h={30} className="rounded-md" />
        <Bar w="104px" h={30} className="rounded-md" />
      </span>
    </Panel>
  );
}

/** Обычная плотность: KPI-строка + две широкие плашки (12-колоночная сетка, как WidgetGrid). */
function DefaultBody() {
  return (
    <>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Panel key={i} className="space-y-3">
            <Bar w="92px" h={10} />
            <Bar w="116px" h={26} className="rounded-md" />
            <Bar w="70%" h={10} />
          </Panel>
        ))}
      </div>
      <div className="grid grid-cols-12 gap-4">
        <Panel className="col-span-12 xl:col-span-8 space-y-3">
          <Bar w="180px" h={12} />
          <Bar w="100%" h={232} className="rounded-md" />
        </Panel>
        <Panel className="col-span-12 xl:col-span-4 space-y-3">
          <Bar w="128px" h={12} />
          {[92, 76, 84, 64, 88, 70].map((w, i) => (
            <Bar key={i} w={`${w}%`} h={14} />
          ))}
        </Panel>
      </div>
    </>
  );
}

/** Плотность «таблица»: шапка таблицы и строки — так страница не схлопывается до огрызков. */
function TableBody() {
  const cols = [34, 16, 16, 12, 14];
  return (
    <Panel className="space-y-3">
      <div className="flex items-center gap-4 border-b border-ink-700 pb-3">
        {cols.map((w, i) => (
          <Bar key={i} w={`${w}%`} h={10} />
        ))}
      </div>
      {Array.from({ length: 9 }, (_, r) => (
        <div key={r} className="flex items-center gap-4">
          {cols.map((w, i) => (
            /* первая колонка — «названия строк», её полосы нарочно разной длины */
            <Bar key={i} w={`${i === 0 ? Math.max(20, w - (r % 3) * 6) : w}%`} h={13} />
          ))}
        </div>
      ))}
    </Panel>
  );
}

export function PageSkeleton({
  variant = "default",
  wide = false,
}: {
  variant?: SkeletonVariant;
  /** шапка на всю ширину — у «Статистики» и «Постановки» wide-раскладка */
  wide?: boolean;
}) {
  return (
    <AppShell wide={wide} busy>
      <div className="space-y-5 min-h-[70vh]" aria-busy="true" role="status" data-skeleton={variant}>
        {/* единственная озвучиваемая фраза: остальное — декорация, она скрыта aria-hidden */}
        <span className="sr-only">Загрузка раздела</span>
        <Toolbar />
        {variant === "table" ? <TableBody /> : <DefaultBody />}
      </div>
    </AppShell>
  );
}
