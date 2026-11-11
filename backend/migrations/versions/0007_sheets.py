"""Google Sheet sources: Argus (Waste/Bio), KPN Market Price (Tradable Prem), China and India parity.

- intel.sheet_sources   : one row per workbook — last pull status, method, the data_version it published
- intel.sheet_uploads   : admin-uploaded workbook exports (fallback when the service account is not set up)
- intel.sheet_snapshots : small non-time-series payloads (Ind/China parity snapshot tables)
- ops.integration_secrets : encrypted credentials (the Google service-account key)
- ops.jobs.kind gains 'sheet_refresh'

Derived time series go into market.series / market.series_points like the LSEG series.

Revision ID: 0007_sheets
"""
from alembic import op

revision = "0007_sheets"
down_revision = "0006_intel"
branch_labels = None
depends_on = None

DDL = r"""
ALTER TABLE ops.jobs DROP CONSTRAINT IF EXISTS jobs_kind_check;
ALTER TABLE ops.jobs ADD CONSTRAINT jobs_kind_check
  CHECK (kind IN ('cftc_refresh','calendar_refresh','document_extract','ai_narrative','sheet_refresh'));

CREATE TABLE intel.sheet_sources (
  code          varchar(32)  PRIMARY KEY,
  status        varchar(12)  NOT NULL DEFAULT 'never' CHECK (status IN ('never','ok','failed','skipped')),
  method        varchar(12)  CHECK (method IN ('csv','api','upload')),
  pulled_at     timestamptz,
  error         text,
  detail        jsonb        NOT NULL DEFAULT '{}'::jsonb,
  data_version  bigint,
  updated_at    timestamptz  NOT NULL DEFAULT now()
);

CREATE TABLE intel.sheet_uploads (
  code          varchar(32)  PRIMARY KEY,
  filename      varchar(255) NOT NULL,
  grids         jsonb        NOT NULL,           -- {tab name: [[cell, ...], ...]}
  uploaded_by   uuid REFERENCES iam.users(id) ON DELETE SET NULL,
  uploaded_at   timestamptz  NOT NULL DEFAULT now()
);

CREATE TABLE intel.sheet_snapshots (
  key           varchar(48)  PRIMARY KEY,
  source_code   varchar(32)  NOT NULL,
  payload       jsonb        NOT NULL,
  updated_at    timestamptz  NOT NULL DEFAULT now()
);

CREATE TABLE ops.integration_secrets (
  provider      varchar(32)  PRIMARY KEY,
  secret_enc    text         NOT NULL,
  label         varchar(255),                    -- non-secret identifier shown to admins (service-account e-mail)
  updated_by    uuid REFERENCES iam.users(id) ON DELETE SET NULL,
  updated_at    timestamptz  NOT NULL DEFAULT now()
);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aastar_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON intel.sheet_sources, intel.sheet_uploads, intel.sheet_snapshots,
          ops.integration_secrets TO aastar_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aastar_ro') THEN
    GRANT SELECT ON intel.sheet_sources, intel.sheet_snapshots TO aastar_ro;
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS ops.integration_secrets;
    DROP TABLE IF EXISTS intel.sheet_snapshots;
    DROP TABLE IF EXISTS intel.sheet_uploads;
    DROP TABLE IF EXISTS intel.sheet_sources;
    DELETE FROM ops.jobs WHERE kind = 'sheet_refresh';
    ALTER TABLE ops.jobs DROP CONSTRAINT IF EXISTS jobs_kind_check;
    ALTER TABLE ops.jobs ADD CONSTRAINT jobs_kind_check
      CHECK (kind IN ('cftc_refresh','calendar_refresh','document_extract','ai_narrative'));
    """)
