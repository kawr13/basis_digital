/**
 * Вкладка «Причалы»: справочник причалов для документа «Постановка судов».
 *
 * Длина причала — не украшение: от неё зависит шкала плана (10 м на ячейку) и
 * то, пустит ли бэкенд судно («Судно 140 м не встанет у причала РП-1»).
 *
 * Удаление — только у причала без постановок: иначе история плана повиснет на
 * несуществующем причале. Такой причал снимается с линии (`is_active=false`) —
 * он уходит из сетки, а строки остаются.
 */
import { useCallback, useEffect, useState } from "react";
import { api, type Berth, type BerthInput } from "../../api";
import { M_PER_CELL } from "../../vessels";
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

type FormState = { code: string; depth: string; length: string; order: string };

const EMPTY: FormState = { code: "", depth: "", length: "", order: "0" };

/** Число из поля или null: пустое поле = «не задано», а не ноль. */
function numOrEmpty(value: string): number | null {
  const t = value.trim().replace(",", ".");
  if (t === "") return null;
  const n = Number(t);
  return Number.isNaN(n) ? null : n;
}

export function BerthsTab() {
  const [rows, setRows] = useState<Berth[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<{ mode: "create" } | { mode: "edit"; berth: Berth } | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [formError, setFormError] = useState("");
  const [confirmId, setConfirmId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await api.berths());
      setError("");
    } catch (e) {
      setError(errText(e, "Не удалось загрузить причалы"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openCreate = () => {
    const last = rows[rows.length - 1];
    setForm({ code: "", depth: "", length: "", order: String((last?.sort_order ?? 0) + 1) });
    setFormError("");
    setEditing({ mode: "create" });
  };

  const openEdit = (b: Berth) => {
    setForm({
      code: b.code,
      depth: b.depth === null ? "" : String(b.depth),
      length: b.length_m === null ? "" : String(b.length_m),
      order: String(b.sort_order),
    });
    setFormError("");
    setEditing({ mode: "edit", berth: b });
  };

  const submit = async () => {
    setFormError("");
    const code = form.code.trim();
    if (code.length < 2) {
      setFormError("Код причала — минимум 2 символа (его печатают в плане)");
      return;
    }
    if (!/^[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё .\-/]*$/.test(code)) {
      setFormError("Код: буквы, цифры, пробел, дефис и слэш — без спецсимволов");
      return;
    }
    const depth = numOrEmpty(form.depth);
    if (form.depth.trim() !== "" && (depth === null || depth < 0 || depth > 100)) {
      setFormError("Осадка — число от 0 до 100 м");
      return;
    }
    const length = numOrEmpty(form.length);
    if (length !== null && (length < 1 || length > 2000)) {
      setFormError("Длина причала — от 1 до 2000 м");
      return;
    }
    const order = numOrEmpty(form.order) ?? 0;

    setBusy(true);
    try {
      const body: BerthInput = { code, depth, length_m: length, sort_order: order, lanes: 1 };
      if (editing?.mode === "create") {
        const created = await api.createBerth(body);
        setRows((list) => [...list, created].sort((a, b) => a.sort_order - b.sort_order));
        setNotice(`Причал ${created.code} добавлен${length ? ` — ${length} м, шкала ${Math.ceil(length / M_PER_CELL)} ячеек` : ""}.`);
      } else if (editing) {
        // дорожки устарели, но чужое значение портить не будем
        const updated = await api.patchBerth(editing.berth.id, { ...body, lanes: editing.berth.lanes });
        setRows((list) => list.map((b) => (b.id === updated.id ? updated : b)));
        setNotice(`Причал ${updated.code} обновлён.`);
      }
      setEditing(null);
      setError("");
    } catch (e) {
      setFormError(errText(e, "Причал не сохранён"));
    } finally {
      setBusy(false);
    }
  };

  const setActive = async (b: Berth, isActive: boolean) => {
    setBusy(true);
    setConfirmId(null);
    try {
      const updated = await api.patchBerth(b.id, {
        code: b.code,
        depth: b.depth,
        length_m: b.length_m,
        lanes: b.lanes,
        sort_order: b.sort_order,
        is_active: isActive,
      });
      setRows((list) => list.map((x) => (x.id === updated.id ? updated : x)));
      setNotice(
        isActive
          ? `Причал ${updated.code} снова в плане.`
          : `Причал ${updated.code} снят с линии: из сетки ушёл, постановки остались.`,
      );
      setError("");
    } catch (e) {
      setError(errText(e, "Не удалось изменить статус причала"));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (b: Berth) => {
    setBusy(true);
    setConfirmId(null);
    try {
      await api.deleteBerth(b.id);
      setRows((list) => list.filter((x) => x.id !== b.id));
      setNotice(`Причал ${b.code} удалён из справочника.`);
      setError("");
    } catch (e) {
      // 409 — на причале есть постановки: текст бэкенда объясняет про «снять с линии»
      setError(errText(e, "Причал не удалён"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel p-4" data-tab-panel="berths">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="panel-title">Причалы</h2>
        <div className="flex items-center gap-3">
          <span className="text-[11px] text-ink-300 num">записей: {rows.length}</span>
          <button className="btn-primary text-xs" onClick={openCreate} data-admin-action="create-berth">
            Добавить причал
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
              <Th right>Осадка, м</Th>
              <Th right>Длина, м</Th>
              <Th right>Шкала</Th>
              <Th right>Порядок</Th>
              <Th>Статус</Th>
              <Th>Действия</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.id} className={TR} data-berth-code={b.code}>
                <td className="py-2 pr-3">{b.code}</td>
                <td className="py-2 pr-3 num text-right">{b.depth ?? "—"}</td>
                <td className="py-2 pr-3 num text-right">{b.length_m ?? "—"}</td>
                <td className="py-2 pr-3 num text-right text-ink-300" data-berth-cells={b.id}>
                  {b.length_m ? `${b.length_cells} × ${M_PER_CELL} м` : "шкала не задана"}
                </td>
                <td className="py-2 pr-3 num text-right text-ink-300">{b.sort_order}</td>
                <td className="py-2 pr-3">
                  {b.is_active ? (
                    <StatusChip tone="ok" text="в плане" />
                  ) : (
                    <StatusChip tone="muted" text="снят с линии" />
                  )}
                </td>
                <td className="py-2">
                  <span className="inline-flex flex-wrap items-center gap-3">
                    <LinkBtn action="edit-berth" onClick={() => openEdit(b)}>
                      Изменить
                    </LinkBtn>
                    {b.is_active ? (
                      confirmId === b.id ? (
                        <ConfirmInline
                          action="deactivate-berth"
                          text="снять с линии?"
                          onYes={() => setActive(b, false)}
                          onNo={() => setConfirmId(null)}
                        />
                      ) : (
                        <LinkBtn action="deactivate-berth-ask" tone="crit" onClick={() => setConfirmId(b.id)} disabled={busy}>
                          Снять с линии
                        </LinkBtn>
                      )
                    ) : (
                      <LinkBtn action="enable-berth" onClick={() => setActive(b, true)} disabled={busy}>
                        Вернуть в план
                      </LinkBtn>
                    )}
                    {confirmId === -b.id ? (
                      <ConfirmInline
                        action="delete-berth"
                        text="удалить навсегда?"
                        onYes={() => remove(b)}
                        onNo={() => setConfirmId(null)}
                      />
                    ) : (
                      <LinkBtn action="delete-berth-ask" tone="crit" onClick={() => setConfirmId(-b.id)} disabled={busy}>
                        Удалить
                      </LinkBtn>
                    )}
                  </span>
                </td>
              </tr>
            ))}
            {!rows.length && !loading && <EmptyRow cols={7} text="Причалов нет — добавьте первый" />}
          </tbody>
        </table>
        {loading && (
          <div className="mt-2">
            <BusyNote text="загрузка причалов…" />
          </div>
        )}
      </div>

      <p className="mt-3 text-[11px] leading-relaxed text-ink-300">
        Длина причала задаёт шкалу плана: {M_PER_CELL} м на ячейку, то есть 130 м — это 13
        ячеек, и судно длиннее причала кабинет не пустит. Снят с линии — причал исчезает из
        сетки плана, но его постановки остаются: так причал в ремонте не портит историю.
        Удалить можно только причал, на котором ни одной постановки.
      </p>

      {editing && (
        <Modal
          title={editing.mode === "create" ? "Новый причал" : `Правка: ${editing.berth.code}`}
          onClose={() => setEditing(null)}
          testId={editing.mode === "create" ? "berth-form" : "berth-edit"}
          footer={
            <div className="space-y-3">
              <FormError text={formError} />
              <div className="flex flex-wrap items-center gap-2">
                <button
                  className="btn-primary text-sm"
                  onClick={submit}
                  disabled={busy}
                  data-admin-action="save-berth"
                >
                  {editing.mode === "create" ? "Добавить" : "Сохранить"}
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
            <Field label="Код причала" hint="как в плане: РП-1, Причал 3">
              <input
                className="field"
                value={form.code}
                autoComplete="off"
                onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
                data-field="code"
              />
            </Field>
            <Field label="Длина причала, м" hint="10 м на ячейку шкалы; без длины шкалы нет">
              <input
                className="field num"
                type="number"
                min={1}
                max={2000}
                step="10"
                value={form.length}
                onChange={(e) => setForm((f) => ({ ...f, length: e.target.value }))}
                data-field="length-m"
              />
            </Field>
            <Field label="Осадка, м" hint="справочно: какая глубина у стенки">
              <input
                className="field num"
                type="number"
                min={0}
                max={100}
                step="0.1"
                value={form.depth}
                onChange={(e) => setForm((f) => ({ ...f, depth: e.target.value }))}
                data-field="depth"
              />
            </Field>
            <Field label="Порядок в плане" hint="меньше — выше">
              <input
                className="field num"
                type="number"
                value={form.order}
                onChange={(e) => setForm((f) => ({ ...f, order: e.target.value }))}
                data-field="sort-order"
              />
            </Field>
          </div>
          {numOrEmpty(form.length) ? (
            <p className="mt-3 text-[11px] text-ink-300" data-free-hint>
              Шкала: {Math.ceil((numOrEmpty(form.length) as number) / M_PER_CELL)} ячеек по{" "}
              {M_PER_CELL} м — судно длиннее {numOrEmpty(form.length)} м сюда не встанет.
            </p>
          ) : null}
        </Modal>
      )}
    </section>
  );
}
