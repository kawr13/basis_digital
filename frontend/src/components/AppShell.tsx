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
      {/* Шапка — ГЛУХОЙ фирменный navy (#1b3360), без стекла и blur: строгая деловая
          шапка. Снизу тонкая голубая рамка brand-sky — единственный «светлый» акцент. */}
      <header className="sticky top-0 z-20 bg-brand-navy text-white border-b border-brand-sky/30">
        <div className="mx-auto max-w-[1500px] px-6">
          <div className="flex items-center gap-4 pt-3">
            <div>
              <div className="text-[11px] uppercase tracking-[0.18em] text-white/60">БазисПорт</div>
              <div className="text-sm font-medium text-white">Кабинет веб-статистики</div>
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
                        ? // активная вкладка: белая пилюля на navy + голубая нижняя черта
                          "bg-white/10 text-white shadow-[inset_0_-2px_0_0_#2fbaec]"
                        : "text-white/70 hover:text-white hover:bg-white/[0.06]"
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
                <div className="text-sm text-white">{user?.full_name}</div>
                <div className="text-[11px] text-white/70">{roleLabel[user?.role ?? ""]}</div>
              </div>
              <button className="btn-ghost text-xs" onClick={logout}>
                Выйти
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2 py-2 text-xs text-white/70">
            {asOf && (
              <span className="chip bg-white/10 border border-white/15 text-white/70">
                данные 1С на <span className="num ml-1 text-white">{asOf}</span>
              </span>
            )}
            {scopeLabel && <span className="chip bg-white/10 border border-white/15 text-white/70">{scopeLabel}</span>}
          </div>
        </div>
      </header>

      <main className={`mx-auto max-w-[1500px] px-6 py-6 space-y-5 ${contentClassName ?? ""}`}>{children}</main>
    </div>
  );
}
