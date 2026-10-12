/**
 * Вкладка «Журнал»: последние записи audit_log — кто входил, что меняли в
 * пользователях, складах и настройках. Фильтр только клиентский: бэкенд
 * отдаёт последние N записей, этого хватает, чтобы найти эпизод.
 */
import { useCallback, useEffect, useState } from "react";
import { api, type AuditEntry } from "../../api";
import { asOf } from "../../format";
import { BusyNote, EmptyRow, errText, FormError, THEAD_ROW, Th, TR } from "./ui";

/** Человекочитаемые названия действий; код действия остаётся рядом — он из журнала. */
const ACTION_LABEL: Record<string, string> = {
  "login.ok": "вход выполнен",
  "login.failed": "неверный пароль",
  "login.blocked": "блокировка учётки",
  "login.logout": "выход",
  "login.rate_limit": "слишком много попыток",
  "admin.user_create": "создан пользователь",
  "admin.user_update": "изменён пользователь",
  "admin.user_deactivate": "пользователь отключён",
  "admin.user_unlock": "пользователь разблокирован",
  "admin.reset_password": "сброшен пароль",
  "admin.warehouse_create": "создан склад",
  "admin.warehouse_update": "изменён склад",
  "admin.warehouse_deactivate": "склад отключён",
  "admin.settings_update": "изменены настройки",
  "admin.settings_reset": "настройки сброшены",
  "admin.onec_test": "проверка связи с 1С",
  "admin.sync_run": "загрузка витрины",
};

export function AuditTab() {
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await api.adminAudit(100));
      setError("");
    } catch (e) {
      setError(errText(e, "Журнал не загружен"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? rows.filter((r) =>
        [r.action, r.login ?? "", r.detail ?? "", r.ip ?? ""].some((v) => v.toLowerCase().includes(needle)),
      )
    : rows;

  return (
    <section className="panel p-4" data-tab-panel="audit">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="panel-title">Журнал действий</h2>
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="field-mini w-44"
            value={filter}
            placeholder="фильтр: логин, действие"
            onChange={(e) => setFilter(e.target.value)}
            data-audit-filter
          />
          <span className="text-[11px] text-ink-300 num">
            записей: {shown.length}
            {needle && ` из ${rows.length}`}
          </span>
          <button className="btn-ghost text-xs" onClick={load} data-admin-action="refresh-audit">
            Обновить
          </button>
        </div>
      </div>

      <FormError text={error} />

      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className={THEAD_ROW}>
              <Th>Время (UTC)</Th>
              <Th>Логин</Th>
              <Th>Действие</Th>
              <Th>Подробно</Th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.id} className={TR} data-audit-action={r.action}>
                <td className="py-2 pr-3 num whitespace-nowrap">{asOf(r.created_at)}</td>
                <td className="py-2 pr-3 num">{r.login ?? "—"}</td>
                <td className="py-2 pr-3">
                  <span className="num">{r.action}</span>
                  {ACTION_LABEL[r.action] && <span className="ml-2 text-ink-300">{ACTION_LABEL[r.action]}</span>}
                </td>
                <td className="py-2 text-ink-300">
                  {r.detail ?? ""}
                  {r.ip && <span className="num ml-2 text-ink-400">ip {r.ip}</span>}
                </td>
              </tr>
            ))}
            {!shown.length && !loading && <EmptyRow cols={4} text={needle ? "Под фильтр ничего не попало" : "Журнал пуст"} />}
          </tbody>
        </table>
        {loading && (
          <div className="mt-2">
            <BusyNote text="загрузка журнала…" />
          </div>
        )}
      </div>

      <p className="mt-3 text-[11px] leading-relaxed text-ink-300">
        Журнал не редактируется из интерфейса: в нём остаются и вход, и изменения настроек, и
        сбросы паролей. Записи переживают отключение учётки — увольнение сотрудника не стирает историю.
      </p>
    </section>
  );
}
