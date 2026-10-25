import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  getAccessToken,
  refreshSession,
  setAccessToken,
  setOnSessionExpired,
  type User,
} from "./api";

type AuthValue = {
  user: User | null;
  booting: boolean;
  login: (login: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const Ctx = createContext<AuthValue | null>(null);

/**
 * Когда бутстрап сессии завершён (вход восстановлен по refresh-cookie или
 * признан пустым). Нужен модулям, которые стреляют авторизованными запросами
 * на горах: первый /api/settings/public без access-токена — гарантированный
 * 401 в консоли, а потом уже тихая ротация и повтор.
 */
let resolveAuthReady: (() => void) | null = null;
export const authReady: Promise<void> = new Promise((r) => (resolveAuthReady = r));

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [booting, setBooting] = useState(true);

  // Восстановление сессии: access-токен живёт только в памяти, поэтому после
  // перезагрузки страницы тянем новый по refresh-cookie.
  const booted = useRef(false);
  useEffect(() => {
    // StrictMode в dev намеренно монтирует эффект дважды; без guard страница
    // входа при отсутствии сессии рисует в консоли пару лишних 401
    if (booted.current) return;
    booted.current = true;
    setOnSessionExpired(() => setUser(null));
    (async () => {
      try {
        // access-токен живёт только в памяти, поэтому после перезагрузки его нет
        // всегда. Сначала тихая ротация по refresh-cookie: если cookie тоже пуст,
        // спрашивать /me незачем — это лишний 401 в консоли на странице входа.
        if (!getAccessToken() && !(await refreshSession())) {
          setUser(null);
          return;
        }
        setUser(await api.me());
      } catch {
        setUser(null);
      } finally {
        setBooting(false);
        resolveAuthReady?.();
      }
    })();
  }, []);

  // Права (в том числе на постановку судов) считает бэкенд и присылает вместе с
  // пользователем. Чтобы отзыв права админом дошёл до вкладок без перелогина,
  // обновляем /me, когда возвращаются к окну. Ошибку глотаем: сеть могла моргнуть,
  // а выбрасывать пользователя из кабинета из-за этого неправильно.
  useEffect(() => {
    if (booting || !user) return;
    const ping = () => {
      if (document.visibilityState !== "visible") return;
      api.me().then(setUser).catch(() => undefined);
    };
    document.addEventListener("visibilitychange", ping);
    window.addEventListener("focus", ping);
    return () => {
      document.removeEventListener("visibilitychange", ping);
      window.removeEventListener("focus", ping);
    };
  }, [booting, user]);

  const login = useCallback(async (loginName: string, password: string) => {
    const u = await api.login(loginName, password);
    setUser(u);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setAccessToken("");
      setUser(null);
    }
  }, []);

  const value = useMemo(
    () => ({ user, booting, login, logout }),
    [user, booting, login, logout],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("AuthProvider не обёрнут");
  return v;
}

export { getAccessToken };
