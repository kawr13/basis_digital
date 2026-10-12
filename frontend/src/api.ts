/**
 * Клиент API кабинета.
 * access-токен держим в памяти (не в localStorage — туда его не украсть через XSS),
 * refresh — в httpOnly-cookie, который ставит бэкенд.
 * На 401 один раз пробуем молча обновить токен и повторить запрос.
 */

export type User = {
  id: number;
  login: string;
  full_name: string;
  role: "admin" | "top" | "manager";
  email: string | null;
  all_warehouses: boolean;
  warehouse_ids: number[];
  last_login_at: string | null;
};

export type Tokens = { access_token: string; expires_in: number; user: User };

export type CargoRow = {
  warehouse_id: number;
  warehouse: string;
  cargo_group: string;
  qty: number;
  weight_t: number;
  overdue_weight_t: number;
  avg_age_days: number;
};

export type CargoBalance = {
  snapshot_at: string | null;
  total_weight_t: number;
  overdue_weight_t: number;
  rows: CargoRow[];
};

export type CapacityRow = {
  warehouse_id: number;
  warehouse: string;
  code: string;
  used: number;
  capacity: number;
  free: number;
  utilization: number;
  unit: string;
  status: "ok" | "warning" | "critical";
};

export type Capacity = { snapshot_at: string | null; rows: CapacityRow[] };

export type DynamicsPoint = {
  bucket_date: string;
  inbound_t: number;
  outbound_t: number;
  processed_t: number;
  docs_count: number;
  avg_dwell_hours: number;
};

export type Dynamics = {
  days: number;
  points: DynamicsPoint[];
  processed_total: number;
  processed_prev: number;
  delta_pct: number | null;
  avg_dwell_hours: number;
};

export type Summary = {
  generated_at: string;
  data_as_of: string | null;
  warehouses: number;
  cargo_weight_t: number;
  overdue_weight_t: number;
  utilization: number;
  processed_today_t: number;
  processed_7d_t: number;
  dwell_hours: number;
  delta_pct: number | null;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let accessToken = "";
let onExpired: (() => void) | null = null;
let refreshing: Promise<boolean> | null = null;

export function setAccessToken(token: string) {
  accessToken = token;
}
export function getAccessToken() {
  return accessToken;
}
export function setOnSessionExpired(cb: () => void) {
  onExpired = cb;
}

async function raw(path: string, init: RequestInit = {}) {
  const res = await fetch(path, {
    credentials: "include",
    ...init,
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(init.headers || {}),
    },
  });
  return res;
}

async function refresh(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      const res = await raw("/api/auth/refresh", { method: "POST" });
      if (!res.ok) return false;
      const data = (await res.json()) as Tokens;
      accessToken = data.access_token;
      return true;
    })().finally(() => (refreshing = null));
  }
  return refreshing;
}

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  // Для входа и ротации 401 — это «неверный пароль» / «нет сессии», а не «токен протух».
  // Без этого исключения текст ошибки подменяется и пользователя водят по кругу.
  const credentialCall = path === "/api/auth/login" || path === "/api/auth/refresh";
  const res = await raw(path, init);
  if (res.status === 401 && retry && !credentialCall && (await refresh())) return request<T>(path, init, false);
  if (res.status === 401 && !credentialCall) {
    onExpired?.();
    throw new ApiError(401, "Сессия истекла, войдите заново");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (JSON.parse(text) as { detail?: string }).detail ?? detail;
    } catch {
      /* тело не JSON */
    }
    throw new ApiError(res.status, String(detail));
  }
  return (text ? JSON.parse(text) : undefined) as T;
}

export const api = {
  async login(login: string, password: string): Promise<User> {
    const data = await request<Tokens>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ login, password }),
    });
    accessToken = data.access_token;
    return data.user;
  },
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  me: () => request<User>("/api/auth/me"),
  summary: () => request<Summary>("/api/metrics/summary"),
  cargo: () => request<CargoBalance>("/api/metrics/cargo-balance"),
  capacity: () => request<Capacity>("/api/metrics/warehouse-capacity"),
  dynamics: (days = 14) => request<Dynamics>(`/api/metrics/processing-dynamics?days=${days}`),
};
