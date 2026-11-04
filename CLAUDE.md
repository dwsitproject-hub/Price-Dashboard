# AASTAR Price Dashboard — project guide

Edible-oils / vegoil / biofuels market dashboard: LSEG DataScope prices → PostgreSQL 18 → ~170 derived
series (spreads, basis, crush, Oilshare, OI) → React SPA with seasonal-overlay charts. Production target:
Alibaba Cloud, 3 servers (frontend · backend · ApsaraDB RDS PG18), DWS Hub SSO + SDK + Sentinel.

## Where things are

| Path | What |
|---|---|
| `docs/01-LEGACY-REVIEW.md` | Critique of the legacy Dash app and its old guide |
| `docs/02-PRD.md` | Requirements (IDs AUTH-, USR-, RBAC-, DASH-, DATA-, SSO-, SDK-, SEN-, SEC-…) |
| `docs/03-TECHNICAL-DESIGN.md` | Architecture, API, ERD + table dictionary, security, deployment |
| `docs/04-REBUILD-PLAN.md` | Phases P0–P11, R1/R2/R3 scope |
| `docs/05-DEPLOYMENT-RUNBOOK.md` | Local run (ports 5544/8710/5181), cloud deploy, operations |
| `docs/legacy/CLAUDE-legacy.md` | Old guide — domain formulas and history of the legacy app |
| `backend/` | FastAPI API + pipeline worker (Python 3.12) |
| `frontend/` | React 18 + TypeScript + Vite SPA |
| `deploy/` | Nginx, systemd units, RDS role bootstrap, local docker-compose |
| `legacy/` | Untouched legacy Dash app — reference until R2 parity; golden tests import it |

## Rules

- **Formulas are ported, not reinvented.** `backend/app/catalog/` (instruments, series registry) and
  `backend/app/engine/` must stay numerically identical to `legacy/scripts/process.py` / `oi_cumulative.py`;
  `backend/tests/test_engine_golden.py` enforces it. Series keys keep their legacy names.
- **New series:** add a `SeriesDef` in `catalog/registry.py` (module, group, title, unit, definition op),
  handle the op in `engine/spreads.py`, run `python -m app.cli bootstrap` then a `recompute` run.
- **Keep the seasonal idiom:** x = day-of-year mapped onto 2000-MM-DD, one trace per year, current year gold.
- **Security is server-side.** Every market route checks the module of the series key; admin routes need the
  `admin` permission; state-changing calls need the CSRF header. Never log secrets; audit every admin action.
- **Schema changes = new Alembic migration** (SQL-first, PG18). The app role `aastar_app` cannot UPDATE/DELETE
  `ops.audit_logs`.
- **No external CDNs** in the frontend (CSP allows only self + the DWS Hub origin).
- LSEG field names: plain `Last`, `Settlement Price`, … — never `Open Price` / `Close` (rejected by the API).

## Commands

```bash
cd backend && .venv/Scripts/python -m pytest -q                 # tests (uses database aastar_test)
cd backend && .venv/Scripts/python -m app.cli run --kind recompute
cd frontend && npm run build                                     # tsc + vite build
```
