import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { hiddenSections, resolveVesselRights, usePublicSettings } from "./settings";
import DashboardPage from "./pages/DashboardPage";
import StatisticsPage from "./pages/StatisticsPage";
import LoginPage from "./pages/LoginPage";
import ReportsPage from "./pages/ReportsPage";
import VesselsPage from "./pages/VesselsPage";
import AdminPage from "./pages/AdminPage";

function Boot() {
  return (
    <div className="grid h-full place-items-center text-ink-400">
      <div className="animate-pulse text-sm">Загрузка кабинета…</div>
    </div>
  );
}

/** Админка доступна только роли admin: остальным — возврат на сводку. */
function AdminOnly({ children }: { children: React.ReactNode }) {
  const { user, booting } = useAuth();
  if (booting) return <Boot />;
  if (!user || user.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}

/** Раздел, который админ скрыл для всех (ui.hidden_sections): и вкладка,
 *  и маршрут. Пересылаем на первый видимый раздел, а не «в никуда». */
const SECTION_PATHS: Record<string, string> = {
  statistics: "/",
  warehouses: "/warehouses",
  reports: "/reports",
  vessels: "/vessels",
};

function firstVisibleSection(settings: Parameters<typeof hiddenSections>[0]): string {
  const hidden = hiddenSections(settings);
  for (const id of ["statistics", "warehouses", "reports", "vessels"]) {
    if (!hidden.has(id)) return SECTION_PATHS[id];
  }
  return "/";
}

function SectionGuard({ section, children }: { section: string; children: React.ReactNode }) {
  const { user, booting } = useAuth();
  const settings = usePublicSettings();
  if (booting) return <Boot />;
  if (!user) return <Navigate to="/login" replace />;
  if (hiddenSections(settings).has(section)) {
    return <Navigate to={firstVisibleSection(settings)} replace />;
  }
  return <>{children}</>;
}

/** Просмотр плана постановки судов — по настройке vessels.view_roles (как и на бэкенде). */
function VesselsGuard({ children }: { children: React.ReactNode }) {
  const { user, booting } = useAuth();
  const settings = usePublicSettings();
  if (booting) return <Boot />;
  if (!user) return <Navigate to="/login" replace />;
  if (hiddenSections(settings).has("vessels")) return <Navigate to={firstVisibleSection(settings)} replace />;
  if (!resolveVesselRights(user, settings).view) return <Navigate to={firstVisibleSection(settings)} replace />;
  return <>{children}</>;
}

export default function App() {
  const { user, booting } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={booting ? <Boot /> : user ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route
        path="/"
        element={
          <SectionGuard section="statistics">
            <StatisticsPage />
          </SectionGuard>
        }
      />
      {/* Остатки и ёмкость складов из витрины 1С — отдельная страница: до
          согласования контракта с 1С это демо-набор, путать его с реальными
          цифрами отчёта диспетчера нельзя. */}
      <Route
        path="/warehouses"
        element={
          <SectionGuard section="warehouses">
            <DashboardPage />
          </SectionGuard>
        }
      />
      <Route
        path="/reports"
        element={
          <SectionGuard section="reports">
            <ReportsPage />
          </SectionGuard>
        }
      />
      <Route
        path="/vessels"
        element={
          <VesselsGuard>
            <VesselsPage />
          </VesselsGuard>
        }
      />
      <Route
        path="/admin"
        element={
          <AdminOnly>
            <AdminPage />
          </AdminOnly>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
