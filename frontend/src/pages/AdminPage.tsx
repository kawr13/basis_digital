/**
 * Страница администрирования кабинета: пользователи, склады, интеграция с 1С,
 * настройки времени выполнения и журнал действий.
 *
 * Страница намеренно «тонкая»: она только переключает вкладки и держит общий
 * конверт настроек (его читают вкладки «1С» и «Настройки»). Вся логика — в
 * компонентах `components/admin/*`.
 */
import { useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../auth";
import { AppShell } from "../components/AppShell";
import { AuditTab } from "../components/admin/AuditTab";
import { OneCTab } from "../components/admin/OneCTab";
import { SettingsTab } from "../components/admin/SettingsTab";
import { UsersTab } from "../components/admin/UsersTab";
import { WarehousesTab } from "../components/admin/WarehousesTab";
import { useAdminSettings } from "../components/admin/useSettings";

type TabId = "users" | "warehouses" | "onec" | "settings" | "audit";

const TABS: { id: TabId; label: string }[] = [
  { id: "users", label: "Пользователи" },
  { id: "warehouses", label: "Склады" },
  { id: "onec", label: "1С и синхронизация" },
  { id: "settings", label: "Настройки" },
  { id: "audit", label: "Журнал" },
];

export default function AdminPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<TabId>("users");
  const settings = useAdminSettings();

  // доступ проверяем и здесь, и в роутере: прямой заход на /admin не должен
  // показывать админские данные даже случайно
  if (!user || user.role !== "admin") return <Navigate to="/" replace />;

  return (
    <AppShell scopeLabel="панель администратора">
      <section className="panel p-4">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <h2 className="panel-title">Администрирование</h2>
          <div className="flex flex-wrap items-center gap-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                data-tab={t.id}
                onClick={() => setTab(t.id)}
                className={`rounded-md px-3 py-1.5 text-sm transition ${
                  tab === t.id ? "bg-ink-700 text-ink-100" : "text-ink-300 hover:text-ink-100"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <span className="ml-auto text-[11px] text-ink-300">
            изменения видны сразу, без перезапуска сервиса
          </span>
        </div>
      </section>

      {tab === "users" && <UsersTab />}
      {tab === "warehouses" && <WarehousesTab />}
      {tab === "onec" && <OneCTab settings={settings} />}
      {tab === "settings" && <SettingsTab settings={settings} />}
      {tab === "audit" && <AuditTab />}

      <p className="text-[11px] text-ink-300 leading-relaxed">
        Все действия администратора попадают в журнал: создание и отключение учётных записей,
        сброс паролей, правку складов и настроек. Удаления нет ни у пользователей, ни у складов —
        только выключение, чтобы аудит и история загрузок переживали увольнение и закрытие склада.
      </p>
    </AppShell>
  );
}
