/**
 * Вкладка «Настройки»: параметры, которые админ меняет без релиза.
 * Форма собрана из `specs` ответа бэкенда, поэтому типы и диапазоны не дублируются.
 *
 * Поле `onec.timeout_sec` продублировано с вкладки «1С и синхронизация» намеренно:
 * оба редактора работают с одним конвертом настроек, значения не расходятся.
 */
import { useEffect, useState } from "react";
import { api } from "../../api";
import { SettingsForm } from "./SettingsForm";
import type { AdminSettings } from "./useSettings";
import { InfoNote } from "./ui";

const SECTIONS = [
  {
    title: "Загрузка витрины по расписанию",
    keys: ["sync.enabled", "sync.interval_minutes", "sync.window_days"],
    note: "После сохранения планировщик перестраивается сразу, без перезапуска сервиса.",
  },
  {
    title: "Что видит пользователь",
    keys: ["ui.dynamics_days", "reports.default_days", "ui.refresh_seconds",
           "thresholds.capacity_warn", "thresholds.capacity_crit"],
    note: "Пороги загрузки склада: «критично» не может быть ниже «внимания» (крит ≥ варн).",
  },
  {
    title: "Отображение разделов (для всех пользователей)",
    keys: ["ui.hidden_sections"],
    note: "Скрытые разделы исчезают из шапки и по своему адресу редиректят на первый видимый. "
        + "Скрыть все разделы нельзя: бэк не даст сохранить. Изменение действует для всех после сохранения.",
  },
  {
    title: "Статистика порта (отчёт диспетчера)",
    keys: ["dispatcher.source_path", "dispatcher.auto_import", "dispatcher.default_period_days",
           "dispatcher.max_period_days"],
    note: "Витрину страницы «Статистика» наполняет «Отчёт диспетчера»: путь к файлу на сервере, "
        + "автозагрузка при старте (файл забирается, только если он свежее последней выгрузки), "
        + "сколько суток считать по умолчанию и какой период бэкенд вообще согласится "
        + "посчитать (годовой запрос стоит секунду и ~700 КБ JSON). Период отсчитывается от "
        + "последней даты данных, а не от сегодня; лимит не может быть меньше периода по умолчанию.",
  },
  {
    title: "Постановка судов: доступ",
    keys: ["vessels.view_roles", "vessels.edit_roles", "vessels.delete_roles"],
    note: "Роли через запятую: admin, top, manager. Право ставить суда можно выдать "
        + "менеджеру-диспетчеру, не повышая его роль; закрытый просмотр убирает и вкладку, и маршрут.",
  },
  {
    title: "Безопасность входа",
    keys: ["security.max_failed_logins", "security.lockout_minutes"],
    note: "После серии неверных входов учётку блокирует бэкенд; разблокировать — на вкладке «Пользователи».",
  },
  {
    title: "Обращение к 1С",
    keys: ["onec.timeout_sec"],
    note: "Адрес и учётные данные — на вкладке «1С и синхронизация».",
  },
];

export function SettingsTab({ settings }: { settings: AdminSettings }) {
  const [pub, setPub] = useState<Record<string, number | string> | null>(null);

  // публичный срез настроек: их читает любой авторизованный пользователь,
  // именно с этими значениями дашборд и отчёты считают пороги и периоды
  useEffect(() => {
    api
      .publicSettings()
      .then(setPub)
      .catch(() => setPub(null));
  }, [settings.env]);

  return (
    <section className="panel p-4" data-tab-panel="settings">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="panel-title">Настройки времени выполнения</h2>
        <span className="text-[11px] text-ink-300">
          приоритет: значения в базе → <span className="num">.env</span> → значения по умолчанию
        </span>
      </div>

      <div className="mt-4">
        <SettingsForm
          env={settings.env}
          loading={settings.loading}
          error={settings.error}
          sections={SECTIONS}
          scheduler={settings.scheduler}
          onSave={settings.save}
          onReset={settings.reset}
          onReload={settings.reload}
        />
      </div>

      {pub && (
        <div className="mt-4">
          <InfoNote
            text={`Доступно всем пользователям: динамика ${pub["ui.dynamics_days"]} сут, отчёт по умолчанию ${pub["reports.default_days"]} сут, пороги загрузки ${pub["thresholds.capacity_warn"]}% / ${pub["thresholds.capacity_crit"]}%`}
          />
        </div>
      )}
    </section>
  );
}
