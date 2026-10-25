/**
 * Импорт «Отчёта диспетчера» в витрину страницы «Статистика».
 *
 * Временный способ загрузки: файл .xlsm с диска машины, где работает сервер,
 * или загрузка из браузера. Когда 1С начнёт отдавать тот же срез по HTTP,
 *тут останется только статус — разбор уйдёт в app/onec/dispatcher_xlsx.py.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type DispatcherImportResult, type DispatcherStatus } from "../../api";
import { asOf, dmy, nf } from "../../format";
import { errText, Field, FormError, Notice, StatusChip } from "./ui";

/** Файл в base64 (без префикса data:). */
function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result).split(",")[1] ?? "");
    fr.onerror = () => reject(new Error("файл не читается"));
    fr.readAsDataURL(file);
  });
}

export function DispatcherImport() {
  const [status, setStatus] = useState<DispatcherStatus | null>(null);
  const [result, setResult] = useState<DispatcherImportResult | null>(null);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const s = await api.dispatcherStatus();
      setStatus(s);
      setPath((cur) => cur || (s.configured_path ?? ""));
    } catch (e) {
      setError(errText(e, "Статус витрины не загружен"));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const run = async (body: { path?: string; filename?: string; content_base64?: string }, label: string) => {
    setBusy(true);
    setError("");
    setNotice("");
    setResult(null);
    try {
      const res = await api.dispatcherImport(body);
      setResult(res);
      setNotice(`${label}: ${res.described}.`);
      await load();
    } catch (e) {
      setError(errText(e, "Импорт не выполнен"));
    } finally {
      setBusy(false);
    }
  };

  const pickFile = async (file: File | undefined) => {
    if (!file) return;
    await run({ filename: file.name, content_base64: await fileToBase64(file) }, `файл «${file.name}»`);
    if (fileRef.current) fileRef.current.value = "";
  };

  const c = status?.counts;
  return (
    <section className="panel p-4" data-dispatcher-panel>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="panel-title">Отчёт диспетчера (страница «Статистика»)</h2>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-ghost text-xs" onClick={load} data-admin-action="dispatcher-reload">
            Обновить статус
          </button>
          <button
            className="btn-primary text-sm"
            disabled={busy || !path.trim()}
            onClick={() => run({ path: path.trim() }, "файл с диска")}
            data-admin-action="dispatcher-import"
          >
            Импортировать по пути
          </button>
          <label className="btn-ghost text-sm inline-flex items-center">
            {busy ? "читаем файл…" : "Загрузить файл…"}
            <input
              ref={fileRef}
              type="file"
              accept=".xlsm,.xlsx"
              className="hidden"
              disabled={busy}
              onChange={(e) => pickFile(e.target.files?.[0])}
              data-admin-action="dispatcher-upload"
            />
          </label>
        </div>
      </div>

      <p className="mt-2 text-[11px] leading-relaxed text-ink-300">
        Витрину наполняет «Отчёт диспетчера» — по одному листу на смену (08–20 и 20–08). Повторный
        импорт заменяет смены из файла, поэтому данные не дублируются. Когда 1С отдаст тот же срез по
        HTTP, файл станет не нужен: путь задаётся в настройке{' '}
        <span className="num text-ink-200">dispatcher.source_path</span>, автозагрузка при старте —{' '}
        <span className="num text-ink-200">dispatcher.auto_import</span>.
      </p>

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <div className="w-[420px] max-w-full">
          <Field label="Путь к файлу на сервере">
            <input
              className="field w-full"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="C:\\reports\\Отчет диспетчера.xlsm"
              data-dispatcher-path
            />
          </Field>
        </div>
        {status && (
          <span className="inline-flex items-center gap-2 text-xs text-ink-300" data-dispatcher-status>
            <StatusChip
              tone={status.exists ? "ok" : status.configured_path ? "warn" : "muted"}
              text={status.exists ? "файл найден" : status.configured_path ? "пути нет" : "путь не задан"}
            />
            {status.exists && <span className="num">изменён {asOf(status.mtime)}</span>}
            <StatusChip tone={status.auto_import ? "ok" : "muted"} text={status.auto_import ? "автозагрузка вкл" : "автозагрузка выкл"} />
          </span>
        )}
      </div>

      <div className="mt-3 space-y-2">
        <FormError text={error} />
        <Notice text={notice} />
      </div>

      {status && c && (
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {[
            { l: "смен", v: c.shifts },
            { l: "строк обработки", v: c.vessels },
            { l: "вагонов-строк", v: c.rails },
            { l: "авто-строк", v: c.trucks },
            { l: "судов в витрине", v: status.date_min ? `${dmy(status.date_min ?? "")} — ${dmy(status.date_max ?? "")}` : "—" },
            { l: "последняя загрузка", v: status.updated_at ? asOf(status.updated_at) : "ещё не загружался" },
          ].map((x) => (
            <div key={x.l} className="rounded-md border border-ink-600 bg-ink-800 p-2">
              <div className="micro-cap">{x.l}</div>
              <div className="num text-sm text-ink-100">{typeof x.v === "number" ? nf(x.v, 0) : x.v}</div>
            </div>
          ))}
        </div>
      )}

      {result && (
        <div className="mt-3 overflow-x-auto" data-dispatcher-result>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-ink-600 text-[11px] uppercase tracking-wide text-ink-300">
                <th className="py-2 pr-3 text-left">Что разобрано</th>
                <th className="py-2 text-right">Строк</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(result.imported).map(([k, v]) => (
                <tr key={k} className="border-b border-ink-700/60">
                  <td className="py-1.5 pr-3 text-ink-200">{k}</td>
                  <td className="py-1.5 text-right num">{nf(v, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
