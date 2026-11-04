<p align="center"><img src="docs/brand/aastar-logo.svg" alt="AASTAR Price Intelligence" width="320"></p>

# AASTAR Price Dashboard

Market intelligence for the edible-oils and biofuels complex. It pulls daily LSEG DataScope prices, converts
them to USD/MT, computes about 170 spreads, basis, crush and Oilshare series and the OI tables, and shows them
as **seasonal overlays** (one line per year).

- **Frontend:** React 18 + TypeScript + Vite (`frontend/`)
- **Backend:** FastAPI + pipeline worker, Python 3.12 (`backend/`)
- **Database:** PostgreSQL 18 (ApsaraDB RDS in the cloud)
- **Identity:** local accounts and DWS Downstream Hub SSO (OIDC + PKCE), RBAC by module, audit log
- **Support:** DWS Hub SDK (analytics, errors, bug reports) and Sentinel (AI root-cause analysis)

| Doc | |
|---|---|
| [Legacy review](docs/01-LEGACY-REVIEW.md) | What the old app does and why it was rebuilt |
| [PRD](docs/02-PRD.md) | Requirements, including production, security and Hub integration |
| [Technical design](docs/03-TECHNICAL-DESIGN.md) | Architecture, API, ERD, security, Alibaba Cloud |
| [Rebuild plan](docs/04-REBUILD-PLAN.md) | Phases and releases |
| [Runbook](docs/05-DEPLOYMENT-RUNBOOK.md) | Run locally, deploy, operate |

Quick start (local): see [runbook §1](docs/05-DEPLOYMENT-RUNBOOK.md#1-local-development-this-laptop). The UI is at
http://localhost:5181.
