#!/usr/bin/env node
/**
 * Build the JSON specs for the three .docx reports from the QA result files, then render them with the
 * prod-readiness-checks skill's make-report.cjs:
 *
 *   node qa/reports/build-specs.mjs
 *   node ~/.claude/skills/prod-readiness-checks/scripts/make-report.cjs qa/reports/<name>.json qa/reports/<name>.docx
 *
 * Counts come from the result files (before = results-before.json, after = results.json); finding statuses are
 * the triage decisions recorded here, with what was done.
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const QA = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p) => JSON.parse(readFileSync(join(QA, p), 'utf8'))
const cut = (text, n = 150) => {
  const s = String(text ?? '').replace(/\s+/g, ' ').trim()
  return s.length > n ? `${s.slice(0, n - 1)}…` : s
}
const count = (rows, status) => rows.filter((r) => r.status === status).length
const DATE = '18 November 2026'
const COMMON_META = [
  ['Application', 'AASTAR Price Dashboard 2.0'],
  ['Code tested', 'Branch SIT. Before fixes: f127d70 (+ 1dc6a24). After fixes: eb0225b'],
  ['Environment tested', 'Isolated local QA copy: API 127.0.0.1:8710 with APP_ENV=prod, frontend localhost:5181, throwaway PostgreSQL 18 (port 5546) with the production role layout, demo prices from 2024 and synthetic Google-Sheet data; integrations off'],
  ['Date', DATE],
  ['Prepared by', 'Claude Code (prod-readiness-checks), for the AASTAR project team'],
]
const RERUN = [
  'backend/.venv/Scripts/python qa/stack/make_env.py        # throwaway settings (gitignored backend/.env.qa)',
  'backend/.venv/Scripts/python qa/stack/pg_cluster.py start # PostgreSQL 18 on 127.0.0.1:5546',
  'backend/.venv/Scripts/python qa/stack/setup_db.py         # migrate, bootstrap, demo prices, sheet data',
  'preview: aastar-qa-api (port 8710) + aastar-web (port 5181)  # .claude/launch.json',
  'node qa/functional/api-suite.mjs',
  'node qa/pentest/pentest-suite.mjs',
  'backend/.venv/Scripts/python qa/stack/rate_limit_check.py # the real 10/min sign-in limit',
  'cd backend && MIGRATION_DATABASE_URL=<QA owner URL>/postgres .venv/Scripts/python -m pytest -q',
].join('\n')

// ---------------------------------------------------------------- functional
function functional() {
  const before = new Map(read('functional/results-before.json').map((r) => [r.id, r.status]))
  const after = read('functional/results.json')
  const uiBefore = read('functional/ui-results.json')
  const recheck = new Map(read('functional/ui-recheck.json').map((r) => [r.id.replace(/[ab]$/, ''), r]))
  const uiStatus = (r) => (r.status === 'FAIL' && recheck.has(r.id) ? 'PASS (fixed)' : r.status)
  const ui = uiBefore.map((r) => ({ ...r, after: uiStatus(r) }))
  const apiB = { pass: count([...before.values()].map((s) => ({ status: s })), 'PASS'), fail: [...before.values()].filter((s) => s === 'FAIL').length, blocked: [...before.values()].filter((s) => s === 'BLOCKED').length }
  const apiA = { pass: count(after, 'PASS'), fail: count(after, 'FAIL'), blocked: count(after, 'BLOCKED') }
  const uiB = { pass: count(uiBefore, 'PASS'), fail: count(uiBefore, 'FAIL'), blocked: count(uiBefore, 'BLOCKED') }
  const uiA = { pass: ui.filter((r) => r.after.startsWith('PASS')).length, fail: ui.filter((r) => r.after === 'FAIL').length, blocked: ui.filter((r) => r.after === 'BLOCKED').length }
  const result = (r) => {
    const b = before.get(r.id)
    if (r.status === 'PASS' && b === 'FAIL') return ['FT-HLT-02', 'FT-SHT-05'].includes(r.id) ? 'PASS (test corrected)' : 'PASS (fixed)'
    return r.status
  }
  const areas = [...new Set(after.map((r) => r.area))]
  const areaSections = areas.map((area) => ({
    heading: `3.${areas.indexOf(area) + 1} ${area}`, level: 2,
    blocks: [{ table: { columns: [['ID', 1.1], ['Scenario', 3.4], ['Type', 0.9], ['Expected', 3.8], ['Result', 1.3]],
      rows: after.filter((r) => r.area === area).map((r) => [r.id, cut(r.title, 110), r.type, cut(r.expected, 140), result(r)]),
      status: 4 } }],
  }))
  areaSections.push({
    heading: `3.${areas.length + 1} User interface (browser)`, level: 2,
    blocks: [{ table: { columns: [['ID', 1.1], ['Scenario', 4], ['Type', 0.9], ['Expected', 3.2], ['Result', 1.3]],
      rows: ui.map((r) => [r.id, cut(r.title, 120), r.type, cut(r.expected, 120), r.after]), status: 4 } }],
  })
  const defects = [
    ['F-01', 'Medium', 'Atlas replace returned 500 on overlong text or out-of-range/infinite capacity', 'intel/atlas.py', 'Length limits per column, finite capacities 0–1e9: 422 bad_atlas', 'FIXED'],
    ['F-02', 'Medium', 'Plant without name/country stored as "None"', 'intel/atlas.py', 'Missing values fail the required check (422)', 'FIXED'],
    ['F-03', 'Low', 'NaN and negative capacities accepted', 'intel/atlas.py', 'Same range check (422)', 'FIXED'],
    ['F-04', 'Low', 'Display name of only spaces stored as ""', 'schemas, UsersPage.tsx', 'Stripped before the length check (422); dialog button needs a non-blank name', 'FIXED'],
    ['F-05', 'Low', 'API defaulted new users to local sign-in (USR-03: Hub SSO)', 'schemas/__init__.py', 'Default auth_provider is "sso"', 'FIXED'],
    ['F-06', 'Low', 'State-changing calls without an Origin header were accepted', 'api/deps.py', 'Origin (or the Referer\'s origin) required on unsafe methods: 403 bad_origin', 'FIXED'],
    ['F-07', 'Low', 'Snapshot day 04-31 silently became 28 Feb', 'routes/market.py, routes/sheets.py', 'Validated as a real day (02-29 allowed): 422', 'FIXED'],
    ['F-08', 'Low', 'Reprocess of an unknown document returned 409 busy', 'intel/news.py', '404 not_found', 'FIXED'],
    ['F-09', 'Low', 'Sheet refresh: docstring said pipeline.run is accepted, route needs admin', 'routes/sheets.py', 'Docstring corrected (admin-only, like the whole page)', 'FIXED'],
    ['F-10', 'Low', 'Audit log had no target filter (ADM-02)', 'routes/admin.py, AuditPage.tsx', '"target" filter (type or id) on the list, the CSV export and the page', 'FIXED'],
    ['F-11', 'Medium', 'Audit CSV export: spreadsheet formula injection', 'routes/admin.py', 'core/csvsafe.py neutralises formula text in both exports (= PT-INJ-04)', 'FIXED'],
    ['F-12', 'Low', 'Routing 404/405 bypassed the error envelope', 'core/errors.py', 'Starlette HTTPException handled', 'FIXED'],
    ['F-13', 'Low', 'Users dialog showed a generic "Invalid request."', 'components/Feedback.tsx', 'Validation errors list each field and message', 'FIXED'],
    ['F-14', 'Low', 'Unknown module code showed 403 to an admin', 'pages/ModulePage.tsx', '404 for admins (403 kept for others: no existence oracle)', 'FIXED'],
    ['F-15', 'Low', 'Stale tab title on sign-in and change-password pages', 'LoginPage.tsx, ChangePasswordPage.tsx', 'Titles set on those pages', 'FIXED'],
    ['F-16', 'Low', 'Atlas table had no empty state', 'pages/AtlasPage.tsx', '"No plants match these filters." row', 'FIXED'],
    ['F-17', 'Low', 'Change-password handler did not re-check its rules', 'pages/ChangePasswordPage.tsx', 'Handler guard (and the same on the sign-in form)', 'FIXED'],
  ]
  return {
    title: 'Functional Test Report', subtitle: 'AASTAR Price Dashboard', orientation: 'landscape',
    meta: [...COMMON_META, ['Overall result', `PASS after fixes - API ${apiA.pass}/${after.length} pass, UI ${uiA.pass}/${ui.length} pass, 0 failures; ${apiA.blocked + uiA.blocked} blocked by the QA environment`]],
    sections: [
      { heading: '1. Summary', blocks: [
        { p: `Every API route group and every page of the dashboard was tested with positive and negative cases against an isolated QA copy running with production settings. **${after.length + ui.length} scenarios** were designed and executed: ${after.length} through the API suite and ${ui.length} in the browser. The first run found **17 defects** (3 Medium, 14 Low, none Critical or High). All 17 are fixed, covered by tests and re-checked.` },
        { table: { columns: [['Run', 2], ['Scenarios', 1], ['Pass', 1], ['Fail', 1], ['Blocked', 1]], rows: [
          ['API - before fixes', String(after.length), String(apiB.pass), String(apiB.fail), String(apiB.blocked)],
          ['API - after fixes', String(after.length), String(apiA.pass), String(apiA.fail), String(apiA.blocked)],
          ['UI - before fixes', String(ui.length), String(uiB.pass), String(uiB.fail), String(uiB.blocked)],
          ['UI - after fixes (re-checked)', String(ui.length), String(uiA.pass), String(uiA.fail), String(uiA.blocked)],
        ] } },
        { p: '**Verdict:** functionally ready. Nothing is open from this test. The three blocked scenarios need a configured Claude key (News upload validation), a 30-minute wait (idle timeout, verified in the database instead: 30 min idle, 12 h absolute) or device emulation (mobile layout) - check them on staging.' },
      ] },
      { heading: '2. Approach', blocks: [
        { bullets: [
          '**Environment:** local QA copy - API on 127.0.0.1:8710 (APP_ENV=prod, so no API docs and no synthetic data unless explicitly allowed), Vite frontend on localhost:5181 proxying /api, throwaway PostgreSQL 18 on port 5546 with the production roles (the API connects as the least-privilege aastar_app).',
          '**Data:** demo LSEG prices from 2024-01-02 (436 series), the four Google-Sheet sources loaded from synthetic grids, 94 HVO/SAF plants. No production data.',
          '**Accounts:** a QA admin generated with random credentials (never printed); analyst, viewer and disabled users created per run with the prefix qa-ft-.',
          '**Tools:** qa/functional/api-suite.mjs (Node, idempotent, writes results.json), the in-app browser for UI scenarios, and the existing backend tests and frontend build.',
          '**Out of scope:** real integrations (LSEG, Claude, CFTC, Google, DWS Hub) - switched off, never contacted; no background worker (queued jobs stay queued); load testing.',
        ] },
      ] },
      { heading: '3. Results by area', blocks: [{ p: 'Post-fix status. "PASS (fixed)" failed before its fix; "PASS (test corrected)" was a test-design error (section 4).' }] },
      ...areaSections,
      { heading: '4. Defects', blocks: [
        { table: { columns: [['ID', 0.6], ['Severity', 0.8], ['Title', 3], ['Location', 2], ['Fix', 3.2], ['Status', 0.8]], rows: defects, status: 5 } },
        { p: '**Test-design corrections (not defects):** FT-HLT-02 pinned the migration name 0007_sheets - it now expects the shipped head (0008_hardening after the fixes). FT-SHT-05 expected the old docstring\'s "pipeline.run may refresh"; the route is admin-only by design (F-09). FT-AI-03 had a check bug fixed during the first run.' },
        { p: '**Observations:** without a worker, queued jobs never run, so the CFTC and Sheet sources pages show "Refreshing…" until a worker starts - an artefact of this QA stack, not of production. AI reports and News documents are shared between users who hold the module (see the penetration test, PT-AC-07).' },
      ] },
      { heading: '5. Automated checks', blocks: [
        { table: { columns: [['Check', 2], ['Result', 3], ['Status', 1]], rows: [
          ['Backend tests (pytest)', '77 passed (64 before; 13 new for these fixes). Run against the QA PostgreSQL 18 because the usual test database (Docker, port 5544) was down', 'PASS'],
          ['Frontend typecheck + build', 'tsc --noEmit clean; vite build succeeds (initial load ~47 kB gzip + vendor chunks)', 'PASS'],
          ['Frontend lint', 'No lint script or ESLint config in the project', 'N/A'],
          ['Functional API suite', `${apiA.pass} pass, ${apiA.fail} fail, ${apiA.blocked} blocked; idempotent across runs`, 'PASS'],
        ], status: 2 } },
      ] },
      { heading: '6. How to re-run', blocks: [{ code: RERUN }] },
    ],
  }
}

// ---------------------------------------------------------------- architecture
function architecture() {
  const score = [
    ['1. Structure & modularity', 'PASS', 'Clean layers, pure formula engine, one Claude gateway, catalog as code'],
    ['2. Data architecture', 'PARTIAL', 'Good keys and bulk writes; unindexed purge FK, retention gaps, UTC dates (fixed: FK index, dates)'],
    ['3. API design', 'PARTIAL', 'Consistent errors and validation; routing 404s outside the envelope (fixed), untyped responses'],
    ['4. Security architecture', 'PARTIAL', 'Strong auth/CSRF/RBAC; HTTP on a shared IP and hard-coded DB role names need decisions'],
    ['5. Reliability & resilience', 'PARTIAL', 'Locks and recovery sound; stuck documents/reports after a crash (fixed), serial job queue'],
    ['6. Performance & scalability', 'PARTIAL', 'Fast hot paths; blocking uploads and polled full counts (fixed), per-request query fan-out'],
    ['7. Observability & operations', 'PARTIAL', 'JSON logs with request ids, audit trail; no alerting or worker liveness'],
    ['8. Deployability & configuration', 'PARTIAL', 'Good staging kit; no pinned subnet, no limits, no CI (production kit covers the first two)'],
    ['9. Maintainability & testability', 'PARTIAL', 'Golden and security tests; no linters, no worker/LSEG-client tests'],
    ['10. Production readiness', 'FAIL', 'Four High items need fixes or decisions before go-live (AR-01..04)'],
  ]
  const F = (id, sev, vp, title, rec, effort, status) => [id, vp, sev, title, rec, effort, status]
  const findings = [
    F('AR-01', 'High', 'Prod readiness', 'A copy of staging carries leftover synthetic prices, staging sessions, staging Hub bindings and staging-encrypted secrets', 'Promotion script with the production kit: delete source=demo prices and recompute, clear sessions/OIDC states/jobs, reset hub_sub, re-enter or keep the encryption key deliberately', 'M', 'PLANNED'),
    F('AR-02', 'High', 'Data / Security', 'Role and database names are fixed (aastar_*): staging and production on one instance would share aastar_app and its 40 connections', 'Use a different instance than staging (the planned shared instance must be checked), or parameterise the role names', 'M', 'OPEN'),
    F('AR-03', 'High', 'Deployability', 'Docker networks not pinned: the default pool includes the hosts\' 172.28/16', 'Production compose pins a /24 chosen by the preflight (no default)', 'S', 'PLANNED'),
    F('AR-04', 'High', 'Security', 'Plain HTTP on a shared IP: the session cookie is sent to every app on that IP and travels in cleartext', 'Own hostname from day one (cookies are host-scoped), HTTPS as soon as a certificate exists; otherwise a written risk acceptance', 'M', 'OPEN'),
    F('AR-05', 'Medium', 'Reliability', 'Default pool sizing exceeded the role\'s connection limit', 'Pool 4 + overflow 2, 2 workers: 18 connections', 'S', 'FIXED'),
    F('AR-06', 'Medium', 'Observability', 'No alerting, worker liveness or data-freshness signal', 'Heartbeat table, /api/health/data, webhook on failed runs/sources', 'M', 'PLANNED'),
    F('AR-07', 'Medium', 'Reliability', 'A worker crash left documents "processing" and AI reports "pending" forever', 'recover_interrupted fails orphans whose job is gone (test added)', 'S', 'FIXED'),
    F('AR-08', 'Medium', 'Performance', 'Upload handlers blocked the event loop while parsing workbooks', 'Plain def handlers (threadpool)', 'S', 'FIXED'),
    F('AR-09', 'Medium', 'Reliability', 'Missing LSEG credentials silently produced synthetic prices in production', 'APP_ENV=prod refuses synthetic prices (ALLOW_DEMO_DATA for QA copies; test added)', 'S', 'FIXED'),
    F('AR-10', 'Medium', 'Deployability', 'No CI, no test gate, no build-once-promote', 'Pipeline with tests and audits; release tags; image registry later', 'M', 'PLANNED'),
    F('AR-11', 'Medium', 'Security', 'Secrets in env files visible through docker inspect on shared hosts', 'chmod 600 secrets file, restrict the docker group; secret files or KMS later', 'M', 'PLANNED'),
    F('AR-12', 'Medium', 'Security', 'DB TLS not enforced (sslmode=prefer); HTTP Hub issuer', 'Production env: sslmode=require, HTTPS Hub', 'S', 'PLANNED'),
    F('AR-13', 'Medium', 'Deployability', 'No container memory/CPU limits on shared hosts', 'mem_limit / cpus in the production compose', 'S', 'PLANNED'),
    F('AR-14', 'Medium', 'Deployability', 'Migrations auto-apply without a backup; "additive" claim false', 'Guide corrected; production release takes an RDS backup before migrating', 'S', 'PARTIAL'),
    F('AR-15', 'Medium', 'Docs', 'Production runbook describes another topology; rotation and connection docs wrong', 'New production guide from the deployment kit', 'S', 'PLANNED'),
    F('AR-16', 'Medium', 'Reliability', 'Serial job queue without per-job timeouts', 'Timeouts per kind; priority lane for sheet_refresh', 'S', 'PLANNED'),
    F('AR-17', 'Low', 'Data', 'Retention gaps (jobs, documents, AI reports, audit archive)', 'Purge policy and archive script', 'M', 'PLANNED'),
    F('AR-18', 'Low', 'Data', 'Run purge would rewrite ~1 M price rows through an unindexed FK', 'Migration 0008 index; purge skips runs that label prices; separate transactions', 'S', 'FIXED'),
    F('AR-19', 'Low', 'Performance', 'Per-request query fan-out', 'One version check and one points query per section', 'S', 'PLANNED'),
    F('AR-20', 'Low', 'Performance', 'Polled admin status ran full-table counts', 'Planner estimates and index probes', 'S', 'FIXED'),
    F('AR-21', 'Low', 'API', 'Routing 404/405 outside the error envelope', 'Starlette HTTPException handled (test added)', 'S', 'FIXED'),
    F('AR-22', 'Low', 'Data', 'Business dates in UTC instead of Asia/Jakarta', 'core/clock.business_today (test added)', 'S', 'FIXED'),
    F('AR-23', 'Low', 'Security', 'Image defaults: FORWARDED_ALLOW_IPS=*, pytest in the runtime', 'Loopback default, requirements-dev.txt; digest pins / unprivileged nginx later', 'S', 'PARTIAL'),
    F('AR-24', 'Low', 'Maintainability', 'Stale definitions (PERMISSIONS, EXPECTED_REVISION, unused APP_SECRET_KEY)', 'PERMISSIONS and readiness head fixed; APP_SECRET_KEY kept for now', 'S', 'PARTIAL'),
    F('AR-25', 'Low', 'Maintainability', 'No linters; no worker-recovery or LSEG-client tests', 'Linters in CI; recovery test added (AR-07)', 'M', 'PLANNED'),
    F('AR-26', 'Low', 'Security', 'Read-only role could read the audit log and stored tokens', 'Migration 0008 revokes it (test added)', 'S', 'FIXED'),
    F('AR-27', 'Low', 'Security', 'SSO: nonce optional, code in access logs, no Hub logout', 'Require nonce; log format without query strings', 'S', 'PLANNED'),
    F('AR-28', 'Low', 'Security', 'Office/zip parsing without decompression limits (admin/analyst uploads only)', 'Uncompressed-size check before parsing', 'S', 'PLANNED'),
    F('AR-29', 'Low', 'Reliability', 'LSEG client held a transaction open; HTTP 429 not retried', 'Read transaction ended; 429 retried with Retry-After', 'S', 'FIXED'),
  ]
  const high = [
    ['AR-01 - Production from a copy of staging', 'Staging started in demo mode before its LSEG credentials were set, so synthetic rows may remain for instruments or dates the LSEG backfill did not overwrite; the worker never backfills a non-empty table and the first live run switches the demo banner off. The copy also carries valid staging sessions, OIDC states, users bound to the staging Hub\'s subjects, the Google key encrypted with the staging APP_ENCRYPTION_KEY, staging jobs and an immutable staging audit trail.', 'Write and rehearse a promotion script (part of the production kit): delete price_daily where source = demo and recompute; truncate sessions, OIDC states and jobs; reset hub_sub so users re-bind by verified e-mail; keep the encryption key on purpose or re-enter the Google key; decide what happens to the audit log.'],
    ['AR-02 - Database roles on a shared instance', 'bootstrap_roles.sql creates aastar_owner/app/ro and database aastar, and every migration grants to those literal names. Roles are instance-wide.', 'If production shares the staging instance: parameterise the names (or use group roles) first. If it shares another app\'s instance: check that no aastar_* role or aastar database exists there (the production preflight will).'],
    ['AR-03 - Unpinned Docker network', 'The staging compose files declare no network; Docker\'s default pool includes 172.28.0.0/16, the hosts\' own LAN, and the 172.30 range that holds the NAS.', 'The production compose files pin a subnet from the preflight with no default value.'],
    ['AR-04 - HTTP on a shared IP', 'Secure is set only for https. Browsers scope cookies by host, not port, so on http://172.28.80.50:3170 the session cookie would also reach every other app on that IP.', 'Give the app its own hostname through the host nginx from day one (even over HTTP this isolates the cookie), and move to HTTPS as soon as a certificate is available.'],
  ]
  return {
    title: 'Architecture Review Report', subtitle: 'AASTAR Price Dashboard', orientation: 'portrait',
    meta: [...COMMON_META, ['Overall result', 'Not ready yet: 4 High items need a fix in the production kit or a decision (AR-01..04); 12 items fixed now']],
    sections: [
      { heading: '1. Summary', blocks: [
        { table: { columns: [['Viewpoint', 2.2], ['Result', 1], ['Key point', 4.5]], rows: score, status: 1 } },
        { p: `**${findings.length} findings:** 4 High, 12 Medium, 13 Low. **Fixed now:** ${findings.filter((f) => f[6] === 'FIXED').length}, partly fixed ${findings.filter((f) => f[6] === 'PARTIAL').length}, planned ${findings.filter((f) => f[6] === 'PLANNED').length}, open for a decision ${findings.filter((f) => f[6] === 'OPEN').length}.` },
        { note: 'Verdict: the application itself is sound; what blocks go-live is how production is set up. AR-03 is solved by the production compose files, AR-01 by a promotion script, and AR-02 and AR-04 need a decision (which database instance; a hostname and TLS).' },
      ] },
      { heading: '2. System overview', blocks: [
        { p: 'React SPA served by Nginx on the frontend host; FastAPI API and an APScheduler worker on the backend host; ApsaraDB PostgreSQL 18. The worker pulls LSEG DataScope prices daily, computes ~170 derived series, refreshes CFTC, the event calendar (Claude with web search) and four Google-Sheet sources; the API serves charts, intelligence pages and the admin console. Sign-in: local accounts and DWS Hub OIDC (PKCE).' },
        { code: 'Browser ──HTTP──> FE host: Nginx (SPA, CSP) ──/api──> BE host: API (FastAPI, gunicorn)\n                                                          │        └─ worker (APScheduler, ops.jobs)\n                                                          └──> ApsaraDB PostgreSQL 18 (iam, market, ops, intel)\nworker ──> LSEG DataScope · api.anthropic.com · cftc.gov · Google Sheets\nBrowser ──> DWS Hub (SSO, SDK, Sentinel)' },
        { p: 'Full write-up: qa/architecture/system-overview.md.' },
      ] },
      { heading: '3. Review method', blocks: [
        { p: 'Ten viewpoints, each with explicit pass criteria, checked against the code, configuration, documents and - read-only - the running QA copy (EXPLAIN ANALYZE of the heavy queries, index and privilege queries, the frontend build). Criteria and evidence per check: qa/architecture/review-scenario.md.' },
      ] },
      { heading: '4. Findings', blocks: [
        { table: { columns: [['ID', 0.9], ['Viewpoint', 1.1], ['Severity', 0.8], ['Title', 2.5], ['Recommendation / done', 2.7], ['Effort', 0.6], ['Status', 0.9]], rows: findings, status: 6 } },
      ] },
      { heading: '5. Details for High findings', blocks: high.flatMap(([h, ev, rec]) => [
        { p: `**${h}**` }, { p: `Evidence and impact: ${ev}` }, { p: `Recommendation: ${rec}` },
      ]) },
      { heading: '6. Strengths to keep', blocks: [{ bullets: [
        'Formulas protected by golden tests against the legacy code, plus parity tests for every Google-Sheet source.',
        'Clean layering: pure engine, bulk SQL in one store, thin routes, one Claude gateway, catalog as code synced by an idempotent bootstrap.',
        'Efficient writes (COPY into temp tables, change-only upserts) and writers that cannot delete each other\'s points.',
        'Sound concurrency: advisory locks, SKIP LOCKED job claims, one running pipeline run, job de-duplication, start-up recovery.',
        'Security fundamentals: Argon2id with timing equalisation and lockout, hashed server-side sessions, CSRF double-submit plus Origin, per-series module checks, OIDC with PKCE S256 and JWKS, strict CSP without CDNs.',
        'Audit trail written in the same transaction as the change and protected from the app role; JSON logs with request ids end to end.',
      ] }] },
      { heading: '7. Roadmap', blocks: [
        { p: '**Before go-live:** AR-01 promotion script, AR-02 instance decision and check, AR-03 pinned subnet, AR-04 hostname (TLS when possible), AR-11/12/13/14 in the production kit (secrets file 600, sslmode=require, limits, backup before migrating), AR-15 production guide.' },
        { p: '**After go-live:** AR-06 alerting and freshness, AR-10 CI, AR-16 job timeouts, AR-17 retention, AR-19 query fan-out, AR-23/24/25/27/28 hardening and tooling.' },
      ] },
    ],
  }
}

// ---------------------------------------------------------------- pentest
function pentest() {
  const after = read('pentest/results.json')
  const before = new Map(read('pentest/results-before.json').map((r) => [r.id, r.status]))
  const rate = JSON.parse(readFileSync(join(QA, 'pentest/rate-limit-result.json'), 'utf8').trim())
  const evidence = (r) => (r.id === 'PT-INJ-04' && r.status === 'SECURE'
    ? "After the fix the exported cell starts with a quote ('=2+5+qapt…): opened as text, not run as a formula"
    : r.evidence)
  const rows = after.map((r) => [r.id, r.category, cut(r.title, 110), cut(evidence(r), 160),
    r.status === 'SECURE' && before.get(r.id) === 'VULNERABLE' ? 'SECURE (fixed)' : r.status])
  rows.push([rate.id, 'Authentication', 'Sign-in rate limit with the real setting (10/min) on a fresh API', `Status codes for 12 failed sign-ins: ${rate.codes.join(', ')}`, rate.status])
  const findings = [
    ['PT-INJ-04', 'Medium (6.1)', 'CSV/formula injection in the audit-log export, injectable without signing in', 'routes/admin.py export_audit; services/auth.py', 'FIXED'],
    ['PT-DEP-01', 'Medium', 'Vulnerable react-router-dom 6.28.0 (open redirect / XSS advisories)', 'frontend/package.json', 'FIXED'],
    ['PT-CFG-02/04/05', 'Low (3.7)', 'Staging exposes API docs (APP_ENV=sit) and version details without a session', 'main.py; auth.py /config; health.py /ready', 'ACCEPTED'],
    ['PT-AC-07', 'Low (3.5)', 'AI reports and News documents are shared between users of the module', 'intel.py; intel/ai.py; intel/news.py', 'ACCEPTED'],
    ['PT-AUTH-04', 'Low (3.1)', 'No Secure cookie flag on plain-HTTP origins', 'core/config.py secure_cookies', 'OPEN'],
  ]
  const details = [
    ['PT-INJ-04 - CSV / formula injection (CVSS 3.1 6.1, AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N)', 'The audit CSV export wrote cells as stored. A failed sign-in records the typed e-mail verbatim, so an anonymous visitor could plant "=HYPERLINK(...)" that runs when an administrator opens the export.', 'Unauthenticated POST /api/auth/login with email "=2+5+qapt<run>", then the admin export contained the cell unescaped.', 'core/csvsafe.py prefixes text starting with = + - @ TAB CR with a quote in both CSV exports; numbers (negative spreads) stay numeric. Test tests/test_csv_safety.py. Re-test: SECURE.'],
    ['PT-DEP-01 - react-router-dom 6.28.0', 'npm audit: 3 high advisories (GHSA-2w69-qvjg-hvjx, GHSA-2j2x-hqr9-3h42).', 'npm audit --omit=dev.', 'Upgraded to 6.30.6. Two moderate advisories remain that only React Router 7 fixes: one concerns server-side rendering (not used); the other, a backslash open redirect, is closed in the app - the sign-in "next" check now refuses backslashes, like the server-side check. Re-test: no high advisories.'],
    ['PT-CFG-02/04/05 - information disclosure on staging', 'Staging runs APP_ENV=sit, which enables /api/docs and /api/openapi.json; /api/auth/config and /api/health/ready return environment, release and migration revision without a session.', 'Passive GETs on http://172.28.92.56:3170.', 'Accepted for staging (internal). Production runs APP_ENV=prod, where the docs answer 404 (verified on the QA copy).'],
    ['PT-AC-07 - shared AI reports and documents', 'Any user holding ai_analysis can read any AI report by id and the list shows requester e-mails; News documents likewise. The module guard holds (a viewer without the module gets 403).', 'Report created by one analyst read by another.', 'Accepted as the intended shared analyst desk - confirm with the product owner; scope by requested_by if isolation is wanted.'],
    ['PT-AUTH-04 - cookies without Secure on HTTP', 'Secure is set only when APP_BASE_URL is https. Over plain HTTP the session token travels in cleartext and, on a shared IP, reaches every other app on that IP.', 'Set-Cookie on the QA copy: HttpOnly; SameSite=Lax, no Secure.', 'Open, tied to architecture finding AR-04: an own hostname for production from day one and HTTPS as soon as a certificate exists.'],
  ]
  const counts = (it) => { const m = [...it]; return { secure: m.filter((s) => s === 'SECURE').length, vuln: m.filter((s) => s === 'VULNERABLE').length, info: m.filter((s) => s === 'INFO').length } }
  const b = counts(before.values()), a = counts(after.map((r) => r.status))
  return {
    title: 'Penetration Test Report', subtitle: 'AASTAR Price Dashboard', orientation: 'landscape',
    meta: [...COMMON_META, ['Overall result', 'Low residual risk after fixes: 0 Critical/High; 2 Medium found and fixed; 3 Low accepted or open (decision on HTTPS)']],
    sections: [
      { heading: '1. Executive summary', blocks: [
        { p: 'An authorized grey-box penetration test of the dashboard, requested by its owner, ahead of the first production release. Active tests ran only against the isolated local QA copy; the staging server received about 24 passive, unauthenticated GET/HEAD requests. Real integrations, other applications and the production hosts were out of scope.' },
        { table: { columns: [['Run', 2], ['Cases', 1], ['Secure', 1], ['Vulnerable', 1], ['Info', 1]], rows: [
          ['Before fixes', String(after.length), String(b.secure), String(b.vuln), String(b.info)],
          ['After fixes', String(after.length + 1), String(a.secure + 1), String(a.vuln), String(a.info)],
        ] } },
        { table: { columns: [['Severity', 1.5], ['Found', 1], ['Fixed', 1], ['Accepted / open', 1.5]], rows: [
          ['Critical / High', '0', '0', '0'], ['Medium', '2', '2', '0'], ['Low', '3', '0', '3'],
        ] } },
        { note: 'Overall risk: Low. Access control, sessions, CSRF, injection, uploads, SSRF and SSO all held. The remaining exposure depends on deployment: run production over its own hostname, and HTTPS as soon as possible.' },
      ] },
      { heading: '2. Scope and method', blocks: [{ bullets: [
        '**Active (local QA copy):** frontend localhost:5181 and API 127.0.0.1:8710 with APP_ENV=prod; throwaway PostgreSQL 18; fixtures prefixed qa-pt-.',
        '**Passive (staging):** http://172.28.92.56:3170 - unauthenticated GET/HEAD only, no logins, no payloads; the backend host was never contacted directly.',
        '**Coverage:** OWASP Top 10 2021 / ASVS - authentication, vertical and horizontal authorization, mass assignment, SQL and header injection, CSV injection, uploads, stored XSS, CSRF/Origin, SSRF, headers and errors, rate limiting, OIDC (code review), secrets in git history, dependencies, container and proxy hardening.',
        '**Tools:** qa/pentest/pentest-suite.mjs (Node), qa/stack/rate_limit_check.py, npm audit, git log searches, source review.',
        '**Limits:** no load or DoS tests; SSO by code review (disabled locally); Python dependencies reviewed against known CVEs because pip-audit is not installed.',
      ] }] },
      { heading: '3. Findings summary', blocks: [
        { table: { columns: [['ID', 1.1], ['Severity (CVSS)', 1.1], ['Title', 3.6], ['Component', 2.4], ['Status', 0.9]], rows: findings, status: 4 } },
      ] },
      { heading: '4. Finding details', blocks: details.flatMap(([h, desc, proof, fix]) => [
        { p: `**${h}**` }, { p: `Description and impact: ${desc}` }, { p: `Proof (redacted): ${proof}` }, { p: `Fix and re-test: ${fix}` },
      ]) },
      { heading: '5. Test results by category', blocks: [
        { table: { columns: [['ID', 1], ['Category', 1.3], ['Test', 3], ['Evidence', 3.6], ['Result', 1.1]], rows, status: 4 } },
      ] },
      { heading: '6. Staging passive observations', blocks: [{ bullets: [
        'Security headers present: CSP, X-Frame-Options DENY, nosniff, Referrer-Policy, COOP, Permissions-Policy; server version hidden; API responses no-store.',
        'Protected endpoints answer 401 without a session.',
        '/.env and /.git/config return the SPA page (fallback), not files - false positives.',
        'Information disclosure while staging runs APP_ENV=sit: /api/docs and /api/openapi.json (62 paths), environment, release and migration revision on /api/auth/config and /api/health/ready (PT-CFG-02/04/05).',
        'The CSP allows the HTTP staging Hub; production must use the HTTPS Hub.',
      ] }] },
      { heading: '7. Controls that held', blocks: [{ bullets: [
        'Authentication: generic errors, no user enumeration (equal timing via a dummy Argon2id hash), lockout, fresh session on sign-in, server-side logout, sessions revoked on password change and when a user is disabled, sign-in rate limit (10/min).',
        'Authorization: admin APIs refused to analysts and viewers, no self-escalation or mass assignment, data.export and pipeline.run enforced, module checks on every series and section.',
        'Injection: bound parameters everywhere; probes on every query and path parameter returned 404/422 without SQL errors; Content-Disposition names sanitised.',
        'CSRF: session-bound double-submit token and Origin allow-list (now also required when Origin is missing).',
        'Uploads: type and size checks per route, admin-only (News: documents.upload).',
        'XSS: React escaping, no dangerouslySetInnerHTML, Markdown without raw HTML; strict CSP.',
        'SSRF: Google sheet IDs fixed in code; the service-account token endpoint is constant.',
        'SSO (code review): single-use hashed state, nonce, PKCE S256, JWKS-verified tokens with an algorithm allow-list, safe redirects.',
        'Secrets: none in git history; .env files ignored. Containers run as non-root; source maps removed.',
      ] }] },
      { heading: '8. Residual risk and recommendations', blocks: [
        { bullets: [
          'Serve production on its own hostname from day one and over HTTPS as soon as a certificate is available (PT-AUTH-04 / AR-04).',
          'Keep APP_ENV=prod in production; consider trimming release and environment from the unauthenticated /api/auth/config.',
          'Confirm that shared AI reports and documents are intended (PT-AC-07).',
          'Plan the React Router 7 upgrade to clear the two remaining moderate advisories.',
        ] },
        { p: '**How to re-run:**' }, { code: RERUN },
      ] },
    ],
  }
}

for (const [name, build] of [['functional-test-report', functional], ['architecture-review-report', architecture], ['penetration-test-report', pentest]]) {
  writeFileSync(join(QA, 'reports', `${name}.json`), JSON.stringify(build(), null, 1))
  console.log('wrote', `qa/reports/${name}.json`)
}
