/**
 * Вкладка «Пользователи»: список учётных записей, создание/редактирование,
 * сброс пароля, блокировка/разблокировка и мягкое отключение.
 *
 * Правила пароля и занятость логина проверяет бэкенд (409 / 400 / 422) — их
 * текст показываем прямо в форме; клиентская проверка только бережёт лишний
 * круг запроса.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type AdminUser,
  type AdminUserCreate,
  type AdminUserPatch,
  type AdminWarehouse,
  type TempPassword,
} from "../../api";
import { asOf, roleLabel } from "../../format";
import {
  BusyNote,
  ConfirmInline,
  EmptyRow,
  errText,
  Field,
  FormError,
  LinkBtn,
  Modal,
  Notice,
  StatusChip,
  THEAD_ROW,
  Th,
  TR,
} from "./ui";

type Role = "admin" | "top" | "manager";

const ROLES: Role[] = ["manager", "top", "admin"];

type FormState = {
  login: string;
  full_name: string;
  role: Role;
  email: string;
  password: string;
  password2: string;
  warehouse_ids: number[];
};

const EMPTY: FormState = {
  login: "",
  full_name: "",
  role: "manager",
  email: "",
  password: "",
  password2: "",
  warehouse_ids: [],
};

/**
 * Статус учётки. Бэкенд в списке пользователей намеренно не отдаёт is_active и
 * locked_until, поэтому то, что мы поменяли сами в этой сессии, держим локально,
 * а поля `is_active` / `locked_until` подхватим, если бэкенд их добавит.
 */
type Status = "active" | "inactive" | "locked";

function statusOf(u: AdminUser, local: Record<number, Status>): Status {
  if (u.is_active === false) return "inactive";
  const until = u.locked_until;
  if (until) {
    const ts = new Date(until.endsWith("Z") ? until : `${until}Z`).getTime();
    if (!Number.isNaN(ts) && ts > Date.now()) return "locked";
  }
  return local[u.id] ?? "active";
}

/** Зеркало правил security.password_is_strong: минимум 8 символов, буквы и цифры. */
function passwordProblem(pwd: string): string {
  if (pwd.length < 8) return "Минимум 8 символов";
  if (/^\d+$/.test(pwd) || /^[^\d]+$/.test(pwd)) return "Нужны буквы и цифры";
  return "";
}

export function UsersTab() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [warehouses, setWarehouses] = useState<AdminWarehouse[]>([]);
  const [local, setLocal] = useState<Record<number, Status>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<{ mode: "create" } | { mode: "edit"; user: AdminUser } | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [newPassword, setNewPassword] = useState("");
  const [formError, setFormError] = useState("");
  const [temp, setTemp] = useState<TempPassword | null>(null);
  const [copied, setCopied] = useState(false);
  const [confirmId, setConfirmId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [us, whs] = await Promise.all([api.adminUsers(), api.adminWarehouses()]);
      setUsers(us);
      setWarehouses(whs);
      setError("");
    } catch (e) {
      setError(errText(e, "Не удалось загрузить список пользователей"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const codeById = useMemo(() => new Map(warehouses.map((w) => [w.id, w.code])), [warehouses]);

  const scopeLabel = (u: AdminUser) => {
    if (u.all_warehouses || u.warehouse_ids.length === 0) return "все склады";
    return u.warehouse_ids.map((id) => codeById.get(id) ?? `#${id}`).join(", ");
  };

  const patchUser = (updated: AdminUser) =>
    setUsers((list) => list.map((u) => (u.id === updated.id ? updated : u)));

  const markLocal = (id: number, status: Status) => setLocal((m) => ({ ...m, [id]: status }));

  // ---------------------------------------------------------------- форма ---
  const openCreate = () => {
    setForm(EMPTY);
    setNewPassword("");
    setFormError("");
    setEditing({ mode: "create" });
  };

  const openEdit = (u: AdminUser) => {
    setForm({
      login: u.login,
      full_name: u.full_name,
      role: u.role as Role,
      email: u.email ?? "",
      password: "",
      password2: "",
      warehouse_ids: u.warehouse_ids,
    });
    setNewPassword("");
    setFormError("");
    setEditing({ mode: "edit", user: u });
  };

  const toggleWarehouse = (id: number) =>
    setForm((f) => ({
      ...f,
      warehouse_ids: f.warehouse_ids.includes(id)
        ? f.warehouse_ids.filter((x) => x !== id)
        : [...f.warehouse_ids, id],
    }));

  const submit = async () => {
    setFormError("");
    const fullName = form.full_name.trim();
    if (fullName.length < 2) {
      setFormError("Укажите фамилию и имя (не короче 2 символов)");
      return;
    }
    const email = form.email.trim();
    if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setFormError("E-mail выглядит некорректно");
      return;
    }

    setBusy(true);
    try {
      if (editing?.mode === "create") {
        const login = form.login.trim().toLowerCase();
        if (login.length < 2) throw new Error("Логин не короче 2 символов");
        const problem = passwordProblem(form.password);
        if (problem) throw new Error(problem);
        if (form.password !== form.password2) throw new Error("Пароли не совпадают");
        const body: AdminUserCreate = {
          login,
          full_name: fullName,
          role: form.role,
          password: form.password,
          email: email || null,
          warehouse_ids: form.warehouse_ids,
        };
        const created = await api.adminCreateUser(body);
        setUsers((list) => [...list.filter((u) => u.id !== created.id), created].sort((a, b) => a.login.localeCompare(b.login, "ru")));
        setNotice(`Пользователь «${created.login}» создан.`);
      } else if (editing) {
        const body: AdminUserPatch = {
          full_name: fullName,
          role: form.role,
          warehouse_ids: form.warehouse_ids,
        };
        if (newPassword) {
          const problem = passwordProblem(newPassword);
          if (problem) throw new Error(`Новый пароль: ${problem}`);
          body.password = newPassword;
        }
        const updated = await api.adminPatchUser(editing.user.id, body);
        patchUser(updated);
        if (newPassword) markLocal(updated.id, "active");
        setNotice(`Изменения сохранены: «${updated.login}».`);
      }
      setEditing(null);
      setError("");
    } catch (e) {
      setFormError(errText(e, "Пользователь не сохранён"));
    } finally {
      setBusy(false);
    }
  };

  // ------------------------------------------------------------ действия ---
  const resetPassword = async (u: AdminUser) => {
    setBusy(true);
    try {
      setTemp(await api.adminResetPassword(u.id));
      setCopied(false);
      setNotice("");
      setError("");
    } catch (e) {
      setError(errText(e, "Сброс пароля не выполнен"));
    } finally {
      setBusy(false);
    }
  };

  const copyTemp = async () => {
    if (!temp) return;
    try {
      await navigator.clipboard.writeText(temp.temporary_password);
      setCopied(true);
    } catch {
      setCopied(false);
      setNotice("Скопировать не удалось — выделите пароль мышью и скопируйте вручную.");
    }
  };

  const unlock = async (u: AdminUser) => {
    setBusy(true);
    try {
      patchUser(await api.adminUnlock(u.id));
      markLocal(u.id, statusOf(u, local) === "locked" ? "active" : statusOf(u, local));
      setNotice(`Учётка «${u.login}» разблокирована.`);
      setError("");
    } catch (e) {
      setError(errText(e, "Разблокировка не выполнена"));
    } finally {
      setBusy(false);
    }
  };

  const setActive = async (u: AdminUser, isActive: boolean) => {
    setBusy(true);
    setConfirmId(null);
    try {
      if (isActive) {
        patchUser(await api.adminPatchUser(u.id, { is_active: true }));
        markLocal(u.id, "active");
        setNotice(`Учётка «${u.login}» включена.`);
      } else {
        patchUser(await api.adminDeleteUser(u.id));
        markLocal(u.id, "inactive");
        setNotice(`Учётка «${u.login}» отключена: вход запрещён, живые сессии отозваны.`);
      }
      setError("");
    } catch (e) {
      setError(errText(e, isActive ? "Включение не выполнено" : "Отключение не выполнено"));
    } finally {
      setBusy(false);
    }
  };

  const statusChip = (s: Status) =>
    s === "active" ? (
      <StatusChip tone="ok" text="активен" />
    ) : s === "inactive" ? (
      <StatusChip tone="muted" text="отключён" />
    ) : (
      <StatusChip tone="crit" text="заблокирован" />
    );

  return (
    <section className="panel p-4" data-tab-panel="users">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="panel-title">Пользователи кабинета</h2>
        <div className="flex items-center gap-3">
          <span className="text-[11px] text-ink-300 num">учёток: {users.length}</span>
          <button className="btn-primary text-xs" onClick={openCreate} data-admin-action="create-user">
            Создать пользователя
          </button>
        </div>
      </div>

      <div className="mt-3 space-y-2">
        <FormError text={error} />
        <Notice text={notice} />
      </div>

      {temp && (
        <div className="mt-3 rounded-md border border-warn/40 bg-warn/5 p-3" data-temp-password-box>
          <div className="panel-title mb-1">Временный пароль для «{temp.login}»</div>
          <div className="flex flex-wrap items-center gap-2">
            <code className="num rounded bg-ink-800 px-2 py-1 text-sm text-ink-100" data-temp-password>
              {temp.temporary_password}
            </code>
            <button className="btn-ghost text-xs" onClick={copyTemp} data-admin-action="copy-password">
              Скопировать
            </button>
            <button className="btn-ghost text-xs" onClick={() => setTemp(null)} data-admin-action="hide-password">
              Скрыть
            </button>
            {copied && <span className="text-xs text-ok">скопировано</span>}
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-ink-300">
            Покажите пароль один раз: бэкенд его не хранит и повторно не покажет. При первом входе
            пользователю придётся сменить пароль на свой.
          </p>
        </div>
      )}

      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className={THEAD_ROW}>
              <Th>Логин</Th>
              <Th>ФИО</Th>
              <Th>Роль</Th>
              <Th>Склады</Th>
              <Th>Последний вход</Th>
              <Th>Статус</Th>
              <Th>Действия</Th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const s = statusOf(u, local);
              return (
                <tr key={u.id} className={TR} data-user={u.login}>
                  <td className="py-2 pr-3 num">{u.login}</td>
                  <td className="py-2 pr-3">{u.full_name}</td>
                  <td className="py-2 pr-3 text-ink-300">{roleLabel[u.role] ?? u.role}</td>
                  <td className="py-2 pr-3 text-ink-300">{scopeLabel(u)}</td>
                  <td className="py-2 pr-3 num text-ink-300">{asOf(u.last_login_at)}</td>
                  <td className="py-2 pr-3">{statusChip(s)}</td>
                  <td className="py-2">
                    <span className="inline-flex flex-wrap items-center gap-3">
                      <LinkBtn action="edit-user" onClick={() => openEdit(u)}>
                        Изменить
                      </LinkBtn>
                      <LinkBtn action="reset-password" onClick={() => resetPassword(u)} disabled={busy} title="Показать один раз; при входе придётся сменить">
                        Сбросить пароль
                      </LinkBtn>
                      {s !== "active" && (
                        <LinkBtn action="unlock" onClick={() => unlock(u)} disabled={busy} title="Снять блокировку после серии неверных входов">
                          Разблокировать
                        </LinkBtn>
                      )}
                      {s === "inactive" ? (
                        <LinkBtn action="enable-user" onClick={() => setActive(u, true)} disabled={busy}>
                          Включить
                        </LinkBtn>
                      ) : confirmId === u.id ? (
                        <ConfirmInline
                          action="deactivate-user"
                          text="отключить?"
                          onYes={() => setActive(u, false)}
                          onNo={() => setConfirmId(null)}
                        />
                      ) : (
                        <LinkBtn action="deactivate-ask" tone="crit" onClick={() => setConfirmId(u.id)} disabled={busy}>
                          Отключить
                        </LinkBtn>
                      )}
                    </span>
                  </td>
                </tr>
              );
            })}
            {!users.length && !loading && <EmptyRow cols={7} text="Учётных записей нет" />}
          </tbody>
        </table>
        {loading && <div className="mt-2">
          <BusyNote text="загрузка пользователей…" />
        </div>}
      </div>

      <p className="mt-3 text-[11px] leading-relaxed text-ink-300">
        Пустой список доступных складов = доступ ко всем складам (так работает роль
        «Топ-менеджмент»). Отключение мягкое: учётка остаётся в базе и в журнале, но вход
        запрещён, а живые сессии отзываются.
      </p>

      {editing && (
        <Modal
          title={editing.mode === "create" ? "Новый пользователь" : `Правка: ${editing.user.login}`}
          onClose={() => setEditing(null)}
          testId={editing.mode === "create" ? "user-form" : "user-edit"}
          wide
          footer={
            <div className="space-y-3">
              <FormError text={formError} />
              <div className="flex flex-wrap items-center gap-2">
                <button className="btn-primary text-sm" onClick={submit} disabled={busy} data-admin-action="save-user">
                  {editing.mode === "create" ? "Создать" : "Сохранить"}
                </button>
                <button className="btn-ghost text-sm" onClick={() => setEditing(null)} disabled={busy}>
                  Отмена
                </button>
                {busy && <BusyNote />}
              </div>
            </div>
          }
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Логин" hint={editing.mode === "create" ? "строчные буквы, минимум 2 символа" : "логин не меняется"}>
              <input
                className="field"
                value={form.login}
                disabled={editing.mode === "edit"}
                autoComplete="off"
                onChange={(e) => setForm((f) => ({ ...f, login: e.target.value }))}
                data-field="login"
              />
            </Field>
            <Field label="ФИО">
              <input
                className="field"
                value={form.full_name}
                autoComplete="off"
                onChange={(e) => setForm((f) => ({ ...f, full_name: e.target.value }))}
                data-field="full_name"
              />
            </Field>
            <Field label="Роль">
              <select
                className="field"
                value={form.role}
                onChange={(e) => setForm((f) => ({ ...f, role: e.target.value as Role }))}
                data-field="role"
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {roleLabel[r]}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="E-mail (необязательно)">
              <input
                className="field"
                value={form.email}
                autoComplete="off"
                onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                data-field="email"
              />
            </Field>

            {editing.mode === "create" ? (
              <>
                <Field label="Пароль" hint="минимум 8 символов, нужны буквы и цифры">
                  <input
                    className="field"
                    type="password"
                    value={form.password}
                    autoComplete="new-password"
                    onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
                    data-field="password"
                  />
                </Field>
                <Field label="Пароль ещё раз">
                  <input
                    className="field"
                    type="password"
                    value={form.password2}
                    autoComplete="new-password"
                    onChange={(e) => setForm((f) => ({ ...f, password2: e.target.value }))}
                    data-field="password2"
                  />
                </Field>
              </>
            ) : (
              <Field
                label="Новый пароль (необязательно)"
                hint="оставьте пустым, чтобы не менять пароль; сбросить — в списке"
                className="sm:col-span-2"
              >
                <input
                  className="field"
                  type="password"
                  value={newPassword}
                  autoComplete="new-password"
                  onChange={(e) => setNewPassword(e.target.value)}
                  data-field="new_password"
                />
              </Field>
            )}

            <div className="sm:col-span-2">
              <div className="panel-title mb-1">Доступные склады</div>
              <div className="flex flex-wrap gap-1.5">
                {warehouses.map((w) => (
                  <label
                    key={w.id}
                    className={`chip cursor-pointer border ${
                      form.warehouse_ids.includes(w.id)
                        ? "border-accent/50 bg-accent/10 text-accent"
                        : "border-ink-600 bg-ink-800 text-ink-300"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="mr-1 h-3 w-3 accent-[#1668a8]"
                      checked={form.warehouse_ids.includes(w.id)}
                      onChange={() => toggleWarehouse(w.id)}
                      data-warehouse={String(w.id)}
                    />
                    <span className="num">{w.code}</span> {w.name}
                    {!w.is_active && " (отключён)"}
                  </label>
                ))}
                {!warehouses.length && <span className="text-xs text-ink-300">справочник складов пуст</span>}
              </div>
              <p className="mt-1 text-[11px] text-ink-300">
                Ничего не отмечено — доступ ко <b>всем складам</b> (как у топ-менеджмента).
              </p>
            </div>
          </div>

        </Modal>
      )}
    </section>
  );
}
