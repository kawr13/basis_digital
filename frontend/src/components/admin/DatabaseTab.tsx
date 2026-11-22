/**
 * Вкладка «База данных»: адрес базы, проверка соединения, переезд без перезапуска
 * и перезапуск сервиса (п. 26–28 TASKS2.md).
 *
 * Почему это в браузере: раньше смена базы означала «зайти на сервер, поправить
 * `.env`, пересобрать контейнер». Кнопка «Проверить соединение» делает этот шаг
 * безопасным — бэкенд жмёт `SELECT 1` отдельным соединением и не меняет ничего,
 * пока админ не нажал «Применить». Сам переезд тоже безопасен: если новая база
 * не ответила, кабинет остаётся на прежней.
 *
 * Цена переезда — текущий вход: пользователей читает новая база, поэтому после
 * «Применить» администратор обязан войти заново. Вкладке нечего перерисовывать
 * на чужой базе, поэтому ответ переезда мы не применяем к состоянию — только
 * показываем, что делать дальше; сам кабинет уйдёт на экран входа при первом же
 * 401 (см. `api.ts`).
 */
import { useCallback, useEffect, useState } from "react";
import { api, type DbConfig, type DbTestResult } from "../../api";
import {
  BusyNote,
  ConfirmInline,
  Field,
  FormError,
  InfoNote,
  Notice,
  StatusChip,
  errText,
} from "./ui";

type Form = {
  dialect: string;
  host: string;
  port: string;
  user: string;
  database: string;
  password: string;
};

const EMPTY: Form = { dialect: "postgresql", host: "", port: "5432", user: "", database: "", password: "" };

type Busy = "" | "test" | "apply" | "revert" | "restart";

export function DatabaseTab() {
  const [cfg, setCfg] = useState<DbConfig | null>(null);
  const [loadError, setLoadError] = useState("");
  const [form, setForm] = useState<Form>(EMPTY);
  const [busy, setBusy] = useState<Busy>("");
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");
  const [test, setTest] = useState<DbTestResult | null>(null);
  const [confirm, setConfirm] = useState<"" | "apply" | "revert" | "restart">("");

  const load = useCallback(async () => {
    setLoadError("");
    try {
      const data = await api.dbConfig();
      setCfg(data);
      setForm({
        dialect: data.parts.dialect || "postgresql",
        host: data.parts.host || "",
        port: String(data.parts.port || 5432),
        user: data.parts.user || "",
        database: data.parts.database || "",
        password: "",
      });
    } catch (e) {
      setLoadError(errText(e, "Сведения о базе не получены"));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const candidate = () => ({
    dialect: form.dialect,
    host: form.host.trim(),
    // порт в диапазоне 1…65535 требует схема; для sqlite он не используется
    port: Number(form.port) || 5432,
    user: form.user.trim(),
    database: form.database.trim(),
    // пустое поле = «пароль не меняли»; бэкенд подставит сохранённый
    password: form.password ? form.password : null,
  });

  const run = async (what: Busy, fn: () => Promise<void>) => {
    setBusy(what);
    setActionError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setActionError(errText(e, "Действие не выполнено"));
    } finally {
      setBusy("");
      setConfirm("");
    }
  };

  const onTest = () =>
    run("test", async () => {
      setTest(await api.dbTest(candidate()));
    });

  const onApply = () =>
    run("apply", async () => {
      await api.dbApply(candidate());
      // Сессию эта база больше не знает: просим войти заново и не трогаем экран.
      setNotice("База применена. Перезайдите в кабинет — вход читает новую базу.");
      setTest(null);
    });

  const onRevert = () =>
    run("revert", async () => {
      await api.dbRevert();
      setNotice("Кабинет вернулся к базе из окружения.");
      await load();
    });

  const onRestart = () =>
    run("restart", async () => {
      const res = await api.serviceRestart();
      setNotice(res.note || "Сервис перезапускается.");
    });

  const sqlite = form.dialect === "sqlite";

  return (
    <div className="space-y-5" data-tab-panel="database">
      <section className="panel p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="panel-title">Работающая база</h2>
          <button className="btn-ghost text-xs" onClick={() => void load()} data-admin-action="db-reload">
            Обновить
          </button>
        </div>
        {busy === "" && !cfg && !loadError && <BusyNote text="читаем настройки базы…" />}
        <FormError text={loadError} />
        {cfg && (
          <dl className="mt-3 grid gap-3 sm:grid-cols-2" data-db-current>
            <div>
              <dt className="panel-title">Источник</dt>
              <dd className="mt-1">
                <StatusChip
                  tone={cfg.source === "settings" ? "warn" : "ok"}
                  text={cfg.source === "settings" ? "из настроек кабинета" : "из окружения (DATABASE_URL)"}
                />
              </dd>
            </div>
            <div>
              <dt className="panel-title">Версия сервера</dt>
              <dd className="num mt-1 text-sm text-ink-100">{cfg.server_version}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="panel-title">Подключено</dt>
              <dd className="num mt-1 break-all text-sm text-ink-100" data-db-url>
                {cfg.url}
              </dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="panel-title">База из окружения</dt>
              <dd className="num mt-1 break-all text-sm text-ink-300">{cfg.env_url}</dd>
              <dd className="mt-1 text-[11px] leading-snug text-ink-300">
                Сюда вернёт кнопка «Вернуть ENV». Пароль базы кабинет наружу не отдаёт —
                вместо него маска и флаг «пароль задан»
                {cfg.password_set ? " (задан)" : " (не задан)"}.
              </dd>
            </div>
          </dl>
        )}
      </section>

      <section className="panel p-4">
        <h2 className="panel-title mb-3">Подключение</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Диалект" hint="postgresql — серверная база; sqlite — файл на диске кабинета">
            <select
              className="field"
              value={form.dialect}
              onChange={(e) => setForm({ ...form, dialect: e.target.value })}
              data-db-field="dialect"
            >
              <option value="postgresql">PostgreSQL</option>
              <option value="sqlite">SQLite (файл)</option>
            </select>
          </Field>
          <Field
            label={sqlite ? "Путь к файлу базы" : "Имя базы"}
            hint={
              sqlite
                ? "абсолютный путь, например D:\\tmp\\statcab.db; каталог должен существовать"
                : "например statcab"
            }
          >
            <input
              className="field"
              value={form.database}
              onChange={(e) => setForm({ ...form, database: e.target.value })}
              data-db-field="database"
            />
          </Field>
          {!sqlite && (
            <>
              <Field label="Хост" hint="адрес сервера PostgreSQL, например db.internal">
                <input
                  className="field"
                  value={form.host}
                  onChange={(e) => setForm({ ...form, host: e.target.value })}
                  data-db-field="host"
                />
              </Field>
              <Field label="Порт">
                <input
                  className="field num"
                  type="number"
                  min={1}
                  max={65535}
                  value={form.port}
                  onChange={(e) => setForm({ ...form, port: e.target.value })}
                  data-db-field="port"
                />
              </Field>
              <Field label="Пользователь">
                <input
                  className="field"
                  value={form.user}
                  onChange={(e) => setForm({ ...form, user: e.target.value })}
                  data-db-field="user"
                />
              </Field>
              <Field
                label="Пароль"
                hint={
                  cfg?.password_set
                    ? "оставьте пустым, чтобы оставить прежний пароль"
                    : "пароль не сохранён; введите, если он у базы есть"
                }
              >
                <input
                  className="field"
                  type="password"
                  autoComplete="new-password"
                  placeholder={cfg?.password_set ? "***" : ""}
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  data-db-field="password"
                />
              </Field>
            </>
          )}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            className="btn-ghost text-sm"
            onClick={onTest}
            disabled={busy !== ""}
            data-admin-action="db-test"
          >
            Проверить соединение
          </button>
          <button
            className="btn-primary text-sm"
            onClick={() => setConfirm("apply")}
            disabled={busy !== ""}
            data-admin-action="db-apply"
          >
            Применить
          </button>
          <button
            className="btn-ghost text-sm"
            onClick={() => setConfirm("revert")}
            disabled={busy !== ""}
            data-admin-action="db-revert"
          >
            Вернуть ENV
          </button>
          {busy !== "" && (
            <BusyNote
              text={
                busy === "test"
                  ? "стучимся в базу…"
                  : busy === "apply"
                    ? "переезжаем…"
                    : busy === "revert"
                      ? "возвращаемся к окружению…"
                      : "перезапускаем…"
              }
            />
          )}
        </div>

        {confirm === "apply" && (
          <div className="mt-3">
            <ConfirmInline
              action="db-apply-yes"
              text="Переезд закроет текущие соединения: начатые запросы упадут, а вам придётся войти заново. Применить?"
              onYes={onApply}
              onNo={() => setConfirm("")}
            />
          </div>
        )}
        {confirm === "revert" && (
          <div className="mt-3">
            <ConfirmInline
              action="db-revert-yes"
              text="Вернуться к базе из DATABASE_URL? Текущие соединения закроются."
              onYes={onRevert}
              onNo={() => setConfirm("")}
            />
          </div>
        )}

        <div className="mt-3 space-y-2">
          <FormError text={actionError} />
          <Notice text={notice} />
          {test && (
            <div data-db-test>
              <StatusChip tone={test.ok ? "ok" : "crit"} text={test.ok ? "отвечает" : "отказ"} />
              <span className="num ml-2 text-sm text-ink-300" data-db-test-url>
                {test.url}
              </span>
              {test.ok && <span className="num ml-2 text-sm text-ink-100">{test.version}</span>}
              {!test.ok && <div className="mt-1 text-sm text-crit">{test.error}</div>}
            </div>
          )}
          <InfoNote
            text={
              "«Проверить соединение» ничего не меняет: отдельное соединение и SELECT 1. " +
              "Меняет только «Применить» — и то остаётся на прежней базе, если новая не " +
              "ответила или на ней не поднялась схема. Пустая база без входа не оставит: " +
              "если пользователей на ней нет, кабинет заведёт администратора из `.env` " +
              "(если он не задан — с паролем из журнала)."
            }
          />
        </div>
      </section>

      <section className="panel p-4">
        <h2 className="panel-title mb-2">Перезапуск сервиса</h2>
        <p className="text-[11px] leading-relaxed text-ink-300">
          Процесс завершается, а Docker или systemd поднимают кабинет заново — это способ
          применить то, что требует старта с нуля. Пока идёт загрузка витрины или импорт
          отчёта диспетчера, перезапуск не пройдёт: рвать посередине нельзя.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            className="btn-ghost text-sm"
            onClick={() => setConfirm("restart")}
            disabled={busy !== ""}
            data-admin-action="restart-service"
          >
            Перезапустить сервис
          </button>
          {confirm === "restart" && (
            <ConfirmInline
              action="restart-service-yes"
              text="Сервис встанет на несколько секунд. Перезапустить?"
              onYes={onRestart}
              onNo={() => setConfirm("")}
            />
          )}
        </div>
      </section>
    </div>
  );
}
