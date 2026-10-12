import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, getAccessToken, setAccessToken, setOnSessionExpired, type User } from "./api";

type AuthValue = {
  user: User | null;
  booting: boolean;
  login: (login: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const Ctx = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [booting, setBooting] = useState(true);

  // Восстановление сессии: access-токен живёт только в памяти, поэтому после
  // перезагрузки страницы тянем новый по refresh-cookie.
  useEffect(() => {
    setOnSessionExpired(() => setUser(null));
    api
      .me()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setBooting(false));
  }, []);

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
