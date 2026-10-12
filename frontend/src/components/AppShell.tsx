import { NavLink } from "react-router-dom";
import { useAuth } from "../auth";
import { roleLabel } from "../format";

const tabs = [
  { to: "/", label: "Сводка", end: true },
  { to: "/reports", label: "Отчёты", end: false },
];

export function AppShell({
  children,
  asOf,
  scopeLabel,
  actions,
  contentClassName,
}: {
  children: React.ReactNode;
  asOf?: string;
  scopeLabel?: string;
  actions?: React.ReactNode;
  /** дополнительные классы для <main>: напр. отступ под фиксированную панель деталей */
  contentClassName?: string;
}) {
  const { user, logout } = useAuth();

  return (
    <div className="min-h-full">
      {/* Шапка — «плотное стекло»: полупрозрачный фирменный navy + blur, снизу тонкая
          голубая рамка (цвет ховера навигации cab.basisport.ru). */}
      <header className="glass-strong sticky top-0 z-20 border-b-[rgba(47,186,236,.25)]">
        <div className="mx-auto max-w-[1500px] px-6">
          <div className="flex items-center gap-4 pt-3">
            <div>
              <div className="text-[11px] uppercase tracking-[0.18em] text-ink-300">БазисПорт</div>
              <div className="text-sm font-medium">Кабинет веб-статистики</div>
            </div>

            <nav className="ml-6 flex items-center gap-1">
              {tabs.map((t) => (
                <NavLink
                  key={t.to}
                  to={t.to}
                  end={t.end}
                  className={({ isActive }) =>
                    `rounded-md px-3 py-1.5 text-sm transition ring-1 ${
                      isActive
                        ? // активная вкладка — фирменный голубой accent: текст и обводка
                          "bg-ink-900/55 text-accent ring-accent/45"
                        : "text-ink-300 ring-transparent hover:text-ink-100 hover:bg-white/5"
                    }`
                  }
                >
                  {t.label}
                </NavLink>
              ))}
            </nav>

            <div className="ml-auto flex items-center gap-3">
              {actions}
              <div className="text-right leading-tight">
                <div className="text-sm">{user?.full_name}</div>
                <div className="text-[11px] text-ink-300">{roleLabel[user?.role ?? ""]}</div>
              </div>
              <button className="btn-ghost text-xs" onClick={logout}>
                Выйти
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2 py-2 text-xs text-ink-300">
            {asOf && (
              <span className="chip bg-white/5 border border-white/10 text-ink-300">
                данные 1С на <span className="num ml-1 text-ink-100">{asOf}</span>
              </span>
            )}
            {scopeLabel && <span className="chip bg-white/5 border border-white/10 text-ink-300">{scopeLabel}</span>}
          </div>
        </div>
      </header>

      <main className={`mx-auto max-w-[1500px] px-6 py-6 space-y-5 ${contentClassName ?? ""}`}>{children}</main>
    </div>
  );
}
