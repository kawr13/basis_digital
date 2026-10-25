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
  /** право на «Постановку судов»: null = по роли, иначе none|view|edit|manage */
  vessel_right?: VesselRight | null;
  /** те же права, но уже посчитанные бэкендом; приходят со входа и /me */
  vessel_rights?: VesselRights;
};

export type VesselRights = { view: boolean; edit: boolean; delete: boolean };

export type VesselRight = "none" | "view" | "edit" | "manage";

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


// ------------------------------------------------- статистика (отчёт диспетчера) ---

export type DispatcherMeta = {
  date_min: string | null;
  date_max: string | null;
  default_period_days: number;
  shifts: { shift: string; count: number }[];
  updated_at: string | null;
  sources: { source: string; count: number }[];
};

export type DispatcherKpi = {
  throughput_t: number;
  plan_shift_t: number;
  plan_pct: number | null;
  delta_pct: number | null;
  prev_throughput_t: number;
  calls: number;
  vessels_unique: number;
  avg_tph: number;
  machine_h: number;
  downtime_h: number;
  downtime_pct: number | null;
  rail_cars: number;
  trucks: number;
  crew_present: number;
  crew_staff: number;
  crew_present_total: number;
  crew_rate: number | null;
  rs_availability: number | null;
  days: number;
  shifts: number;
};

export type DispatcherDayPoint = {
  bucket_date: string;
  throughput_t: number;
  plan_shift_t: number;
  plan_pct: number | null;
  calls: number;
  machine_h: number;
  downtime_h: number;
  downtime_pct: number | null;
  rail_cars: number;
  trucks: number;
  present: number;
  crew: number;
  contractors: number;
  day_off: number;
  sick: number;
  vacation: number;
  other_absent: number;
  staff: number;
  crew_rate: number | null;
  shifts: number;
  rs_ok_start: number;
  rs_repair_start: number;
  rs_ok_end: number;
  rs_repair_end: number;
  rs_total: number;
  rs_availability: number | null;
};

export type DispatcherOverview = {
  period: { from: string; to: string; days: number; requested_from?: string | null; requested_to?: string | null };
  shift_filter: string;
  meta: { date_min: string | null; date_max: string | null; updated_at: string | null; sources: string[] };
  kpi: DispatcherKpi;
  dynamics: DispatcherDayPoint[];
  cargo_mix: {
    cargo_group: string; tons: number; calls: number; plan_t: number;
    machine_h: number; downtime_h: number; tph: number; share: number | null;
  }[];
  direction: { import_t: number; export_t: number };
  shift_split: {
    shift: string; label: string; throughput_t: number; plan_pct: number | null;
    calls: number; avg_tph: number; downtime_pct: number | null;
  }[];
  berths: {
    berth: string; calls: number; throughput_t: number; vessels: number;
    machine_h: number; downtime_h: number; downtime_pct: number | null;
  }[];
  vessels_top: {
    vessel: string; calls: number; tons: number; machine_h: number;
    downtime_h: number; tph: number; cargos: string[];
  }[];
  weekday: { weekday: number; label: string; avg_throughput_t: number; days: number }[];
  rail: {
    totals: Record<string, number>;
    by_cargo: {
      cargo_group: string; processed: number; start_loaded: number; start_empty: number;
      end_loaded: number; end_empty: number;
    }[];
  };
  trucks: {
    totals: Record<string, number>;
    by_cargo: {
      cargo_group: string; imp_unload: number; exp_unload: number; imp_load: number;
      exp_load: number; direct_vessel: number; total: number;
    }[];
    by_day: {
      bucket_date: string; imp_unload: number; exp_unload: number; imp_load: number;
      exp_load: number; direct_vessel: number; total: number;
    }[];
  };
  arrivals: {
    eta: string | null; vessel: string; berth: string; work_kind: string;
    cargo_group: string; plan_t: number; plan_raw: string; agent: string; report_date: string;
  }[];
  rail_arrivals: {
    station: string; cars: number; trains: number; cargos: string[]; work_kinds: string[];
  }[];
  /** Блоки листа «ВЫВОД» и «ВВОД»: выводка и постановка вагонов. */
  rail_moves: {
    totals: {
      out_cars: number; in_cars: number; in_loaded: number; in_empty: number;
      trains_out: number; avg_delay_min: number | null; late_trains: number;
    };
    by_day: {
      bucket_date: string; out_cars: number; in_cars: number; in_loaded: number;
      in_empty: number; late: number; avg_delay_min: number | null;
    }[];
    by_wagon: { wagon_type: string; out: number; in: number }[];
  };
  /** Платформы, выведенные из аренды: остаток на конец периода и сдвиг. */
  platforms: {
    owner: string; total_cars: number; remaining: number; terminal: number;
    sent: number; arrived_loaded: number; remaining_delta: number;
  }[];
};

/** Таблица-детализация: строки витрины, из которых собран график. */
export type DispatcherRowsResult = {
  kind: string; key: string | null; title: string;
  columns: { key: string; label: string; right?: boolean; sum?: boolean }[];
  rows: Record<string, string | number | null>[];
  totals: Record<string, number | null>;
  period: { from: string; to: string; shift: string | null };
};

export type DispatcherRowKind =
  | "vessel" | "berth" | "cargo" | "rail_cargo" | "truck_cargo"
  | "station" | "rail_move" | "platform";

export type DispatcherDay = {
  date: string;
  shifts: {
    shift: string; label: string; dispatcher: string; crew_no: number | null;
    present: number; crew: number; staff: number; sick: number; vacation: number;
    contractors: { name: string; count: number }[];
    rs_ok_start: number; rs_repair_start: number; rs_ok_end: number; rs_repair_end: number;
  }[];
  vessels: {
    shift: string; berth: string; vessel: string; cargo_group: string;
    plan_shift_t: number; fact_shift_t: number; plan_pct: number | null;
    plan_total_t: number; remaining_t: number; tph: number; moves: number;
    machine_h: number; downtime_h: number; note: string;
  }[];
  rails: { shift: string; cargo_group: string; processed: number; port_start: number; port_end: number }[];
  trucks: {
    shift: string; cargo_group: string; total: number; imp_unload: number;
    exp_unload: number; imp_load: number; exp_load: number; direct_vessel: number;
  }[];
  arrivals: {
    eta: string | null; vessel: string; berth: string; work_kind: string;
    cargo_group: string; plan_raw: string; agent: string;
  }[];
  rail_out: {
    shift: string; move_date: string | null; plan_time: string; semi_wagons: number;
    covered: number; flatcars: number; total: number; accepted_by: string;
    fact_cars: number; fact_date: string | null; fact_time: string; delay_min: number | null;
  }[];
  rail_in: {
    shift: string; move_date: string | null; plan_time: string; settle_time: string;
    load_semi: number; load_covered: number; load_flat: number;
    empty_semi: number; empty_covered: number; empty_flat: number; total: number;
  }[];
  platforms: {
    shift: string; owner: string; total_cars: number; remaining: number;
    terminal: number; sent: number; arrived_loaded: number;
  }[];
};

export type DispatcherImportResult = {
  imported: Record<string, number>;
  described: string;
  source: string;
  date_min: string;
  date_max: string;
  total: Record<string, number>;
};

export type DispatcherStatus = {
  configured_path: string;
  exists: boolean;
  mtime: string | null;
  auto_import: boolean;
  counts: Record<string, number>;
  /** Границы того, что реально лежит в витрине, и момент последней загрузки. */
  date_min: string | null;
  date_max: string | null;
  updated_at: string | null;
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
  vessel_right?: VesselRight | null;
};

export type AdminUserPatch = {
  full_name?: string;
  role?: "admin" | "top" | "manager";
  is_active?: boolean;
  password?: string;
  warehouse_ids?: number[];
  vessel_right?: VesselRight | null;
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
  kind: "str" | "int" | "float" | "bool" | "choice" | "str_list";
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
  settings: Record<string, string | number | boolean | string[]>;
  defaults: Record<string, string | number | boolean | string[]>;
  overridden: string[];
  specs: Record<string, SettingSpec>;
  scheduler?: SchedulerReport | null;
};

export type SettingsPatch = Record<string, string | number | boolean | string[]>;

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
/**
 * Ротация по refresh-cookie наружу. Нужна при старте страницы: access-токен
 * живёт только в памяти, поэтому первый /me после перезагрузки заведомо 401
 * и в консоли каждый раз мелькает «Unauthorized», хотя сессия жива.
 */
export async function refreshSession(): Promise<boolean> {
  return refresh();
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

/**
 * Тот же путь с авторотацией, но тело ответа — бинарник (PDF), а не JSON.
 * Размножать обработку 401 не хочется, поэтому она здесь короткая копия.
 */
async function requestBlob(path: string, init: RequestInit = {}, retry = true): Promise<Blob> {
  const res = await raw(path, init);
  if (res.status === 401 && retry && (await refresh())) return requestBlob(path, init, false);
  if (res.status === 401) {
    onExpired?.();
    throw new ApiError(401, "Сессия истекла, войдите заново");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* тело не JSON */
    }
    throw new ApiError(res.status, String(detail));
  }
  return res.blob();
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

/** ---------- постановка судов (ручной планировочный документ) ---------- */

export type Berth = {
  id: number;
  code: string;
  depth: number | null;
  length_m: number | null;
  lanes: number;
  sort_order: number;
  is_active: boolean;
  /** ячеек шкалы длины (10 м на ячейку): 130 м → 13. Считает бэкенд. */
  length_cells: number;
};

export type BerthInput = {
  code: string;
  depth?: number | null;
  length_m?: number | null;
  lanes?: number;
  sort_order?: number;
  is_active?: boolean;
};

export type Placement = {
  id: number;
  berth_id: number;
  lane: number;
  /** длина судна в метрах; null — неизвестна, занимает причал до конца */
  length_m: number | null;
  /** отметка швартовки в метрах от начала причала */
  offset_m: number;
  vessel: string;
  volume: string | null;
  cargo: string | null;
  note: string | null;
  starts_at: string;
  ends_at: string;
  /** перешвартовка: id постановки-оригинала, если это копия (к ней тянется стрелка) */
  moved_from_id?: number | null;
  /** дата перестановки наступила: копия обычная, без стрелки и пунктира */
  committed?: boolean;
  /** действительный конец оригинала: обрезан на старте состоявшейся копии */
  effective_ends_at?: string | null;
  /** судно сейчас на другом терминале — красная рамка на плане; без флага — в проливе */
  at_other_terminal?: boolean;
  /** цвет полосы вручную (#rrggbb); пусто — автоматом по грузу */
  color?: string | null;
  created_at: string;
  updated_at: string;
};

export type PlacementInput = {
  berth_id: number;
  lane: number;
  length_m: number | null;
  offset_m: number;
  vessel: string;
  volume?: string | null;
  cargo?: string | null;
  note?: string | null;
  color?: string | null;
  starts_at: string;
  ends_at: string;
  moved_from_id?: number | null;
  at_other_terminal?: boolean;
};

/** Ответ /api/vessels/free-offset — где судно такой длины встаёт без пересечений. */
export type FreeOffset = {
  offset_m: number | null;
  length_m: number;
  berth_length_m: number;
  fits: boolean;
};

export type VesselPlan = {
  period: { from: string; to: string };
  slot_hours: number[];
  berths: Berth[];
  placements: Placement[];
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
  publicSettings: () => request<Record<string, number | string>>("/api/settings/public"),
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
  /** Выключить учётку: вход запрещён, живые сессии отзваны, учётка остаётся в списке. */
  adminSetUserActive: (id: number, active: boolean) =>
    request<AdminUser>(`/api/admin/users/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: active }),
    }),
  /**
   * Удалить учётку целиком. Постановки и журнал остаются, но без автора: для
   * «увольнения» нужен adminSetUserActive, а не это.
   */
  adminDeleteUser: (id: number) => request<void>(`/api/admin/users/${id}`, { method: "DELETE" }),


  // ------------------------------------- статистика: отчёт диспетчера порта ---
  dispatcherMeta: () => request<DispatcherMeta>("/api/dispatcher/meta"),
  dispatcherOverview: (p: { from?: string; to?: string; shift?: string }) => {
    const q = new URLSearchParams();
    if (p.from) q.set("from", p.from);
    if (p.to) q.set("to", p.to);
    if (p.shift && p.shift !== "all") q.set("shift", p.shift);
    const tail = q.toString();
    return request<DispatcherOverview>(`/api/dispatcher/overview${tail ? `?${tail}` : ""}`);
  },
  dispatcherDay: (day: string, shift?: string) =>
    request<DispatcherDay>(
      `/api/dispatcher/day?date=${day}${shift && shift !== "all" ? `&shift=${shift}` : ""}`,
    ),
  dispatcherRows: (p: {
    from?: string; to?: string; shift?: string; kind: DispatcherRowKind; key?: string;
  }) => {
    const q = new URLSearchParams();
    if (p.from) q.set("from", p.from);
    if (p.to) q.set("to", p.to);
    if (p.shift && p.shift !== "all") q.set("shift", p.shift);
    q.set("kind", p.kind);
    if (p.key) q.set("key", p.key);
    return request<DispatcherRowsResult>(`/api/dispatcher/rows?${q.toString()}`);
  },
  dispatcherStatus: () => request<DispatcherStatus>("/api/admin/dispatcher/status"),
  dispatcherImport: (body: { path?: string; content_base64?: string; filename?: string }) =>
    request<DispatcherImportResult>("/api/admin/dispatcher/import", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // -------------------------------------------------- администрирование: склады ---
  adminWarehouses: () => request<AdminWarehouse[]>("/api/admin/warehouses"),
  adminCreateWarehouse: (body: WarehouseInput) =>
    request<AdminWarehouse>("/api/admin/warehouses", { method: "POST", body: JSON.stringify(body) }),
  adminPatchWarehouse: (id: number, body: WarehouseInput) =>
    request<AdminWarehouse>(`/api/admin/warehouses/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  /**
   * Удалить склад. Без статистики удаляется сразу; по складу со строками витрины
   * бэкенд отвечает 409 и просит purge=true — иначе вместе со складом стираются
   * отчёты за этот склад.
   */
  adminDeleteWarehouse: (id: number, purge = false) =>
    request<void>(`/api/admin/warehouses/${id}${purge ? "?purge=true" : ""}`, { method: "DELETE" }),

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

  // ---- постановка судов ----
  berths: () => request<Berth[]>("/api/vessels/berths"),
  createBerth: (body: BerthInput) =>
    request<Berth>("/api/vessels/berths", { method: "POST", body: JSON.stringify(body) }),
  patchBerth: (id: number, body: BerthInput) =>
    request<Berth>(`/api/vessels/berths/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteBerth: (id: number) => request<void>(`/api/vessels/berths/${id}`, { method: "DELETE" }),
  /**
   * PDF видимого куска плана. Файл рисует бэкенд (вектор, кириллица текстом),
   * браузер его только сохраняет — печать браузера для этого не используется.
   */
  planPdf: (from: string, to: string, dayFrom: number, dayTo: number, berthIds: number[]) => {
    const q = new URLSearchParams({
      from,
      to,
      day_from: String(dayFrom),
      day_to: String(dayTo),
      ...(berthIds.length ? { berth_ids: berthIds.join(",") } : {}),
    });
    return requestBlob(`/api/vessels/plan.pdf?${q}`);
  },
  vesselPlan: (from?: string, to?: string, berthId?: number) => {
    const q = new URLSearchParams();
    if (from) q.set("from", from);
    if (to) q.set("to", to);
    if (berthId) q.set("berth_id", String(berthId));
    const tail = q.toString();
    return request<VesselPlan>(`/api/vessels/placements${tail ? `?${tail}` : ""}`);
  },
  createPlacement: (body: PlacementInput) =>
    request<Placement>("/api/vessels/placements", { method: "POST", body: JSON.stringify(body) }),
  updatePlacement: (id: number, body: Partial<PlacementInput>) =>
    request<Placement>(`/api/vessels/placements/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deletePlacement: (id: number) => request<void>(`/api/vessels/placements/${id}`, { method: "DELETE" }),
  freeOffset: (berthId: number, startsAt: string, endsAt: string, lengthM: number) => {
    const q = new URLSearchParams({
      berth_id: String(berthId),
      starts_at: startsAt,
      ends_at: endsAt,
      length_m: String(lengthM),
    });
    return request<FreeOffset>(`/api/vessels/free-offset?${q}`);
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
