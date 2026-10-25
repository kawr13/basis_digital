import { useEffect, useRef } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../auth";
import { roleLabel } from "../format";
import { ageLabel, type Live } from "../live";
import { hiddenSections, resolveVesselRights, usePublicSettings } from "../settings";

// `section` — id раздела в настройке ui.hidden_sections: админ может прятать
// целые разделы для всех, вкладка и маршрут закрываются одновременно.
const tabs = [
  { to: "/", label: "Статистика", end: true, nav: "summary", section: "statistics" },
  { to: "/warehouses", label: "Склады 1С", end: true, nav: "warehouses", section: "warehouses" },
  { to: "/reports", label: "Отчёты", end: false, nav: "reports", section: "reports" },
];

// Вкладка постановки судов показывается только тем ролям, которым разрешён
// просмотр плана (настройка vessels.view_roles) — маршрут закрыт и на бэкенде.
const VESSELS_TAB = { to: "/vessels", label: "Постановка", end: false, nav: "vessels", section: "vessels" };

// Вкладка администратора видна только роли admin: маршрут /admin всё равно
// закрыт редиректом, но лишний раз подсказывать про доступ не стоит.
const ADMIN_TAB = { to: "/admin", label: "Администрирование", end: true, nav: "admin" };

export function AppShell({
  children,
  asOf,
  scopeLabel,
  actions,
  live,
  wide,
  busy,
}: {
  children: React.ReactNode;
  asOf?: string;
  scopeLabel?: string;
  actions?: React.ReactNode;
  /** возраст данных из useLive: «обновлено N мин назад» + бейдж устаревания */
  live?: Live;
  /** на всю ширину окна: план постановки читают поперёк, 1500px ему тесно */
  wide?: boolean;
  /** догружается чанк раздела: чип держит высоту второй строки шапки, иначе */
  /** приехавшая страница подтягивает контент на ~30px вверх */
  busy?: boolean;
}) {
  const { user, logout } = useAuth();
  const settings = usePublicSettings();
  const canSeeVessels = resolveVesselRights(user, settings).view;
  const hidden = hiddenSections(settings);
  const navTabs = [
    ...tabs.filter((t) => !hidden.has(t.section)),
    ...(canSeeVessels && !hidden.has(VESSELS_TAB.section) ? [VESSELS_TAB] : []),
    // Админка всегда остаётся: спрятать её от админа нельзя (и незачем),
    // иначе настройки, которые её прячут, будет некому вернуть.
    ...(user?.role === "admin" ? [ADMIN_TAB] : []),
  ];
  const headerRef = useRef<HTMLElement>(null);

  // Высоту шапки нельзя зашить числом: логотип, перенос навигации на узких
  // экранах и длина ФИО её меняют. Публикуем её в CSS-переменной — панель
  // детализации и всё остальное позиционируются от реального низа шапки.
  useEffect(() => {
    const el = headerRef.current;
    if (!el) return;
    const publish = () =>
      document.documentElement.style.setProperty("--header-h", `${Math.round(el.getBoundingClientRect().height)}px`);
    publish();
    const ro = new ResizeObserver(publish);
    ro.observe(el);
    window.addEventListener("resize", publish);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", publish);
    };
  }, []);

  return (
    <div className="min-h-full">
      {/* Шапка — матовое стекло в фирменном navy (.glass-navy): под ней прокручивается
          контент, и blur(16px) размывает его в мягкое navy-марево. Текст белый —
          контраст 11.5:1, снизу тонкая голубая рамка brand-sky. */}
      <header ref={headerRef} className="glass-navy sticky top-0 z-20 text-white">
        <div className={`mx-auto ${wide ? "max-w-none px-3" : "max-w-[1500px] px-6"}`}>
          {/* flex-wrap: на узком экране навигация и блок пользователя перестают
              влезать в одну строку — без переноса страница уезжает в горизонт */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-3">
            <div className="flex items-center gap-3">
              {/* Фирменный знак БазисПорта (public/logo-light.svg): в navy-шапке
                  обычный логотип не годится — его тёмно-синие пути #063058 на #1b3360
                  сливаются, поэтому для тёмных фонов лежит перекрашенная версия. */}
              <img
                src="/logo-light.svg"
                alt="БазисПорт"
                width={230}
                height={35}
                className="h-8 w-auto shrink-0"
              />
              <span className="hidden sm:block pl-3 border-l border-white/25 text-[11px] tracking-[0.18em] text-white/75">
                Basis.Indicators
              </span>
            </div>

            <nav className="ml-6 flex flex-wrap items-center gap-1">
              {navTabs.map((t) => (
                <NavLink
                  key={t.to}
                  to={t.to}
                  end={t.end}
                  data-nav={t.nav}
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
            {/* Заполнитель строки, пока догружается чанк раздела (см. PageSkeleton). */}
            {/* Декорация: озвучивает состояние сама плашка скелона, поэтому aria-hidden. */}
            {busy && (
              <span className="chip bg-white/10 border border-white/15 text-white/70" aria-hidden="true">
                Загрузка раздела
              </span>
            )}
            {asOf && (
              <span
                className="chip bg-white/10 border border-white/15 text-white/85"
                data-live={live?.ready ? "on" : undefined}
                data-live-age={live?.ageSeconds != null ? String(live.ageSeconds) : undefined}
                data-stale={live?.stale ? "1" : undefined}
              >
                данные 1С на <span className="num ml-1 text-white">{asOf}</span>
                {live?.ageSeconds != null && (
                  <span className="ml-1 text-white/85">· обновлено {ageLabel(live.ageSeconds)}</span>
                )}
              </span>
            )}
            {live?.stale && (
              // честно предупреждаем: цифры могли устареть, 1С давно ничего не присылала
              <span
                className="chip bg-warn/25 border border-warn/40 text-white"
                title={`последняя загрузка из 1С: ${live.lastSyncAt ? new Date(live.lastSyncAt).toLocaleString("ru-RU") : "не известна"}`}
                data-stale="1"
              >
                данные устарели
              </span>
            )}
            {scopeLabel && <span className="chip bg-white/10 border border-white/15 text-white/70">{scopeLabel}</span>}
          </div>
        </div>
      </header>

      <main className={`mx-auto ${wide ? "max-w-none px-3" : "max-w-[1500px] px-6"} py-6 space-y-5`}>{children}</main>
    </div>
  );
}
