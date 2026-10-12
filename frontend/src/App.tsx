import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import DashboardPage from "./pages/DashboardPage";
import LoginPage from "./pages/LoginPage";
import ReportsPage from "./pages/ReportsPage";
import AdminPage from "./pages/AdminPage";

function Boot() {
  return (
    <div className="grid h-full place-items-center text-ink-400">
      <div className="animate-pulse text-sm">Загрузка кабинета…</div>
    </div>
  );
}

function Protected({ children }: { children: React.ReactNode }) {
  const { user, booting } = useAuth();
  if (booting) return <Boot />;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

/** Админка доступна только роли admin: остальным — возврат на сводку. */
function AdminOnly({ children }: { children: React.ReactNode }) {
  const { user, booting } = useAuth();
  if (booting) return <Boot />;
  if (!user || user.role !== "admin") return <Navigate to="/" replace />;
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
          <Protected>
            <DashboardPage />
          </Protected>
        }
      />
      <Route
        path="/reports"
        element={
          <Protected>
            <ReportsPage />
          </Protected>
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
