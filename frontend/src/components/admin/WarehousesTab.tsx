/**
 * Вкладка «Склады»: справочник складов кабинета.
 *
 * Жёсткого удаления нет намеренно (факты витрины висят внешним ключом на
 * складе): «удалить» = деактивировать, склад перестаёт попадать в метрики,
 * но история остаётся.
 */
import { useCallback, useEffect, useState } from "react";
import { api, type AdminWarehouse, type WarehouseInput } from "../../api";
import { nf } from "../../format";
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

/** Вид склада — тот же белый список, что в бэкенде (schemas.WAREHOUSE_KINDS). */
const KINDS: { value: string; label: string }[] = [
  { value: "warehouse", label: "Склад" },
  { value: "cold", label: "Холодильный" },
  { value: "terminal", label: "Терминал" },
];

const kindLabel = (kind: string) => KINDS.find((k) => k.value === kind)?.label ?? kind;

type FormState = { code: string; name: string; kind: string; capacity: string; unit: string };

const EMPTY: FormState = { code: "", name: "", kind: "warehouse", capacity: "", unit: "т" };

export function WarehousesTab() {
  const [rows, setRows] = useState<AdminWarehouse[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<{ mode: "create" } | { mode: "edit"; warehouse: AdminWarehouse } | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [formError, setFormError] = useState("");
  const [confirmId, setConfirmId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await api.adminWarehouses());
      setError("");
    } catch (e) {
      setError(errText(e, "Не удалось загрузить склады"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openCreate = () => {
    setForm(EMPTY);
    setFormError("");
    setEditing({ mode: "create" });
  };

  const openEdit = (w: AdminWarehouse) => {
    setForm({ code: w.code, name: w.name, kind: w.kind, capacity: String(w.capacity), unit: w.unit });
    setFormError("");
    setEditing({ mode: "edit", warehouse: w });
  };

  const submit = async () => {
    setFormError("");
    const code = form.code.trim();
    const name = form.name.trim();
    if (!code) {
      setFormError("Код склада обязателен (как в 1С)");
      return;
    }
    if (!name) {
      setFormError("Название склада обязательно");
      return;
    }
    const capacity = form.capacity.trim() === "" ? 0 : Number(form.capacity.replace(",", "."));
    if (Number.isNaN(capacity) || capacity < 0) {
      setFormError("Ёмкость — число не меньше 0");
      return;
    }

    setBusy(true);
    try {
      const body: WarehouseInput = {
        code,
        name,
        kind: form.kind,
        capacity,
        unit: form.unit.trim() || "т",
      };
      if (editing?.mode === "create") {
        const created = await api.adminCreateWarehouse(body);
        setRows((list) => [...list, created].sort((a, b) => a.code.localeCompare(b.code, "ru")));
        setNotice(`Склад «${created.code}» создан.`);
      } else if (editing) {
        const updated = await api.adminPatchWarehouse(editing.warehouse.id, body);
        setRows((list) => list.map((w) => (w.id === updated.id ? updated : w)));
        setNotice(`Склад «${updated.code}» обновлён.`);
      }
      setEditing(null);
      setError("");
    } catch (e) {
      setFormError(errText(e, "Склад не сохранён"));
    } finally {
      setBusy(false);
    }
  };

  const setActive = async (w: AdminWarehouse, isActive: boolean) => {
    setBusy(true);
    setConfirmId(null);
    try {
      const updated = isActive
        ? await api.adminPatchWarehouse(w.id, { is_active: true })
        : await api.adminDeleteWarehouse(w.id);
      setRows((list) => list.map((x) => (x.id === updated.id ? updated : x)));
      setNotice(isActive ? `Склад «${updated.code}» снова активен.` : `Склад «${updated.code}» отключён: история сохранена.`);
      setError("");
    } catch (e) {
      setError(errText(e, "Не удалось изменить статус склада"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel p-4" data-tab-panel="warehouses">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="panel-title">Склады</h2>
        <div className="flex items-center gap-3">
          <span className="text-[11px] text-ink-300 num">записей: {rows.length}</span>
          <button className="btn-primary text-xs" onClick={openCreate} data-admin-action="create-warehouse">
            Создать склад
          </button>
        </div>
      </div>

      <div className="mt-3 space-y-2">
        <FormError text={error} />
        <Notice text={notice} />
      </div>

      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className={THEAD_ROW}>
              <Th>Код</Th>
              <Th>Название</Th>
              <Th>Вид</Th>
              <Th right>Ёмкость</Th>
              <Th>Единица</Th>
              <Th>Статус</Th>
              <Th>Действия</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((w) => (
              <tr key={w.id} className={TR} data-warehouse-code={w.code}>
                <td className="py-2 pr-3 num">{w.code}</td>
                <td className="py-2 pr-3">{w.name}</td>
                <td className="py-2 pr-3 text-ink-300">{kindLabel(w.kind)}</td>
                <td className="py-2 pr-3 num text-right">{nf(w.capacity, 1)}</td>
                <td className="py-2 pr-3 text-ink-300">{w.unit}</td>
                <td className="py-2 pr-3">
                  {w.is_active ? <StatusChip tone="ok" text="активен" /> : <StatusChip tone="muted" text="отключён" />}
                </td>
                <td className="py-2">
                  <span className="inline-flex flex-wrap items-center gap-3">
                    <LinkBtn action="edit-warehouse" onClick={() => openEdit(w)}>
                      Изменить
                    </LinkBtn>
                    {w.is_active ? (
                      confirmId === w.id ? (
                        <ConfirmInline
                          action="deactivate-warehouse"
                          text="отключить?"
                          onYes={() => setActive(w, false)}
                          onNo={() => setConfirmId(null)}
                        />
                      ) : (
                        <LinkBtn action="deactivate-warehouse-ask" tone="crit" onClick={() => setConfirmId(w.id)} disabled={busy}>
                          Отключить
                        </LinkBtn>
                      )
                    ) : (
                      <LinkBtn action="enable-warehouse" onClick={() => setActive(w, true)} disabled={busy}>
                        Включить
                      </LinkBtn>
                    )}
                  </span>
                </td>
              </tr>
            ))}
            {!rows.length && !loading && <EmptyRow cols={7} text="Складов нет — создайте первый" />}
          </tbody>
        </table>
        {loading && (
          <div className="mt-2">
            <BusyNote text="загрузка складов…" />
          </div>
        )}
      </div>

      <p className="mt-3 text-[11px] leading-relaxed text-ink-300">
        Код склада — идентификатор из 1С, дубликаты недопустимы. Склады, которые заводит 1С,
        появляются здесь сами; руками справочник правят до подключения 1С или чтобы поправить ёмкость.
      </p>

      {editing && (
        <Modal
          title={editing.mode === "create" ? "Новый склад" : `Правка: ${editing.warehouse.code}`}
          onClose={() => setEditing(null)}
          testId={editing.mode === "create" ? "warehouse-form" : "warehouse-edit"}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Код" hint="например WH-01, как в 1С">
              <input
                className="field"
                value={form.code}
                autoComplete="off"
                onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
                data-field="code"
              />
            </Field>
            <Field label="Название">
              <input
                className="field"
                value={form.name}
                autoComplete="off"
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                data-field="name"
              />
            </Field>
            <Field label="Вид склада">
              <select
                className="field"
                value={form.kind}
                onChange={(e) => setForm((f) => ({ ...f, kind: e.target.value }))}
                data-field="kind"
              >
                {KINDS.map((k) => (
                  <option key={k.value} value={k.value}>
                    {k.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Ёмкость" hint="0 — ёмкость неизвестна, загрузка не считается">
              <input
                className="field"
                type="number"
                min={0}
                step="0.1"
                value={form.capacity}
                onChange={(e) => setForm((f) => ({ ...f, capacity: e.target.value }))}
                data-field="capacity"
              />
            </Field>
            <Field label="Единица измерения" hint="т, м³, паллеты">
              <input
                className="field"
                value={form.unit}
                autoComplete="off"
                onChange={(e) => setForm((f) => ({ ...f, unit: e.target.value }))}
                data-field="unit"
              />
            </Field>
          </div>

          <div className="mt-4 space-y-3">
            <FormError text={formError} />
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn-primary text-sm" onClick={submit} disabled={busy} data-admin-action="save-warehouse">
                {editing.mode === "create" ? "Создать" : "Сохранить"}
              </button>
              <button className="btn-ghost text-sm" onClick={() => setEditing(null)} disabled={busy}>
                Отмена
              </button>
              {busy && <BusyNote />}
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
}
