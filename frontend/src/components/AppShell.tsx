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
}: {
  children: React.ReactNode;
  asOf?: string;
  scopeLabel?: string;
  actions?: React.ReactNode;
}) {
  const { user, logout } = useAuth();

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-20 bg-ink-800/95 backdrop-blur border-b border-ink-600">
        <div className="mx-auto max-w-[1500px] px-6">
          <div className="flex items-center gap-4 pt-3">
            <div>
              <div className="text-[11px] uppercase tracking-[0.18em] text-ink-400">БазисПорт</div>
              <div className="text-sm font-medium">Кабинет веб-статистики</div>
            </div>

            <nav className="ml-6 flex items-center gap-1">
              {tabs.map((t) => (
                <NavLink
                  key={t.to}
                  to={t.to}
                  end={t.end}
                  className={({ isActive }) =>
                    `rounded-md px-3 py-1.5 text-sm transition ${
                      isActive
                        ? "bg-amber2/15 text-amber2"
                        : "text-ink-300 hover:text-ink-100 hover:bg-ink-700"
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
                <div className="text-[11px] text-ink-400">{roleLabel[user?.role ?? ""]}</div>
              </div>
              <button className="btn-ghost text-xs" onClick={logout}>
                Выйти
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2 py-2 text-xs text-ink-400">
            {asOf && (
              <span className="chip bg-ink-700 text-ink-300">
                данные 1С на <span className="num ml-1 text-ink-100">{asOf}</span>
              </span>
            )}
            {scopeLabel && <span className="chip bg-ink-700 text-ink-300">{scopeLabel}</span>}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] px-6 py-6 space-y-5">{children}</main>
    </div>
  );
}
