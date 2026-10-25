/**
 * Мелкие переиспользуемые элементы страницы администрирования.
 *
 * Здесь нет бизнес-логики: только разметка (модалка, поля формы, статусы,
 * состояния «загрузка / пуст / ошибка»), одинаковая для всех вкладок админки.
 * Классы сняты с ReportsPage — админка должна выглядеть как остальной кабинет.
 * (модалка затемняет подложку rgba(15,27,45,.35), а стекло даёт сам .panel).
 *
 * Два неочевидных момента, без которых окно «не влазит в экран»:
 * 1) портал в document.body. Карточка админки — это .panel с backdrop-filter,
 *    а backdrop-filter превращает карточку в containing block для position:fixed:
 *    без портала модалка запиралась внутри карточки (310px вместо всего экрана)
 *    и обрастала собственными ползунками прокрутки;
 * 2) окно — flex-колонка с прокруткой только в середине: шапка и кнопки формы
 *    остаются на месте даже на низком экране (ноутбук ~525px по высоте).
 */
import { useEffect } from "react";
import { createPortal } from "react-dom";

export type Tone = "ok" | "warn" | "crit" | "muted";

/** Текст ошибки из ApiError/Error: у валидации бэкенда он и так по-русски. */
export function errText(e: unknown, fallback = "Операция не удалась"): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/**
 * Модальное окно формы. Esc и клик по подложке закрывают, как в DrilldownPanel.
 * Рендерится порталом в <body> (см. шапку файла) — иначе position:fixed
 * считается не от экрана, а от карточки-родителя с backdrop-filter.
 */
export function Modal({
  title,
  onClose,
  children,
  footer,
  testId,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  /** нижняя панель с кнопками: не уезжает при прокрутке тела формы */
  footer?: React.ReactNode;
  /** stable value for data-modal — используется браузерными проверками */
  testId?: string;
  wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Пока окно открыто — страницу за ним не крутим: два ползунка рядом
  // (в окне и за окном) читаются как сломанный интерфейс
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  return createPortal(
    <div
      className="fixed inset-0 z-40 overflow-y-auto bg-[rgba(15,27,45,.35)] p-4"
      onClick={onClose}
      data-modal-overlay
    >
      {/* min-h-full + items-center: окно по центру, пока влезает, и обычная
          прокрутка подложки, когда экран ниже самой формы */}
      <div className="flex min-h-full items-center justify-center">
        {/* сама модалка — .panel, а значит уже матовое стекло: blur(20px)
            размывает затемнённую подложку и страницу за ней */}
        <div
          className={`panel dd-panel relative flex w-full max-h-[calc(100vh-2rem)] flex-col ${
            wide ? "max-w-5xl" : "max-w-2xl"
          } p-0`}
          role="dialog"
          aria-modal="true"
          aria-label={title}
          data-modal={testId}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex shrink-0 items-center justify-between gap-3 border-b border-ink-700 px-4 py-3">
            <h2 className="panel-title">{title}</h2>
            <button className="btn-ghost text-xs" onClick={onClose} data-admin-action="close-modal">
              Закрыть
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-4" data-modal-body>
            {children}
          </div>

          {footer && (
            <div className="shrink-0 border-t border-ink-700 px-4 py-3" data-modal-footer>
              {footer}
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Подпись поля + подсказка. `changed` подсвечивает поле, если значение правили. */
export function Field({
  label,
  hint,
  children,
  changed,
  className,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
  changed?: boolean;
  className?: string;
}) {
  return (
    <label className={`block ${changed ? "rounded-md bg-amber2/10 ring-1 ring-amber2/50 p-2" : ""} ${className ?? ""}`}>
      <span className="panel-title mb-1 block">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] leading-snug text-ink-300">{hint}</span>}
    </label>
  );
}

export function FormError({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div role="alert" className="rounded-md border border-crit/40 bg-crit/5 px-3 py-2 text-sm text-crit" data-form-error>
      {text}
    </div>
  );
}

export function Notice({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div className="rounded-md border border-ok/40 bg-ok/5 px-3 py-2 text-sm text-ok" data-notice>
      {text}
    </div>
  );
}

export function InfoNote({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div className="rounded-md border border-accent/30 bg-accent/5 px-3 py-2 text-sm text-accent" data-info>
      {text}
    </div>
  );
}

export function BusyNote({ text = "загрузка…" }: { text?: string }) {
  return <span className="text-xs text-ink-300">{text}</span>;
}

/** Пустая строка таблицы: без неё «пусто» выглядит как сломанный экран. */
export function EmptyRow({ cols, text }: { cols: number; text: string }) {
  return (
    <tr>
      <td colSpan={cols} className="py-6 text-center text-ink-300">
        {text}
      </td>
    </tr>
  );
}

export function StatusChip({ tone, text }: { tone: Tone; text: string }) {
  const cls =
    tone === "ok"
      ? "bg-ok/10 text-ok"
      : tone === "warn"
        ? "bg-warn/10 text-warn"
        : tone === "crit"
          ? "bg-crit/10 text-crit"
          : "bg-ink-800 text-ink-300 border border-ink-600";
  return <span className={`chip ${cls}`}>{text}</span>;
}

/** Текстовая кнопка действия в строке таблицы. */
export function LinkBtn({
  action,
  onClick,
  children,
  tone = "accent",
  disabled,
  title,
}: {
  action: string;
  onClick: () => void;
  children: React.ReactNode;
  tone?: "accent" | "crit" | "muted";
  disabled?: boolean;
  title?: string;
}) {
  const cls = tone === "crit" ? "text-crit" : tone === "muted" ? "text-ink-300" : "text-accent";
  return (
    <button
      className={`${cls} hover:underline disabled:opacity-40`}
      onClick={onClick}
      disabled={disabled}
      title={title}
      data-admin-action={action}
    >
      {children}
    </button>
  );
}

/**
 * Двухшаговое подтверждение прямо в строке: window.confirm вложен в браузер
 * и плохо читается тестом, а «отключить пользователя» — действие необратимое
 * на глаз, его надо подтверждать явно.
 */
export function ConfirmInline({
  action,
  text,
  onYes,
  onNo,
}: {
  action: string;
  text: string;
  onYes: () => void;
  onNo: () => void;
}) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="text-[11px] text-warn">{text}</span>
      <button className="text-xs text-crit hover:underline" onClick={onYes} data-admin-action={action}>
        Да
      </button>
      <button className="text-xs text-ink-300 hover:underline" onClick={onNo} data-admin-action="cancel">
        Нет
      </button>
    </span>
  );
}

/** Шапка таблицы в стиле остальных плашек кабинета. */
export function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return <th className={`py-2 pr-3 font-medium ${right ? "text-right" : ""}`}>{children}</th>;
}

export const THEAD_ROW = "text-left text-[11px] uppercase tracking-wider text-ink-400 bg-ink-800";
export const TR = "border-t border-ink-700 hover:bg-ink-800";
