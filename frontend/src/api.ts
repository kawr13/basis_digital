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

// ------------------------------------------------------------- admin ------

/**
 * Ответ GET /api/admin/users. Полей is_active / locked_until бэкенд наружу не
 * отдаёт — они опциональны: если бэкенд их добавит, UI подхватит без правок.
 */
export type AdminUser = User & { is_active?: boolean; locked_until?: string | null };

export type AdminUserCreate = {
  login: string;
  full_name: string;
  role: "admin" | "top" | "manager";
  password: string;
  email?: string | null;
  warehouse_ids: number[];
};

export type AdminUserPatch = {
  full_name?: string;
  role?: "admin" | "top" | "manager";
  is_active?: boolean;
  password?: string;
  warehouse_ids?: number[];
};

export type TempPassword = { login: string; temporary_password: string };

export type AdminWarehouse = {
  id: number;
  code: string;
  name: string;
  kind: string;
  capacity: number;
  unit: string;
  is_active: boolean;
};

export type WarehouseInput = {
  code?: string;
  name?: string;
  kind?: string;
  capacity?: number;
  unit?: string;
  is_active?: boolean;
};

export type SettingSpec = {
  kind: "str" | "int" | "float" | "bool" | "choice";
  min: number | null;
  max: number | null;
  choices: string[] | null;
  secret: boolean;
  label: string;
  hint: string;
  from_env: string | null;
};

/** Отчёт планировщика после PUT /api/admin/settings. */
export type SchedulerReport = {
  scheduler: boolean;
  enabled: boolean;
  interval_minutes: number;
  job: string | null;
};

export type SettingsEnvelope = {
  settings: Record<string, string | number | boolean>;
  defaults: Record<string, string | number | boolean>;
  overridden: string[];
  specs: Record<string, SettingSpec>;
  scheduler?: SchedulerReport | null;
};

export type SettingsPatch = Record<string, string | number | boolean>;

export type OneCCheck = {
  path: string;
  ok: boolean;
  status: number | null;
  ms: number;
  error: string | null;
};

export type OneCTestResult = { source: string; ok: boolean; checks: OneCCheck[] };

export type SyncRun = {
  id: number;
  started_at: string;
  finished_at: string | null;
  source: string;
  status: string;
  rows: number;
  error: string | null;
  counts?: Record<string, number>;
};

export type AuditEntry = {
  id: number;
  created_at: string;
  login: string | null;
  action: string;
  ip?: string | null;
  detail: string | null;
};

// --------------------------------------------------------------- layout ----

export type WidgetState = { id: string; span: number; visible: boolean; h?: number };
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

/** Ответ `/api/meta/freshness` — насколько свежи витринные данные. */
export type Freshness = {
  data_as_of: string | null;
  last_sync: {
    id: number;
    started_at: string | null;
    finished_at: string | null;
    status: string;
    source: string;
    rows: number;
  } | null;
  stale: boolean;
  sync_interval_minutes: number;
  server_time: string;
};

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

  // публичные настройки: пороги и периоды — чтобы UI и бэкенд считали одинаково
  publicSettings: () => request<Record<string, number>>("/api/settings/public"),
  freshness: () => request<Freshness>("/api/meta/freshness"),

  // ------------------------------------------- администрирование: пользователи ---
  adminUsers: () => request<AdminUser[]>("/api/admin/users"),
  adminCreateUser: (body: AdminUserCreate) =>
    request<AdminUser>("/api/admin/users", { method: "POST", body: JSON.stringify(body) }),
  adminPatchUser: (id: number, body: AdminUserPatch) =>
    request<AdminUser>(`/api/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  /** Временный пароль показывается один раз: бэкенд его не хранит в открытом виде. */
  adminResetPassword: (id: number) =>
    request<TempPassword>(`/api/admin/reset-password?user_id=${id}`, { method: "POST" }),
  adminUnlock: (id: number) => request<AdminUser>(`/api/admin/users/${id}/unlock`, { method: "POST" }),
  /** Мягкая деактивация: учётка остаётся в базе, токены отзываются. */
  adminDeleteUser: (id: number) => request<AdminUser>(`/api/admin/users/${id}`, { method: "DELETE" }),

  // -------------------------------------------------- администрирование: склады ---
  adminWarehouses: () => request<AdminWarehouse[]>("/api/admin/warehouses"),
  adminCreateWarehouse: (body: WarehouseInput) =>
    request<AdminWarehouse>("/api/admin/warehouses", { method: "POST", body: JSON.stringify(body) }),
  adminPatchWarehouse: (id: number, body: WarehouseInput) =>
    request<AdminWarehouse>(`/api/admin/warehouses/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  adminDeleteWarehouse: (id: number) =>
    request<AdminWarehouse>(`/api/admin/warehouses/${id}`, { method: "DELETE" }),

  // ------------------------------------- администрирование: настройки и 1С ---
  adminSettings: () => request<SettingsEnvelope>("/api/admin/settings"),
  adminSaveSettings: (patch: SettingsPatch) =>
    request<SettingsEnvelope>("/api/admin/settings", { method: "PUT", body: JSON.stringify(patch) }),
  /** Бэкенд ждёт голый JSON-массив ключей (не объект {keys: [...]}). */
  adminResetSettings: (keys: string[]) =>
    request<SettingsEnvelope>("/api/admin/settings/reset", { method: "POST", body: JSON.stringify(keys) }),
  onecTest: () => request<OneCTestResult>("/api/admin/onec/test", { method: "POST" }),
  syncRun: (days?: number) =>
    request<SyncRun>(`/api/admin/sync/run${days ? `?days=${days}` : ""}`, { method: "POST" }),
  syncRuns: (limit = 20) => request<SyncRun[]>(`/api/admin/sync/runs?limit=${limit}`),
  adminAudit: (limit = 100) => request<AuditEntry[]>(`/api/admin/audit?limit=${limit}`),
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
