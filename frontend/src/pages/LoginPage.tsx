import { FormEvent, useState } from "react";
import { useAuth } from "../auth";
import { ApiError } from "../api";

export default function LoginPage() {
  const { login } = useAuth();
  const [loginName, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(loginName.trim(), password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Сервер недоступен, попробуйте позже");
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid h-full lg:grid-cols-[1.1fr_1fr]">
      {/* Левая панель — фирменный navy-градиент (nav шапки #1b3360) с голубыми подсветками */}
      <aside className="relative hidden lg:block overflow-hidden border-r border-white/10 bg-[linear-gradient(165deg,#12203a_0%,#1b3360_52%,#0b1424_100%)]">
        <div className="absolute inset-0 opacity-[0.16] bg-[radial-gradient(circle_at_20%_18%,#2fbaec_0,transparent_45%),radial-gradient(circle_at_78%_72%,#337ab7_0,transparent_42%)]" />
        <div className="relative h-full flex flex-col justify-between p-12">
          <div className="text-[13px] tracking-[0.2em] uppercase text-ink-300">БазисПорт</div>
          <div>
            <h1 className="text-4xl leading-tight font-semibold max-w-md">
              Кабинет <span className="text-accent">веб-статистики</span>
            </h1>
            <p className="mt-4 text-ink-300 max-w-md">
              Остатки грузов, загрузка складов и динамика обработки — в одном окне,
              данные выгружаются из 1С.
            </p>
            <dl className="mt-10 grid grid-cols-3 gap-6 max-w-md">
              {[
                ["Остатки", "по всем складам"],
                ["Ёмкость", "занято / доступно"],
                ["Динамика", "принято / отгружено"],
              ].map(([t, d]) => (
                <div key={t}>
                  <dt className="text-sm text-ink-100">{t}</dt>
                  <dd className="text-xs text-ink-300 mt-1">{d}</dd>
                </div>
              ))}
            </dl>
          </div>
          <p className="text-xs text-ink-300">
            Доступ выдаёт администратор кабинета. Вопросы по учёткам — служба поддержки.
          </p>
        </div>
      </aside>

      {/* Карточка входа — то же «стекло», что и панели кабинета */}
      <main className="grid place-items-center p-6">
        <form onSubmit={submit} className="panel glass-strong w-full max-w-sm p-6">
          <h2 className="text-xl font-semibold">Вход в кабинет</h2>
          <p className="mt-1 text-sm text-ink-300">Рабочая учётная запись, не учётка 1С.</p>

          <label className="block mt-8">
            <span className="panel-title">Логин</span>
            <input
              className="field mt-2"
              value={loginName}
              autoComplete="username"
              autoFocus
              onChange={(e) => setLogin(e.target.value)}
              placeholder="ivanova"
            />
          </label>

          <label className="block mt-4">
            <span className="panel-title">Пароль</span>
            <input
              className="field mt-2"
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
            />
          </label>

          {error && (
            <div
              role="alert"
              className="mt-4 rounded-md border border-crit/40 bg-crit/10 px-3 py-2 text-sm text-crit"
            >
              {error}
            </div>
          )}

          <button className="btn-primary w-full mt-6" disabled={busy || !loginName || !password}>
            {busy ? "Проверяем…" : "Войти"}
          </button>

          <p className="mt-6 text-xs text-ink-300 leading-relaxed">
            После 5 неверных попыток учётка блокируется на 15 минут. Если вход не
            получается — обратитесь к администратору.
          </p>
        </form>
      </main>
    </div>
  );
}
