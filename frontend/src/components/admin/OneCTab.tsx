/**
 * Вкладка «1С и синхронизация»: параметры подключения, проверка связи, ручная
 * загрузка витрины и журнал загрузок.
 *
 * Граница с 1С остаётся ровно той же: кабинет только читает HTTP-срез
 * (или принимает push), в базу 1С не пишет. Поэтому здесь только диагностика.
 */
import { useCallback, useEffect, useState } from "react";
import { api, type OneCTestResult, type SyncRun } from "../../api";
import { asOf, nf } from "../../format";
import { DispatcherImport } from "./DispatcherImport";
import { SettingsForm } from "./SettingsForm";
import type { AdminSettings } from "./useSettings";
import { BusyNote, EmptyRow, errText, FormError, Notice, StatusChip, THEAD_ROW, Th, TR } from "./ui";

const SECTIONS = [
  {
    title: "Источник данных 1С",
    keys: ["onec.source", "onec.base_url", "onec.login", "onec.password", "onec.push_token", "onec.timeout_sec"],
    note: "mock — демо-данные кабинета, http — HTTP-сервис 1С. Пароль и токен push наружу не отдаются: пустое поле означает «не менять».",
  },
  {
    title: "Загрузка витрины по расписанию",
    keys: ["sync.enabled", "sync.interval_minutes", "sync.window_days"],
  },
];

export function OneCTab({ settings }: { settings: AdminSettings }) {
  const [test, setTest] = useState<OneCTestResult | null>(null);
  const [testBusy, setTestBusy] = useState(false);
  const [run, setRun] = useState<SyncRun | null>(null);
  const [runBusy, setRunBusy] = useState(false);
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");

  const loadRuns = useCallback(async () => {
    try {
      setRuns(await api.syncRuns(20));
    } catch (e) {
      setActionError(errText(e, "Журнал загрузок не загружен"));
    }
  }, []);

  useEffect(() => {
    loadRuns();
  }, [loadRuns]);

  const runTest = async () => {
    setTestBusy(true);
    setActionError("");
    setNotice("");
    try {
      const res = await api.onecTest();
      setTest(res);
      setNotice(res.ok ? `Связь с источником «${res.source}» в порядке.` : `Источник «${res.source}»: проверка не прошла, детали в таблице.`);
    } catch (e) {
      setActionError(errText(e, "Проверка связи не выполнена"));
    } finally {
      setTestBusy(false);
    }
  };

  const runNow = async () => {
    setRunBusy(true);
    setActionError("");
    setNotice("");
    try {
      const res = await api.syncRun();
      setRun(res);
      await loadRuns();
      setNotice(
        res.status === "ok"
          ? `Загрузка завершена: ${nf(res.rows)} строк.`
          : `Загрузка завершилась со статусом «${res.status}».`,
      );
    } catch (e) {
      setActionError(errText(e, "Загрузка не запущена"));
    } finally {
      setRunBusy(false);
    }
  };

  return (
    <div className="space-y-5" data-tab-panel="onec">
      <section className="panel p-4">
        <h2 className="panel-title mb-3">Интеграция с 1С</h2>
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
      </section>

      <section className="panel p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="panel-title">Диагностика и ручная загрузка</h2>
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn-ghost text-sm" onClick={runTest} disabled={testBusy} data-admin-action="test-onec">
              Проверить связь
            </button>
            <button className="btn-primary text-sm" onClick={runNow} disabled={runBusy} data-admin-action="sync-now">
              Синхронизировать сейчас
            </button>
            {(testBusy || runBusy) && <BusyNote text={runBusy ? "загрузка витрины…" : "проверяем связь…"} />}
          </div>
        </div>

        <div className="mt-3 space-y-2">
          <FormError text={actionError} />
          <Notice text={notice} />
        </div>

        {run && (
          <div className="mt-3 rounded-md border border-ink-600 bg-ink-800 p-3 text-sm" data-sync-result>
            <span className="mr-3">
              итог:{" "}
              <StatusChip tone={run.status === "ok" ? "ok" : "crit"} text={run.status === "ok" ? "загружено" : run.status} />
            </span>
            <span className="num mr-3 text-ink-300">строк: {nf(run.rows)}</span>
            <span className="num mr-3 text-ink-300">источник: {run.source}</span>
            <span className="num text-ink-300">
              {asOf(run.started_at)}
              {run.finished_at ? ` — ${asOf(run.finished_at)}` : ""}
            </span>
            {run.error && <div className="mt-1 text-crit">{run.error}</div>}
          </div>
        )}

        {test && (
          <div className="mt-3 overflow-x-auto" data-onec-test>
            <div className="panel-title mb-2">
              Проверка связи · источник <span className="num text-ink-100">{test.source}</span> ·{" "}
              <StatusChip tone={test.ok ? "ok" : "crit"} text={test.ok ? "ok" : "ошибка"} />
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className={THEAD_ROW}>
                  <Th>Путь / проверка</Th>
                  <Th>Результат</Th>
                  <Th right>HTTP</Th>
                  <Th right>мс</Th>
                  <Th>Ошибка</Th>
                </tr>
              </thead>
              <tbody>
                {test.checks.map((c, i) => (
                  <tr key={`${c.path}-${i}`} className={TR} data-onec-check={c.path}>
                    <td className="py-2 pr-3 num">{c.path}</td>
                    <td className="py-2 pr-3">
                      <StatusChip tone={c.ok ? "ok" : "crit"} text={c.ok ? "ok" : "ошибка"} />
                    </td>
                    <td className="py-2 pr-3 num text-right text-ink-300">{c.status ?? "—"}</td>
                    <td className="py-2 pr-3 num text-right text-ink-300">{nf(c.ms)}</td>
                    <td className="py-2 text-crit">{c.error ?? ""}</td>
                  </tr>
                ))}
                {!test.checks.length && <EmptyRow cols={5} text="Источник не вернул проверок" />}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <DispatcherImport />

      <section className="panel p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="panel-title">Журнал загрузок</h2>
          <div className="flex items-center gap-3">
            <span className="text-[11px] text-ink-300 num">записей: {runs.length}</span>
            <button className="btn-ghost text-xs" onClick={loadRuns} data-admin-action="reload-runs">
              Обновить
            </button>
          </div>
        </div>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className={THEAD_ROW}>
                <Th>Старт</Th>
                <Th>Финиш</Th>
                <Th>Статус</Th>
                <Th>Источник</Th>
                <Th right>Строк</Th>
                <Th>Ошибка</Th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className={TR} data-sync-run={String(r.id)}>
                  <td className="py-2 pr-3 num">{asOf(r.started_at)}</td>
                  <td className="py-2 pr-3 num text-ink-300">{r.finished_at ? asOf(r.finished_at) : "—"}</td>
                  <td className="py-2 pr-3">
                    <StatusChip tone={r.status === "ok" ? "ok" : r.status === "running" ? "warn" : "crit"} text={r.status} />
                  </td>
                  <td className="py-2 pr-3 text-ink-300">{r.source}</td>
                  <td className="py-2 pr-3 num text-right">{nf(r.rows)}</td>
                  <td className="py-2 text-crit">{r.error ?? ""}</td>
                </tr>
              ))}
              {!runs.length && <EmptyRow cols={6} text="Загрузок ещё не было" />}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-[11px] leading-relaxed text-ink-300">
          Здесь и плановые загрузки по расписанию, и ручные, и push из 1С. Повторный срез не
          плодит дубликатов: строки витрины обновляются по ключу «срез × склад × группа груза».
        </p>
      </section>
    </div>
  );
}
