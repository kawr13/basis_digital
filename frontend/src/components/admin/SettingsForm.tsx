/**
 * Форма настроек времени выполнения, собранная из `specs` ответа
 * GET /api/admin/settings: типы, диапазоны и подсказки приходят с бэкенда,
 * поэтому новый ключ настройки появляется в UI сам.
 *
 * Секреты (onec.password, onec.push_token) наружу не отдаются: поле пустое —
 * значение не меняем, подсказка показывает только флаг «задан».
 */
import { useEffect, useMemo, useState } from "react";
import type { SettingsEnvelope, SettingsPatch, SettingSpec, SchedulerReport } from "../../api";
import { BusyNote, Field, FormError, InfoNote, Notice } from "./ui";
import { schedulerNote } from "./useSettings";

export type SettingsSection = { title: string; keys: string[]; note?: string };

/** Подписи id разделов для чекбоксов (ui.hidden_sections). */
const SECTION_LABELS: Record<string, string> = {
  statistics: "Статистика",
  warehouses: "Склады 1С",
  reports: "Отчёты",
  vessels: "Постановка",
};

type DraftValue = string | boolean;

/** Значение настройки в виде строки (для input) или булева (для чекбокса).
 *  Для str_list список складываем в «a,b» — черновик остаётся строковым,
 *  а чекбоксы рисуются из этой строки. */
function toDraft(spec: SettingSpec | undefined, value: unknown): DraftValue {
  if (spec?.secret) return "";
  if (typeof value === "boolean") return value;
  if (spec?.kind === "str_list") return Array.isArray(value) ? value.join(",") : "";
  return value === null || value === undefined ? "" : String(value);
}

function differs(stored: unknown, draft: DraftValue): boolean {
  if (typeof draft === "boolean") return draft !== stored;
  const a = String(stored ?? "");
  if (a === "" || draft === "") return a !== draft;
  const na = Number(a);
  const nb = Number(draft);
  if (!Number.isNaN(na) && !Number.isNaN(nb)) return na !== nb;
  return a !== draft;
}

export function SettingsForm({
  env,
  loading,
  error,
  sections,
  scheduler,
  onSave,
  onReset,
  onReload,
}: {
  env: SettingsEnvelope | null;
  loading: boolean;
  error: string;
  sections: SettingsSection[];
  scheduler: SchedulerReport | null;
  onSave: (patch: SettingsPatch) => Promise<SettingsEnvelope | null>;
  onReset: (keys: string[]) => Promise<SettingsEnvelope | null>;
  onReload: () => void;
}) {
  const [draft, setDraft] = useState<Record<string, DraftValue>>({});
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");

  const keys = useMemo(() => sections.flatMap((s) => s.keys), [sections]);
  // пересобираем черновик только когда реально изменились значения с сервера
  const signature = env ? JSON.stringify(env.settings) : "";

  useEffect(() => {
    if (!env) return;
    const next: Record<string, DraftValue> = {};
    for (const key of keys) next[key] = toDraft(env.specs[key], env.settings[key]);
    setDraft(next);
  }, [signature, keys]);

  if (loading && !env) {
    return (
      <div className="space-y-3">
        <BusyNote text="загрузка настроек…" />
        <FormError text={error} />
      </div>
    );
  }
  if (!env) return <FormError text={error || "Настройки недоступны"} />;

  const overridden = new Set(env.overridden);
  const changed = keys.filter((key) => {
    const spec = env.specs[key];
    if (spec?.secret) return false;
    return differs(env.settings[key], draft[key] ?? "");
  });
  const resettable = keys.filter((k) => overridden.has(k));

  const set = (key: string, value: DraftValue) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setNotice("");
  };

  const save = async () => {
    setFormError("");
    setNotice("");
    const patch: SettingsPatch = {};
    const problems: string[] = [];

    for (const key of keys) {
      const spec = env.specs[key];
      if (!spec) continue;
      const value = draft[key] ?? "";

      if (spec.secret) {
        // пустое поле = не трогаем секрет; маска "***" из ответа тоже не значение
        if (typeof value === "string" && value.trim() && value.trim() !== "***") patch[key] = value.trim();
        continue;
      }
      if (!differs(env.settings[key], value)) continue;

      if (spec.kind === "bool") {
        patch[key] = Boolean(value);
        continue;
      }
      if (spec.kind === "str_list") {
        // бэк ждёт массив; пустой список — «ничего не прячем»
        patch[key] = String(value).split(",").map((t) => t.trim()).filter(Boolean);
        continue;
      }
      if (spec.kind === "int" || spec.kind === "float") {
        const raw = String(value).trim().replace(",", ".");
        const num = Number(raw);
        if (raw === "" || Number.isNaN(num) || (spec.kind === "int" && !Number.isInteger(num))) {
          problems.push(`«${spec.label}»: нужно ${spec.kind === "int" ? "целое" : "число"}`);
          continue;
        }
        if (spec.min !== null && num < spec.min) problems.push(`«${spec.label}»: не меньше ${spec.min}`);
        if (spec.max !== null && num > spec.max) problems.push(`«${spec.label}»: не больше ${spec.max}`);
        patch[key] = num;
        continue;
      }
      patch[key] = String(value);
    }

    // пара порогов: «критично» не ниже «внимания» — ловим до запроса, текст тот же
    const warn = patch["thresholds.capacity_warn"] ?? env.settings["thresholds.capacity_warn"];
    const crit = patch["thresholds.capacity_crit"] ?? env.settings["thresholds.capacity_crit"];
    if (typeof warn === "number" && typeof crit === "number" && crit < warn) {
      problems.push("Порог «критично» не может быть ниже порога «внимание»");
    }

    if (problems.length) {
      setFormError(problems.join("; "));
      return;
    }
    if (!Object.keys(patch).length) {
      setNotice("Менять нечего: значения совпадают с текущими.");
      return;
    }

    setBusy(true);
    const saved = await onSave(patch);
    setBusy(false);
    if (saved) setNotice(`Сохранено: ${Object.keys(patch).join(", ")}.`);
  };

  const resetDefaults = async () => {
    if (!resettable.length) return;
    setFormError("");
    setBusy(true);
    const saved = await onReset(resettable);
    setBusy(false);
    if (saved) setNotice(`Возвращено к значениям по умолчанию: ${resettable.join(", ")}.`);
  };

  return (
    <div className="space-y-5" data-settings-form>
      {sections.map((section) => (
        <section key={section.title}>
          <h3 className="panel-title mb-2">{section.title}</h3>
          {section.note && <p className="mb-2 text-[11px] leading-relaxed text-ink-300">{section.note}</p>}
          <div className="grid gap-3 sm:grid-cols-2">
            {section.keys.map((key) => {
              const spec = env.specs[key];
              if (!spec) return null;
              const value = draft[key] ?? "";
              const isChanged = spec.secret ? typeof value === "string" && !!value.trim() : changed.includes(key);
              const fromEnv = spec.from_env && !overridden.has(key);

              const hints: string[] = [];
              if (spec.hint) hints.push(spec.hint);
              if (spec.secret) {
                hints.push(
                  env.settings[`${key}_set`] === true
                    ? "значение задано — оставьте поле пустым, чтобы не менять"
                    : "значение не задано",
                );
              } else if (overridden.has(key)) {
                hints.push(`значения по умолчанию: ${String(env.defaults[key])}`);
              } else if (fromEnv) {
                hints.push(`значение из .env (${spec.from_env}), по умолчанию: ${String(env.defaults[key])}`);
              }
              if (spec.min !== null && spec.max !== null) hints.push(`допустимо ${spec.min}–${spec.max}`);

              return (
                <Field key={key} label={spec.label} hint={hints.join(" · ")} changed={isChanged}>
                  {spec.kind === "bool" ? (
                    <span className="inline-flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-[#1668a8]"
                        checked={value === true}
                        onChange={(e) => set(key, e.target.checked)}
                        data-setting={key}
                        data-changed={isChanged ? "1" : "0"}
                      />
                      <span className="text-sm text-ink-300">{value === true ? "включено" : "выключено"}</span>
                    </span>
                  ) : spec.kind === "str_list" ? (
                    <span className="flex flex-wrap gap-x-4 gap-y-1.5">
                      {(spec.choices ?? []).map((c) => {
                        const list = String(value).split(",").map((t) => t.trim()).filter(Boolean);
                        return (
                          <label
                            key={c}
                            className="inline-flex cursor-pointer items-center gap-2 text-sm text-ink-100"
                          >
                            <input
                              type="checkbox"
                              className="h-4 w-4 accent-[#1668a8]"
                              checked={list.includes(c)}
                              onChange={(e) => {
                                const next = e.target.checked
                                  ? [...list, c]
                                  : list.filter((t) => t !== c);
                                set(key, next.join(","));
                              }}
                              data-setting={key}
                              data-choice={c}
                              data-changed={isChanged ? "1" : "0"}
                            />
                            {SECTION_LABELS[c] ?? c}
                          </label>
                        );
                      })}
                    </span>
                  ) : spec.kind === "choice" ? (
                    <select
                      className="field"
                      value={String(value)}
                      onChange={(e) => set(key, e.target.value)}
                      data-setting={key}
                      data-changed={isChanged ? "1" : "0"}
                    >
                      {(spec.choices ?? []).map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      className="field"
                      type={spec.kind === "int" || spec.kind === "float" ? "number" : spec.secret ? "password" : "text"}
                      step={spec.kind === "float" ? "0.1" : "1"}
                      min={spec.min ?? undefined}
                      max={spec.max ?? undefined}
                      value={typeof value === "boolean" ? String(value) : value}
                      placeholder={spec.secret ? (env.settings[`${key}_set`] === true ? "***" : "не задан") : ""}
                      autoComplete={spec.secret ? "new-password" : "off"}
                      onChange={(e) => set(key, e.target.value)}
                      data-setting={key}
                      data-changed={isChanged ? "1" : "0"}
                    />
                  )}
                </Field>
              );
            })}
          </div>
        </section>
      ))}

      <div className="flex flex-wrap items-center gap-2 border-t border-ink-700 pt-3">
        <button className="btn-primary text-sm" onClick={save} disabled={busy} data-admin-action="save-settings">
          Сохранить
        </button>
        <button
          className="btn-ghost text-sm"
          onClick={resetDefaults}
          disabled={busy || !resettable.length}
          title={resettable.length ? `Вернуть к значениям по умолчанию: ${resettable.join(", ")}` : "нет изменённых настроек"}
          data-admin-action="reset-settings"
        >
          Сбросить к значениям по умолчанию
        </button>
        <button className="btn-ghost text-sm" onClick={onReload} disabled={busy} data-admin-action="reload-settings">
          Обновить
        </button>
        {changed.length > 0 && <span className="text-xs text-amber2">изменено полей: {changed.length}</span>}
        {busy && <BusyNote text="сохранение…" />}
      </div>

      <FormError text={formError || error} />
      <Notice text={notice} />
      {scheduler && (
        <div data-scheduler-report>
          <InfoNote text={schedulerNote(scheduler)} />
        </div>
      )}
    </div>
  );
}
