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
  groups: { name: string; section?: string | null; series: SeriesRef[] }[];
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

export interface XYLine {
  label: string;
  x: string[];
  y: number[];
}

/** Calendar Spreads → YTD: one futures product in one year (legacy make_ytd_spread_chart). */
export interface YtdProduct {
  key: string;
  title: string;
  unit: string;
  year: number;
  legs: XYLine[];
  front: XYLine | null;
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

// ── Intelligence sections ───────────────────────────────────────────────────

export interface JobInfo {
  id: string;
  kind: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  trigger: string;
  result: Record<string, unknown> | null;
  error: string | null;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface SndTrace { year: number; x: string[]; y: (number | null)[] }
export interface SndCountry { name: string; has_source: boolean; items: { name: string; unit: string; traces: SndTrace[] }[] }
export interface SndTable {
  code: string; title: string; year: number | null; missing?: boolean;
  headers?: string[]; rows?: string[][]; bmd?: { as_of: string | null; months: Record<string, number> } | null;
}
export interface SndSource {
  role: string; year: number; filename: string; sheet: string | null; rows: number; uploaded_at: string;
  uploaded_by: string | null;
}

export interface CftcProduct {
  code: string; label: string; market_name: string; report_date: string; open_interest: number | null;
  mm_net: number | null; swap_net: number | null; pm_net: number | null;
  mm_net_pct_oi: number | null; swap_net_pct_oi: number | null; pm_net_pct_oi: number | null;
  wow_mm_net: number | null; wow_swap_net: number | null; wow_pm_net: number | null;
  history: { dates: string[]; mm_net: (number | null)[]; swap_net: (number | null)[]; pm_net: (number | null)[] };
}
export interface CftcPayload {
  as_of: string | null; weeks: number; products: CftcProduct[]; categories: [string, string][];
  job: JobInfo | null; can_refresh: boolean;
}

export interface CalendarEvent {
  event_date: string; day: string; event: string; agency: string; region: string; category: string;
  source_url: string; notes: string;
}

export interface AtlasPlant {
  name: string; company: string; country: string; region: string; lat: number; lon: number;
  status: 'op' | 'con' | 'ann' | 'can'; type: 'standalone' | 'copro'; kt: number; safkt: number | null;
  quoted: string; startup: string; note: string; est: boolean;
}

export interface AiReport {
  id: string; instrument: string; instrument_label: string; timeframe: string;
  status: 'pending' | 'ready' | 'failed' | 'skipped'; error: string | null; narrative: string | null;
  raw_text: string; model: string | null; created_at: string; finished_at: string | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  sections: Record<string, any>;
}

export interface NewsDocument {
  id: string; filename: string; size_bytes: number; doc_type: string | null; type_source: string | null;
  doc_date: string; status: 'queued' | 'processing' | 'succeeded' | 'failed'; error: string | null;
  num_prices: number | null; num_sentiment: number | null; uploaded_at: string; processed_at: string | null;
  uploaded_by: string | null;
}

// ── Google Sheet sources (Waste/Bio, Tradable Prem, India/China parity) ──────────────────────────────────────

export interface SnapshotRecord { label: string; x: string[]; y: number[] }
export interface SnapshotBlock { key: string; title: string; period: 'daily' | 'weekly'; records: SnapshotRecord[] }
export interface ParitySnapshots {
  india: SnapshotBlock[];
  china: SnapshotBlock[];
  india_updated_at: string | null;
  china_updated_at: string | null;
}

export interface MipQuarter {
  year: number; quarter: number; label: string; window_start: string; window_end: string; q_start: string;
  q_end: string; avg_sbo_usd: number; eurusd: number; fx_date: string; mip_eur: number; n_days: number;
}
export interface ArbMonth {
  month: string; maturity: string; sme_landed_eur: number; fame0_usd: number | null; fame0_eur: number | null;
  diff_eur: number | null; status: 'open' | 'closed' | null;
}
export interface ArbPayload {
  constants: { conversion_eur: number; freight_usd: number; import_duty: number; rme_fraction: number; fame_maturity: number };
  quarters: MipQuarter[];
  current: MipQuarter | null;
  sme: null | {
    eur_oil: number; eur_conversion: number; eur_freight: number; subtotal: number; duty: number; landed: number;
    live_eurusd: number; rme_m1: number | null; fame0_m1: number | null; rme_premium: number | null; value: number;
    months: ArbMonth[];
  };
  china_ucome: null | {
    china_fob: number; freight: number; trucking: number; fobbing: number; china_landed: number; china_weight: number;
    china_weighted: number; pome_fob: number; net_costs: number; pome_reference: number; pome_weight: number;
    pome_weighted: number; blended: number; diff: number; status: 'open' | 'closed';
  };
}
export interface TicketsCalc {
  assumptions: string;
  rows: { market: string; ticket: string; formula: string; example: string }[];
}

export interface SheetSource {
  code: string; title: string; url: string; access: 'public' | 'private'; feeds: string;
  tabs: { title: string; gid: number }[]; series_defined: number;
  status: 'never' | 'ok' | 'failed' | 'skipped'; method: 'csv' | 'api' | 'upload' | null;
  pulled_at: string | null; error: string | null;
  detail: { series?: number; last?: string; first?: string; rows?: number; upserted?: number; deleted?: number };
  upload: null | { filename: string; uploaded_at: string; uploaded_by: string | null };
}
export interface SheetSourcesPayload {
  sources: SheetSource[];
  job: null | { id: string; status: string; trigger: string; error: string | null; queued_at: string;
                finished_at: string | null; result: Record<string, unknown> | null };
  service_account: { configured: boolean; email: string | null; origin: 'admin' | 'file' | null };
}
