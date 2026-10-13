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
      {/* Левая панель — ГЛУХОЙ фирменный navy (#1b3360): белый текст, акценты brand-sky. */}
      <aside className="relative hidden lg:block overflow-hidden bg-brand-navy text-white border-r border-brand-sky/30">
        <div className="relative h-full flex flex-col justify-between p-12">
          {/* Фирменный знак: маленький (h-7) в левом верхнем углу панели.
              На глухом navy — светлая версия логотипа. */}
          <img src="/logo-light.svg" alt="БазисПорт" width={230} height={35} className="h-7 w-auto" />
          {/* Продукт — Basis.Indicators, ровно по середине высоты панели (абсолютно:
              flex-отступы съели бы половину на логотип и строку внизу). Раскрашен как
              сам знак: белое первое слово, голубое второе. Голубой взят из знака
              (#26AAE1), а не из темы, — иначе «как лого» не получается. На navy даёт
              4.7:1, то есть AA даже для мелкого текста. */}
          <h1 className="absolute left-12 right-12 top-1/2 -translate-y-1/2 text-5xl leading-tight font-semibold max-w-md">
            <span className="text-white">Basis</span>
            <span className="text-[#26AAE1]">.Indicators</span>
          </h1>
          <p className="text-xs text-white/60 max-w-sm">
            Доступ выдаёт администратор кабинета. Вопросы по учёткам — служба поддержки.
          </p>
        </div>
      </aside>

      {/* Карточка входа — обычная белая плашка с мягкой тенью (стекла больше нет) */}
      <main className="grid place-items-center p-6">
        <div className="w-full max-w-sm">
          {/* На мобильных левая navy-панель скрыта — без этого знака на телефоне
              кабинет вообще без бренда. Здесь фон светлый, поэтому логотип обычный. */}
          <img src="/logo.svg" alt="БазисПорт" width={230} height={35} className="mb-5 h-8 w-auto lg:hidden" />
          <form onSubmit={submit} className="panel w-full p-6">
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
        </div>
      </main>
    </div>
  );
}
