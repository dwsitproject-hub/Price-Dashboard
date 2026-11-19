# Existing checks — 2026-11-18

| Check | Command | Result |
|---|---|---|
| Backend tests | `cd backend && .venv/Scripts/python -m pytest -q` | **BLOCKED** — 71 collected, 0 passed, 0 failed, 71 errors at setup: `psycopg.OperationalError: connection to server at "127.0.0.1", port 5544 failed` (dev database `aastar_test` is down because Docker is stopped; not started per instructions). |
| Frontend typecheck + build | `cd frontend && npm run build` | **PASS** — `tsc --noEmit` clean; `vite build` 349 modules, built in 4.2 s (largest chunk plotly-basic 1.12 MB / 387 kB gzip). |
| Frontend lint | — | **N/A** — `frontend/package.json` has no `lint` script and no ESLint config. |
| Functional API suite | `node qa/functional/api-suite.mjs` | 206 cases: 191 PASS, 13 FAIL, 2 BLOCKED (31 s); identical outcome on two consecutive runs (idempotent). |
| Functional UI run | browser pane tab-1 | 43 cases: 36 PASS, 6 FAIL, 1 BLOCKED (`ui-results.json`). |
| Session expiry (DB, read-only) | `backend/.venv/Scripts/python qa/functional/tools/dbq.py "select idle_expires_at - created_at, expires_at - created_at from iam.sessions order by created_at desc limit 1"` | 0:30:00 idle, 12:00:00 absolute — matches AUTH-11 (supports FT-AUTH-19/20). |
| State restored after the suite (DB, read-only) | `dbq.py` on `intel.sheet_uploads`, `ops.integration_secrets`, `intel.snd_sources`, `ops.app_settings`, role modules, `intel.atlas_plants` | No uploads, no service-account secret, no SND sources, settings 30 / 12, viewer role 6 modules, 94 atlas plants (0 test plants). Left behind by design: queued `cftc_refresh` (1), `sheet_refresh` (3) and one queued `recompute` pipeline run (no worker); disabled/revoked `qa-ft-*` users. |

Note: the working tree changed while the run was in progress (uncommitted edits in 30 files incl. new
`tests/test_csv_safety.py` and `tests/test_hardening.py`); the QA API was not restarted, so the functional results
reflect the code that was running.
