"""Initial schema: iam, market, ops (PostgreSQL 18).

Uses PG18 features: built-in uuidv7() for time-ordered keys and a VIRTUAL generated column
(market.price_daily.mid). See docs/03-TECHNICAL-DESIGN.md §7.

Revision ID: 0001_initial
"""
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

DDL = r"""
CREATE SCHEMA IF NOT EXISTS iam;
CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS ops;

-- ── shared trigger: maintain updated_at ─────────────────────────────────────
CREATE OR REPLACE FUNCTION ops.touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- ═════════════════════════════════════ iam ═════════════════════════════════
CREATE TABLE iam.roles (
  id           smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code         varchar(32)  NOT NULL UNIQUE,
  name         varchar(64)  NOT NULL,
  description  text,
  permissions  text[]       NOT NULL DEFAULT '{}',
  is_system    boolean      NOT NULL DEFAULT false,
  created_at   timestamptz  NOT NULL DEFAULT now()
);

CREATE TABLE iam.modules (
  code         varchar(48)  PRIMARY KEY,
  name         varchar(80)  NOT NULL,
  description  text,
  category     varchar(24)  NOT NULL DEFAULT 'analytics',
  release      varchar(4)   NOT NULL DEFAULT 'R1',
  sort_order   smallint     NOT NULL DEFAULT 0,
  icon         varchar(32),
  is_enabled   boolean      NOT NULL DEFAULT true
);

CREATE TABLE iam.role_modules (
  role_id      smallint     NOT NULL REFERENCES iam.roles(id) ON DELETE CASCADE,
  module_code  varchar(48)  NOT NULL REFERENCES iam.modules(code) ON DELETE CASCADE,
  PRIMARY KEY (role_id, module_code)
);
CREATE INDEX ix_role_modules_module ON iam.role_modules(module_code);

CREATE TABLE iam.users (
  id                   uuid         PRIMARY KEY DEFAULT uuidv7(),
  email                varchar(254) NOT NULL,
  display_name         varchar(120) NOT NULL,
  role_id              smallint     NOT NULL REFERENCES iam.roles(id),
  auth_provider        varchar(8)   NOT NULL DEFAULT 'local'
                       CHECK (auth_provider IN ('local','sso','both')),
  password_hash        text,
  must_change_password boolean      NOT NULL DEFAULT false,
  hub_sub              varchar(128) UNIQUE,
  status               varchar(12)  NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  failed_login_count   smallint     NOT NULL DEFAULT 0,
  locked_until         timestamptz,
  last_login_at        timestamptz,
  last_login_method    varchar(8),
  password_changed_at  timestamptz,
  created_at           timestamptz  NOT NULL DEFAULT now(),
  updated_at           timestamptz  NOT NULL DEFAULT now(),
  created_by           uuid REFERENCES iam.users(id),
  updated_by           uuid REFERENCES iam.users(id),
  CONSTRAINT ck_users_password_required CHECK (auth_provider = 'sso' OR password_hash IS NOT NULL),
  CONSTRAINT ck_users_email_shape CHECK (position('@' in email) > 1)
);
CREATE UNIQUE INDEX ux_users_email ON iam.users (lower(email));
CREATE INDEX ix_users_role ON iam.users(role_id);
CREATE TRIGGER trg_users_updated BEFORE UPDATE ON iam.users
  FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

CREATE TABLE iam.user_module_overrides (
  user_id      uuid        NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
  module_code  varchar(48) NOT NULL REFERENCES iam.modules(code) ON DELETE CASCADE,
  effect       varchar(5)  NOT NULL CHECK (effect IN ('grant','deny')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  created_by   uuid REFERENCES iam.users(id),
  PRIMARY KEY (user_id, module_code)
);

CREATE TABLE iam.password_history (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id        uuid        NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
  password_hash  text        NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_password_history_user ON iam.password_history(user_id, created_at DESC);

CREATE TABLE iam.sessions (
  id               uuid         PRIMARY KEY DEFAULT uuidv7(),
  user_id          uuid         NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
  token_hash       bytea        NOT NULL UNIQUE,
  csrf_hash        bytea        NOT NULL,
  auth_method      varchar(8)   NOT NULL CHECK (auth_method IN ('local','sso')),
  created_at       timestamptz  NOT NULL DEFAULT now(),
  last_seen_at     timestamptz  NOT NULL DEFAULT now(),
  idle_expires_at  timestamptz  NOT NULL,
  expires_at       timestamptz  NOT NULL,
  revoked_at       timestamptz,
  revoked_reason   varchar(32),
  ip               inet,
  user_agent       varchar(512)
);
CREATE INDEX ix_sessions_user_active ON iam.sessions(user_id) WHERE revoked_at IS NULL;
CREATE INDEX ix_sessions_expires ON iam.sessions(expires_at);

CREATE TABLE iam.oidc_login_states (
  state_hash         bytea        PRIMARY KEY,
  code_verifier_enc  text         NOT NULL,
  nonce              varchar(128) NOT NULL,
  next_path          varchar(512),
  ip                 inet,
  created_at         timestamptz  NOT NULL DEFAULT now(),
  expires_at         timestamptz  NOT NULL,
  consumed_at        timestamptz
);
CREATE INDEX ix_oidc_states_expires ON iam.oidc_login_states(expires_at);

-- ═════════════════════════════════════ ops ═════════════════════════════════
CREATE TABLE ops.audit_logs (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at    timestamptz  NOT NULL DEFAULT now(),
  actor_user_id  uuid REFERENCES iam.users(id) ON DELETE SET NULL,
  actor_email    varchar(254),
  action         varchar(64)  NOT NULL,
  target_type    varchar(32),
  target_id      varchar(128),
  outcome        varchar(8)   NOT NULL DEFAULT 'success' CHECK (outcome IN ('success','failure')),
  ip             inet,
  user_agent     varchar(512),
  request_id     varchar(64),
  details        jsonb        NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_audit_occurred_brin ON ops.audit_logs USING brin (occurred_at);
CREATE INDEX ix_audit_action ON ops.audit_logs(action, occurred_at DESC);
CREATE INDEX ix_audit_actor ON ops.audit_logs(actor_user_id, occurred_at DESC);

-- Append-only, defence in depth (grants already withhold UPDATE/DELETE from aastar_app).
CREATE OR REPLACE FUNCTION ops.audit_logs_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'ops.audit_logs is append-only';
END $$;
CREATE TRIGGER trg_audit_immutable BEFORE UPDATE OR DELETE ON ops.audit_logs
  FOR EACH ROW EXECUTE FUNCTION ops.audit_logs_immutable();

CREATE TABLE ops.app_settings (
  key          varchar(64) PRIMARY KEY,
  value        jsonb       NOT NULL,
  description  text,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  updated_by   uuid REFERENCES iam.users(id)
);

CREATE TABLE ops.integration_tokens (
  provider     varchar(32) PRIMARY KEY,
  token_enc    text        NOT NULL,
  obtained_at  timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL
);

CREATE SEQUENCE ops.data_version_seq;

CREATE TABLE ops.pipeline_runs (
  id               uuid        PRIMARY KEY DEFAULT uuidv7(),
  kind             varchar(16) NOT NULL CHECK (kind IN ('incremental','full','recompute','demo_seed')),
  trigger          varchar(12) NOT NULL CHECK (trigger IN ('schedule','manual','cli','startup')),
  status           varchar(12) NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued','running','succeeded','partial','failed')),
  triggered_by     uuid REFERENCES iam.users(id),
  queued_at        timestamptz NOT NULL DEFAULT now(),
  started_at       timestamptz,
  finished_at      timestamptz,
  rows_fetched     integer     NOT NULL DEFAULT 0,
  rows_upserted    integer     NOT NULL DEFAULT 0,
  series_computed  integer     NOT NULL DEFAULT 0,
  data_version     bigint      UNIQUE,
  warnings_count   integer     NOT NULL DEFAULT 0,
  error            text,
  params           jsonb       NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX ux_pipeline_one_running ON ops.pipeline_runs((true)) WHERE status = 'running';
CREATE INDEX ix_pipeline_runs_queued ON ops.pipeline_runs(queued_at DESC);

CREATE TABLE ops.pipeline_run_steps (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id       uuid        NOT NULL REFERENCES ops.pipeline_runs(id) ON DELETE CASCADE,
  seq          smallint    NOT NULL,
  step         varchar(32) NOT NULL,
  status       varchar(12) NOT NULL CHECK (status IN ('running','succeeded','failed','skipped')),
  started_at   timestamptz,
  finished_at  timestamptz,
  message      text,
  metrics      jsonb       NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_pipeline_steps_run ON ops.pipeline_run_steps(run_id, seq);

CREATE TABLE ops.dq_findings (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id      uuid        NOT NULL REFERENCES ops.pipeline_runs(id) ON DELETE CASCADE,
  check_code  varchar(32) NOT NULL,
  severity    varchar(8)  NOT NULL CHECK (severity IN ('info','warn','error')),
  ric         varchar(32),
  obs_date    date,
  detail      jsonb       NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_dq_findings_run ON ops.dq_findings(run_id);

-- ═════════════════════════════════════ market ══════════════════════════════
CREATE TABLE market.products (
  code               varchar(8)   PRIMARY KEY,
  display_name       varchar(80)  NOT NULL,
  oi_display_name    varchar(80),
  exchange           varchar(32),
  native_unit        varchar(24)  NOT NULL,
  conversion_type    varchar(8)   NOT NULL CHECK (conversion_type IN ('none','fx_div','factor')),
  conversion_factor  numeric(12,6),
  conversion_fx_ric  varchar(32),
  oi_contracts       smallint     NOT NULL DEFAULT 7,
  oi_display_order   smallint,
  sort_order         smallint     NOT NULL DEFAULT 0
);

CREATE TABLE market.instruments (
  ric                varchar(32)  PRIMARY KEY,
  product_code       varchar(8)   REFERENCES market.products(code),
  category           varchar(24)  NOT NULL CHECK (category IN ('futures','fx','fx_usd','malaysia_cash',
                       'indonesia_cash','us_cash','sa_cash','china_cash','blacksea_cash')),
  contract_no        smallint,
  display_name       varchar(120) NOT NULL,
  native_unit        varchar(24)  NOT NULL,
  conversion_type    varchar(8)   NOT NULL CHECK (conversion_type IN ('none','fx_div','factor')),
  conversion_factor  numeric(12,6),
  conversion_fx_ric  varchar(32),
  price_basis        varchar(8)   NOT NULL DEFAULT 'last' CHECK (price_basis IN ('last','mid')),
  universe           varchar(12)  NOT NULL DEFAULT 'core' CHECK (universe IN ('core','oi_extended')),
  is_active          boolean      NOT NULL DEFAULT true,
  created_at         timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_instruments_product ON market.instruments(product_code, contract_no);

CREATE TABLE market.price_daily (
  ric               varchar(32)   NOT NULL REFERENCES market.instruments(ric),
  trade_date        date          NOT NULL,
  last_price        numeric(18,6),
  settlement_price  numeric(18,6),
  bid               numeric(18,6),
  ask               numeric(18,6),
  mid               numeric GENERATED ALWAYS AS ((bid + ask) / 2) VIRTUAL,
  price             numeric(18,6),
  volume            bigint,
  open_interest     bigint,
  source            varchar(8)    NOT NULL DEFAULT 'lseg' CHECK (source IN ('lseg','demo')),
  run_id            uuid REFERENCES ops.pipeline_runs(id) ON DELETE SET NULL,
  updated_at        timestamptz   NOT NULL DEFAULT now(),
  PRIMARY KEY (ric, trade_date)
) WITH (fillfactor = 90);
CREATE INDEX ix_price_daily_date ON market.price_daily(trade_date);

CREATE TABLE market.series (
  key          varchar(64)  PRIMARY KEY,
  module_code  varchar(48)  NOT NULL REFERENCES iam.modules(code),
  group_name   varchar(64)  NOT NULL,
  title        varchar(160) NOT NULL,
  kind         varchar(16)  NOT NULL,
  family       varchar(32),
  tenor        varchar(4),
  unit         varchar(24)  NOT NULL DEFAULT 'USD/MT',
  local_unit   varchar(24),
  definition   jsonb        NOT NULL DEFAULT '{}',
  sort_order   integer      NOT NULL DEFAULT 0,
  is_active    boolean      NOT NULL DEFAULT true,
  updated_at   timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_series_module ON market.series(module_code, sort_order);

CREATE TABLE market.series_points (
  series_key   varchar(64)      NOT NULL REFERENCES market.series(key) ON DELETE CASCADE,
  obs_date     date             NOT NULL,
  value        double precision NOT NULL,
  value_local  double precision,
  PRIMARY KEY (series_key, obs_date)
);

CREATE TABLE market.oi_cumulative (
  product_code         varchar(8) NOT NULL REFERENCES market.products(code),
  obs_date             date       NOT NULL,
  total_oi             bigint     NOT NULL,
  oi_chg               bigint,
  total_volume         bigint,
  volume_chg           bigint,
  oi_vol_pct           double precision,
  avg_price            double precision,
  price_chg_pct        double precision,
  contracts_reporting  smallint,
  PRIMARY KEY (product_code, obs_date)
);

CREATE TABLE market.oi_arrow_thresholds (
  product_code  varchar(8)       PRIMARY KEY REFERENCES market.products(code),
  median_pct    double precision NOT NULL,
  p75_pct       double precision NOT NULL,
  n_obs         integer          NOT NULL,
  window_start  date             NOT NULL,
  computed_at   timestamptz      NOT NULL DEFAULT now()
);

-- ── grants for the least-privilege runtime role (created by deploy/rds/bootstrap_roles.sql) ──
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aastar_app') THEN
    GRANT USAGE ON SCHEMA iam, market, ops TO aastar_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA iam, market TO aastar_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ops.app_settings, ops.integration_tokens,
          ops.pipeline_runs, ops.pipeline_run_steps, ops.dq_findings TO aastar_app;
    GRANT SELECT, INSERT ON ops.audit_logs TO aastar_app;
    GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA iam, market, ops TO aastar_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aastar_ro') THEN
    GRANT USAGE ON SCHEMA market, ops TO aastar_ro;
    GRANT SELECT ON ALL TABLES IN SCHEMA market, ops TO aastar_ro;
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS market CASCADE; DROP SCHEMA IF EXISTS ops CASCADE; "
               "DROP SCHEMA IF EXISTS iam CASCADE;")
