export interface AuthConfig {
  local_enabled: boolean;
  sso_enabled: boolean;
  sso_label: string;
  app_version: string;
  release: string;
  environment: string;
  data_mode: 'demo' | 'live';
}

export interface Me {
  id: string;
  email: string;
  display_name: string;
  role: string;
  role_name: string;
  permissions: string[];
  modules: string[];
  hub_sub: string | null;
  auth_provider: 'local' | 'sso' | 'both';
  auth_method: 'local' | 'sso';
}

export interface SeriesRef {
  key: string;
  title: string;
  unit: string;
  local_unit: string | null;
  family: string | null;
}

export interface ModuleCatalog {
  code: string;
  name: string;
  description: string;
  nav_group: string;
  icon: string;
  kind: 'seasonal' | 'overview' | 'forward' | 'oi' | 'ta';
  has_local_units: boolean;
  has_tenor: boolean;
  groups: { name: string; series: SeriesRef[] }[];
}

export interface Trace {
  year: number;
  x: string[];
  y: (number | null)[];
  as_of?: string;
}

export interface SeriesPayload {
  key: string;
  title: string;
  group?: string;
  unit: string;
  traces: Trace[];
  last: { date: string; value: number } | null;
}

export interface YearsInfo {
  years: number[];
  default: number[];
  data_version: number;
  as_of: string | null;
}

export interface Tile {
  ric: string;
  title: string;
  unit: string;
  value: number | null;
  as_of?: string;
  change?: number | null;
  change_pct?: number | null;
  usd_mt?: number | null;
  spark?: number[];
}

export interface Overview {
  tiles: Tile[];
  latest_trade_date: string | null;
  last_run: { id: string; kind: string; status: string; finished_at: string } | null;
  series_defined: number;
  series_with_data: number;
  instruments: number;
}

export interface Curve {
  key: string;
  title: string;
  unit: string;
  traces: Trace[];
}

export interface OiRow {
  date: string;
  total_oi: number;
  oi_chg: number | null;
  total_volume: number | null;
  volume_chg: number | null;
  oi_vol_pct: number | null;
  oi_vol_flag: 'none' | 'normal' | 'notable' | 'anomaly';
  avg_price: number | null;
  price_chg_pct: number | null;
  arrow: 'up' | 'down' | 'flat';
  arrow_n: number;
  contracts: number | null;
}

export interface OiTable {
  product: string;
  title: string;
  unit: string;
  contracts_scanned: number;
  threshold_median: number;
  threshold_p75: number;
  rows: OiRow[];
}

export interface TaInstrument {
  key: string;
  title: string;
  kind: 'price' | 'spread';
  native_unit: string;
}

export interface TaPayload {
  key: string;
  title: string;
  unit: string | null;
  dates: string[];
  price: (number | null)[];
  sma50: (number | null)[];
  sma100: (number | null)[];
  sma200: (number | null)[];
  levels: number[];
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  role: string;
  role_name: string;
  auth_provider: 'local' | 'sso' | 'both';
  status: 'active' | 'disabled';
  hub_linked: boolean;
  locked: boolean;
  last_login_at: string | null;
  last_login_method: string | null;
  created_at: string;
  updated_at: string;
  overrides: { module: string; effect: 'grant' | 'deny' }[];
  effective_modules: string[];
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Role {
  code: string;
  name: string;
  description: string;
  permissions: string[];
  is_system: boolean;
  modules: string[];
  user_count: number;
}

export interface AdminModule {
  code: string;
  name: string;
  description: string;
  release: string;
  is_enabled: boolean;
}

export interface AuditEntry {
  id: number;
  occurred_at: string;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  outcome: 'success' | 'failure';
  ip: string | null;
  request_id: string | null;
  details: Record<string, unknown>;
}

export interface PipelineRun {
  id: string;
  kind: string;
  trigger: string;
  status: 'queued' | 'running' | 'succeeded' | 'partial' | 'failed';
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  rows_fetched: number;
  rows_upserted: number;
  series_computed: number;
  data_version: number | null;
  warnings_count: number;
  error: string | null;
  duration_s: number | null;
  steps?: {
    seq: number;
    step: string;
    status: string;
    started_at: string | null;
    finished_at: string | null;
    message: string | null;
    metrics: Record<string, unknown>;
  }[];
  findings?: { check: string; severity: string; ric: string | null; date: string | null; detail: Record<string, unknown> }[];
}

export interface PipelineStatus {
  data_mode: 'demo' | 'live';
  lseg_configured: boolean;
  schedule: { cron: string; timezone: string };
  last_success: PipelineRun | null;
  active: PipelineRun | null;
  stats: { price_rows: number; latest_trade_date: string | null; series_points: number; rics_with_data: number };
}

export interface SettingsView {
  editable: { key: string; value: boolean | number | string; type: string; description: string }[];
  integrations: {
    environment: string;
    version: string;
    release: string;
    data_mode: string;
    lseg: { configured: boolean; base_url: string; token_obtained_at: string | null; token_expires_at: string | null };
    sso: { enabled: boolean; configured: boolean; issuer: string; client_id: string | null; redirect_uri: string; launch_url: string };
    local_login_enabled: boolean;
    pipeline: { cron: string; timezone: string };
  };
}
