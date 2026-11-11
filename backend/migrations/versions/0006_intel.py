"""Intelligence sections: SND, CFTC, AI, News, Calendar, HVO/SAF Atlas (legacy parity, R2/R3 scope).

- ops.jobs: background work the worker drains (sheet/CFTC/calendar refreshes, document extraction, AI narratives)
- intel.*: the data those sections show

Revision ID: 0006_intel
"""
from alembic import op

revision = "0006_intel"
down_revision = "0005_drop_must_change_password"
branch_labels = None
depends_on = None

DDL = r"""
CREATE SCHEMA IF NOT EXISTS intel;

-- ── background jobs ─────────────────────────────────────────────────────────
CREATE TABLE ops.jobs (
  id            uuid        PRIMARY KEY DEFAULT uuidv7(),
  kind          varchar(32) NOT NULL CHECK (kind IN ('cftc_refresh','calendar_refresh','document_extract','ai_narrative')),
  status        varchar(12) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed')),
  trigger       varchar(12) NOT NULL DEFAULT 'manual' CHECK (trigger IN ('manual','schedule','startup','upload')),
  params        jsonb       NOT NULL DEFAULT '{}'::jsonb,
  result        jsonb,
  error         text,
  requested_by  uuid REFERENCES iam.users(id) ON DELETE SET NULL,
  queued_at     timestamptz NOT NULL DEFAULT now(),
  started_at    timestamptz,
  finished_at   timestamptz
);
CREATE INDEX ix_jobs_queue ON ops.jobs(status, queued_at);
CREATE INDEX ix_jobs_kind ON ops.jobs(kind, queued_at DESC);

-- ── SND source sheets (legacy snd_data.py). The Google Sheets are no longer public, so each sheet is
--    uploaded (CSV download of the tab, or an Excel sheet) and stored as its raw cell grid; graphs and tables
--    are parsed from these grids exactly as the legacy parsed the CSV exports. year = 0 for the UOB history.
CREATE TABLE intel.snd_sources (
  role         varchar(20)  NOT NULL CHECK (role IN ('malaysia_history','malaysia','indonesia','india','exports')),
  year         integer      NOT NULL DEFAULT 0,
  filename     varchar(255) NOT NULL,
  sheet        varchar(120),
  grid         jsonb        NOT NULL,             -- list of rows, each a list of cell strings ('' when empty)
  uploaded_by  uuid REFERENCES iam.users(id) ON DELETE SET NULL,
  uploaded_at  timestamptz  NOT NULL DEFAULT now(),
  PRIMARY KEY (role, year)
);

-- ── CFTC Commitments of Traders, disaggregated futures+options combined ─────
CREATE TABLE intel.cftc_positions (
  cftc_code      varchar(12)  NOT NULL,
  report_date    date         NOT NULL,
  market_name    varchar(200) NOT NULL,
  open_interest  bigint,
  pm_long bigint, pm_short bigint,
  swap_long bigint, swap_short bigint, swap_spread bigint,
  mm_long bigint, mm_short bigint, mm_spread bigint,
  other_long bigint, other_short bigint, other_spread bigint,
  nonrept_long bigint, nonrept_short bigint,
  updated_at     timestamptz  NOT NULL DEFAULT now(),
  PRIMARY KEY (cftc_code, report_date)
);

-- ── HVO / SAF plant atlas ───────────────────────────────────────────────────
CREATE TABLE intel.atlas_plants (
  id        integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name      varchar(200) NOT NULL,
  company   varchar(200) NOT NULL DEFAULT '',
  country   varchar(80)  NOT NULL,
  region    varchar(120) NOT NULL DEFAULT '',
  lat       numeric(9,5) NOT NULL,
  lon       numeric(9,5) NOT NULL,
  status    varchar(4)   NOT NULL CHECK (status IN ('op','con','ann','can')),
  type      varchar(12)  NOT NULL CHECK (type IN ('standalone','copro')),
  kt        numeric(12,2) NOT NULL,
  safkt     numeric(12,2),
  quoted    text NOT NULL DEFAULT '',
  startup   text NOT NULL DEFAULT '',
  note      text NOT NULL DEFAULT '',
  est       boolean NOT NULL DEFAULT false
);

-- ── AI analysis reports ─────────────────────────────────────────────────────
CREATE TABLE intel.ai_reports (
  id                uuid        PRIMARY KEY DEFAULT uuidv7(),
  instrument        varchar(96) NOT NULL,
  instrument_label  varchar(200) NOT NULL,
  timeframe         varchar(4)  NOT NULL,
  sections          jsonb       NOT NULL,
  raw_text          text        NOT NULL,
  narrative         text,
  status            varchar(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','ready','failed','skipped')),
  error             text,
  model             varchar(48),
  usage             jsonb,
  requested_by      uuid REFERENCES iam.users(id) ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  finished_at       timestamptz
);
CREATE INDEX ix_ai_reports_recent ON intel.ai_reports(created_at DESC);

-- ── News: documents and what Claude extracted from them ─────────────────────
CREATE TABLE intel.documents (
  id             uuid         PRIMARY KEY DEFAULT uuidv7(),
  filename       varchar(255) NOT NULL,
  sha256         char(64)     NOT NULL UNIQUE,
  mime           varchar(120) NOT NULL,
  size_bytes     integer      NOT NULL,
  content        bytea        NOT NULL,
  doc_type       varchar(24)  CHECK (doc_type IN ('broker_commentary','oil_world','energy')),
  type_source    varchar(10)  CHECK (type_source IN ('user','filename','model','default')),
  doc_date       date         NOT NULL,
  status         varchar(12)  NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','succeeded','failed')),
  error          text,
  num_prices     integer,
  num_sentiment  integer,
  model          varchar(48),
  usage          jsonb,
  uploaded_by    uuid REFERENCES iam.users(id) ON DELETE SET NULL,
  uploaded_at    timestamptz  NOT NULL DEFAULT now(),
  processed_at   timestamptz
);
CREATE INDEX ix_documents_recent ON intel.documents(uploaded_at DESC);

CREATE TABLE intel.doc_prices (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  document_id    uuid NOT NULL REFERENCES intel.documents(id) ON DELETE CASCADE,
  document_date  date NOT NULL,
  product        varchar(24) NOT NULL,
  geography      varchar(12) NOT NULL,
  sub_geography  varchar(80),
  price_type     varchar(12) NOT NULL,
  value          numeric(20,6) NOT NULL,
  unit           varchar(40),
  change         numeric(20,6),
  tenor          varchar(40),
  broker         varchar(120),
  raw_text       text
);
CREATE INDEX ix_doc_prices_cell ON intel.doc_prices(geography, product, document_date DESC);
CREATE INDEX ix_doc_prices_doc ON intel.doc_prices(document_id);

CREATE TABLE intel.doc_sentiment (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  document_id    uuid NOT NULL REFERENCES intel.documents(id) ON DELETE CASCADE,
  document_date  date NOT NULL,
  product        varchar(24) NOT NULL,
  geography      varchar(12) NOT NULL,
  sentiment      varchar(8)  NOT NULL,
  confidence     numeric(4,3),
  theme          varchar(80),
  timeframe      varchar(16),
  summary        text,
  raw_text       text
);
CREATE INDEX ix_doc_sentiment_cell ON intel.doc_sentiment(geography, product, document_date DESC);
CREATE INDEX ix_doc_sentiment_doc ON intel.doc_sentiment(document_id);

-- ── Event calendar (replaced as a whole by each successful refresh) ─────────
CREATE TABLE intel.calendar_events (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_date  date         NOT NULL,
  event       varchar(200) NOT NULL,
  agency      varchar(120) NOT NULL DEFAULT '',
  region      varchar(12)  NOT NULL DEFAULT 'Global',
  category    varchar(12)  NOT NULL,
  source_url  text         NOT NULL DEFAULT '',
  notes       text         NOT NULL DEFAULT '',
  fetched_at  timestamptz  NOT NULL DEFAULT now(),
  job_id      uuid
);
CREATE INDEX ix_calendar_date ON intel.calendar_events(event_date);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aastar_app') THEN
    GRANT USAGE ON SCHEMA intel TO aastar_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA intel TO aastar_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA intel TO aastar_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ops.jobs TO aastar_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aastar_ro') THEN
    GRANT USAGE ON SCHEMA intel TO aastar_ro;
    GRANT SELECT ON intel.snd_sources, intel.cftc_positions, intel.atlas_plants,
                    intel.calendar_events TO aastar_ro;
    GRANT SELECT ON ops.jobs TO aastar_ro;
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS intel CASCADE; DROP TABLE IF EXISTS ops.jobs;")
