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

// ---------------------------------------------------------------- reports ---

export type ReportWarehouse = { id: number; code: string; name: string };

export type ProcessingRow = {
  bucket_date: string;
  warehouse_id: number;
  warehouse: string;
  code: string;
  inbound_t: number;
  outbound_t: number;
  processed_t: number;
  docs_count: number;
  avg_dwell_hours: number;
};

export type ProcessingReport = {
  period: { from: string; to: string };
  totals: {
    inbound_t: number;
    outbound_t: number;
    processed_t: number;
    docs_count: number;
    avg_dwell_hours: number;
    days: number;
  };
  per_warehouse: {
    warehouse_id: number;
    warehouse: string;
    code: string;
    processed_t: number;
    inbound_t: number;
    outbound_t: number;
    docs_count: number;
    avg_dwell_hours: number;
  }[];
  rows: ProcessingRow[];
};

export type CargoReport = {
  snapshot_at: string | null;
  totals: { weight_t: number; overdue_weight_t: number; positions: number };
  rows: {
    snapshot_at: string;
    warehouse_id: number;
    warehouse: string;
    code: string;
    cargo_group: string;
    qty: number;
    weight_t: number;
    overdue_weight_t: number;
    avg_age_days: number;
  }[];
};

export type CapacityTrendReport = {
  period: { from: string; to: string };
  rows: {
    snapshot_at: string;
    warehouse_id: number;
    warehouse: string;
    code: string;
    used: number;
    capacity: number;
    utilization: number;
    unit: string;
  }[];
};

// --------------------------------------------------------------- layout ----

export type WidgetState = { id: string; span: number; visible: boolean };
export type DashboardLayout = { version: number; widgets: WidgetState[] };

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

  // персональные настройки (раскладка дашборда, фильтры отчётов)
  preferences: () => request<Record<string, unknown>>("/api/settings/preferences"),
  savePreference: (key: string, value: unknown) =>
    request<{ key: string; value: unknown }>(`/api/settings/preferences/${key}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  resetPreference: (key: string) =>
    request<void>(`/api/settings/preferences/${key}`, { method: "DELETE" }),

  // отчёты
  reportWarehouses: () => request<ReportWarehouse[]>("/api/reports/warehouses"),
  reportProcessing: (p: ReportParams) => request<ProcessingReport>(`/api/reports/processing?${qs(p)}`),
  reportCargo: (p: ReportParams) => request<CargoReport>(`/api/reports/cargo?${qs(p)}`),
  reportCapacity: (p: ReportParams) => request<CapacityTrendReport>(`/api/reports/capacity-trend?${qs(p)}`),

  /** Выгрузка CSV: fetch с Authorization, затем сохранение файла на диск браузера. */
  async downloadCsv(path: string, p: ReportParams, filename: string) {
    const res = await raw(`${path}?${qs(p)}`);
    if (!res.ok) throw new ApiError(res.status, "Не удалось выгрузить отчёт");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};

export type ReportParams = { from?: string; to?: string; warehouses?: number[] };

function qs(p: ReportParams): string {
  const parts: string[] = [];
  if (p.from) parts.push(`from=${p.from}`);
  if (p.to) parts.push(`to=${p.to}`);
  if (p.warehouses?.length) parts.push(`warehouses=${p.warehouses.join(",")}`);
  return parts.join("&");
}
