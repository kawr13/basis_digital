import { Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { ChunkErrorBoundary } from "./components/ChunkErrorBoundary";
import { PageSkeleton, type SkeletonVariant } from "./components/PageSkeleton";
import { lazyRoute } from "./lazyRoute";
import type { User } from "./api";
import {
  SECTION_IDS,
  sectionVisible,
  usePublicSettings,
  type PublicSettings,
} from "./settings";
import LoginPage from "./pages/LoginPage";

/**
 * Разделы кабинета — ленивые (TASKS.md, п.7 «Code-splitting»): экран входа больше
 * не тянет «Статистику» (1357 строк), админку, постановку судов и отчёты, а после
 * релиза неизменённые чанки react и чужих страниц остаются в кэше браузера.
 * LoginPage намеренно статический: это первый экран, он обязан рисоваться без
 * дополнительного запроса, — иначе вход стал бы медленнее, а не быстрее.
 */
const StatisticsPage = lazyRoute(() => import("./pages/StatisticsPage"));
const DashboardPage = lazyRoute(() => import("./pages/DashboardPage"));
const ReportsPage = lazyRoute(() => import("./pages/ReportsPage"));
const VesselsPage = lazyRoute(() => import("./pages/VesselsPage"));
const AdminPage = lazyRoute(() => import("./pages/AdminPage"));

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

/** Маршрут раздела. Раздел закрыт, если он скрыт для всех (`ui.hidden_sections`)
 *  или снят в личных вкладках `users.sections` — и вкладка, и маршрут сразу.
 *  Пересылаем на первый видимый раздел, а не «в никуда». */
const SECTION_PATHS: Record<string, string> = {
  statistics: "/",
  warehouses: "/warehouses",
  reports: "/reports",
  vessels: "/vessels",
};

/** Первый раздел, который человеку показываем; «постановка» — только со правом смотреть. */
function firstVisibleSection(user: User | null, settings: PublicSettings | null): string {
  for (const id of SECTION_IDS) {
    if (sectionVisible(user, settings, id)) return SECTION_PATHS[id];
  }
  return "/";
}

function SectionGuard({ section, children }: { section: string; children: React.ReactNode }) {
  const { user, booting } = useAuth();
  const settings = usePublicSettings();
  if (booting) return <Boot />;
  if (!user) return <Navigate to="/login" replace />;
  if (!sectionVisible(user, settings, section)) {
    const to = firstVisibleSection(user, settings);
    // Делать нечего: видно нечего вообще (в личных вкладках осталась одна
    // «Постановка», а прав на неё нет). Редирект в себя же закрыт — иначе
    // React Router гонял бы <Navigate> по кругу, а экран остался бы пустым.
    if (to === SECTION_PATHS[section]) return <>{children}</>;
    return <Navigate to={to} replace />;
  }
  return <>{children}</>;
}

/** Просмотр плана постановки судов — раздел плюс права на него (см. `sectionVisible`). */
function VesselsGuard({ children }: { children: React.ReactNode }) {
  const { user, booting } = useAuth();
  const settings = usePublicSettings();
  if (booting) return <Boot />;
  if (!user) return <Navigate to="/login" replace />;
  if (!sectionVisible(user, settings, "vessels")) {
    return <Navigate to={firstVisibleSection(user, settings)} replace />;
  }
  return <>{children}</>;
}

/**
 * Обёртка ленивого раздела: скелон на время загрузки чанка + граница ошибок
 * импорта. Порядок снаружи обязателен: guards (AdminOnly/SectionGuard/VesselsGuard)
 * остаются в индексном чанке и считаются ДО запроса чанка страницы — права из
 * vessel_rights и ui.hidden_sections не переезжают в ленивый чанк, поэтому скрытый
 * раздел не мелькнёт вкладкой и не откроется на кадр человеку без доступа.
 */
function LazySection({
  section,
  variant = "default",
  wide = false,
  children,
}: {
  section: string;
  variant?: SkeletonVariant;
  /** wide-раскладка шапки, как у самой страницы, — чтобы не гуляла ширина контейнера */
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <ChunkErrorBoundary key={section} resetKey={section} wide={wide}>
      <Suspense fallback={<PageSkeleton variant={variant} wide={wide} />}>{children}</Suspense>
    </ChunkErrorBoundary>
  );
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
            <LazySection section="statistics" wide>
              <StatisticsPage />
            </LazySection>
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
            <LazySection section="warehouses">
              <DashboardPage />
            </LazySection>
          </SectionGuard>
        }
      />
      <Route
        path="/reports"
        element={
          <SectionGuard section="reports">
            <LazySection section="reports" variant="table">
              <ReportsPage />
            </LazySection>
          </SectionGuard>
        }
      />
      <Route
        path="/vessels"
        element={
          <VesselsGuard>
            <LazySection section="vessels" wide>
              <VesselsPage />
            </LazySection>
          </VesselsGuard>
        }
      />
      <Route
        path="/admin"
        element={
          <AdminOnly>
            <LazySection section="admin" variant="table">
              <AdminPage />
            </LazySection>
          </AdminOnly>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
