# AASTAR Price Dashboard — functional test findings

Run date 2026-11-18 on the QA stack: API at 127.0.0.1:8710 behind the Vite proxy at 127.0.0.1:5181, APP_ENV=prod, demo data, no worker, no integrations. Evidence is in `qa/functional/results.json` (API, 206 cases) and `qa/functional/ui-results.json` (UI, 43 cases). Re-run with `node qa/functional/api-suite.mjs`.

**Concurrent changes.** The working tree changed during the run: 30 uncommitted files, including `admin.py`, `errors.py`, `intel.py`, `market.py`, `sheets.py`, a new `app/core/csvsafe.py`, and new tests `test_csv_safety.py` and `test_hardening.py`. The QA API process was not restarted, so the results describe the code that was running. F-11 and F-12 already have an uncommitted fix; re-run after the API restarts. Line numbers refer to the current working tree.

| ID | Sev | Area | Title | Tests |
|---|---|---|---|---|
| F-01 | Medium | atlas | Atlas replace returns 500 on overlong text or out-of-range/infinite capacity | FT-ATL-05, FT-ATL-06 |
| F-02 | Medium | atlas | Plant without name/country accepted and stored as "None" | FT-ATL-04 |
| F-03 | Low | atlas | `kt` "NaN" and negative capacities accepted; NaN plant served first with `kt: null` | FT-ATL-07 |
| F-04 | Low | users | Whitespace-only display name stored as "" (API and dialog) | FT-USR-09, UI-USR-02 |
| F-05 | Low | users | API default auth_provider is `local`; USR-03 says Hub SSO | FT-USR-05 |
| F-06 | Low | auth | Unsafe requests without Origin are accepted (AUTH-14) | FT-AUTH-17 |
| F-07 | Low | market | Snapshot `04-31` silently becomes Feb 28 | FT-MKT-22 |
| F-08 | Low | news | Reprocess of a nonexistent document returns 409 busy instead of 404 | FT-NEWS-07 |
| F-09 | Low | sheets-admin | Sheet refresh: docstring says `pipeline.run` is accepted, route requires `admin` | FT-SHT-05 |
| F-10 | Low | admin | Audit log has no target filter (ADM-02) | FT-ADM-05 |
| F-11 | Medium | admin | Audit CSV export: formula injection (fix pending in working tree) | FT-ADM-07 |
| F-12 | Low | health | Routing 404 bypasses the error format (fix pending) | FT-HLT-03 |
| F-13 | Low | ui-admin | Users dialog shows generic "Invalid request." | UI-USR-03 |
| F-14 | Low | ui-access | Unknown module code shows 403 instead of 404 | UI-NAV-05 |
| F-15 | Low | ui-pages | Stale `document.title` on /login and /change-password | UI-NAV-12 |
| F-16 | Low | ui-intel | Atlas table has no empty state | UI-ATL-02 |
| F-17 | Low | ui-auth | Change-password submit handler doesn't re-check `valid` | UI-PWD-04 |

## F-01 · Medium · Atlas replace returns 500 on overlong text or out-of-range capacity
- **Steps (admin):** `PUT /api/intel/atlas` with the current plants plus `{"name": "N"×201, "company":"QA", "country":"Nowhere", "lat":1.5, "lon":103.8, "status":"op", "type":"standalone", "kt":100}`.
- **Result:** `500 {"error":{"code":"internal_error","message":"Something went wrong."}}`. Same with `"kt": 1e12` (numeric(12,2) overflow) and `"safkt": "Infinity"`. The transaction rolls back, so data is unchanged.
- **Expected:** 422 `bad_atlas`. **Actual:** 500.
- **Location:** `backend/app/intel/atlas.py:23-43`. `_validate` has no length or range checks, and `float()` accepts inf/nan. Column sizes are in `migrations/versions/0006_intel.py:65-81`.
- **Fix:** enforce the column lengths (name 200, company 200, country 80, region 120). Require `math.isfinite()` and `0 <= kt < 1e10`, and the same for `safkt`. Raise 422 for violations. Optionally map `sqlalchemy.exc.DataError` to 422.

## F-02 · Medium · Plant without name/country stored as "None"
- **Steps:** `PUT /api/intel/atlas` with the current plants plus one plant that has no `name`.
- **Result:** `200 {"plants":95}`, and GET lists a plant named "None". The suite restored the data.
- **Expected:** 422.
- **Location:** `atlas.py:30`. `str(row["name"]).strip()` turns None into "None", so the emptiness check at line 38 never fires. Country has the same problem.
- **Fix:** `(p.get("name") or "").strip()`, then require it to be non-empty.

## F-03 · Low · NaN / negative capacity accepted
- **Steps:** a plant with `"kt":"NaN"`.
- **Result:** PUT returns 200. GET returns that plant first, because PostgreSQL sorts NaN highest in `ORDER BY kt DESC`, with `"kt": null`; the table shows 0. A plant with `"kt": -5` also returns 200.
- **Expected:** 422.
- **Location:** `atlas.py:31-32`.
- **Fix:** as in F-01.

## F-04 · Low · Whitespace-only display name
- **Steps:** `POST /api/admin/users {"email":"qa-ft-blank-…@example.com", "display_name":"   ", "role":"viewer", "auth_provider":"sso"}`.
- **Result:** 201 with `display_name ""`. PATCH with "   " also stores "". In the dialog, Register is enabled for a name of spaces.
- **Location:** `schemas/__init__.py:28,37` checks `min_length` before stripping; `services/users.py:90,128-129`; `frontend/src/pages/admin/UsersPage.tsx:219` uses `!name`.
- **Fix:** strip whitespace in the schema validator before the length check, and use `name.trim()` in the dialog.

## F-05 · Low · API default sign-in method
- **Steps:** `POST /api/admin/users` without `auth_provider`.
- **Result:** 201 with `auth_provider "local"` and a temporary password in the response.
- **Expected:** PRD USR-03 and commit d02f327 say new users default to Hub SSO; the UI dialog already does.
- **Location:** `schemas/__init__.py:30`.
- **Fix:** default to `"sso"`, or make the field required.

## F-06 · Low · Missing Origin accepted
- **Steps (admin):** valid session and `X-CSRF-Token`, no `Origin` header: `PUT /api/admin/settings {"values":{}}`.
- **Result:** 200 `{"changed":[]}`. A foreign Origin correctly gets 403. The session-bound CSRF token is still required, which limits the impact.
- **Location:** `api/deps.py:35-38` (`if origin and …`).
- **Fix:** reject a missing Origin on unsafe methods; optionally fall back to the Referer origin.

## F-07 · Low · Impossible snapshot day
- **Steps:** `GET /api/market/forward-curves?snapshot=04-31&years=2024`.
- **Result:** 200, with traces as of 2024-02-28. The Waste/Bio curves route has the same issue.
- **Location:** the regexes at `routes/market.py:22` and `routes/sheets.py:26` accept day 31 for any month; the fallback is at `services/market.py:215-219`.
- **Fix:** validate the date with `date(2000, m, d)` and return 422, or clamp to the last day of that month instead of Feb 28.

## F-08 · Low · Reprocess of a nonexistent document
- **Steps (analyst):** `POST /api/intel/news/documents/<random uuid>/reprocess`.
- **Result:** 409 `busy`.
- **Expected:** 404.
- **Location:** `intel/news.py:249-255`. An update that matches zero rows is always reported as busy.
- **Fix:** check that the document exists first (404), and return 409 only when it really is busy.

## F-09 · Low · Sheet refresh permission
- **Steps (analyst, has `pipeline.run`):** `POST /api/admin/sheets/refresh {"sources":["argus"]}`.
- **Result:** 403.
- **Conflict:** the docstring at `routes/sheets.py:3-4` says "the refresh also accepts `pipeline.run`", but the route at `:105-106` uses `require_admin`.
- **Fix:** decide which is right, then either accept `admin` or `pipeline.run` on the route, or correct the docstring and docs.

## F-10 · Low · No audit target filter
- **Steps:** `GET /api/admin/audit?target_id=<id>&page_size=200`.
- **Result:** 200 rows, 194 of them for other targets; the parameter is ignored. The Audit page has no target field either.
- **Location:** `routes/admin.py:127-157` and `pages/admin/AuditPage.tsx` (filter bar).
- **Fix:** add `target_type` and `target_id` parameters to both the list and the CSV export, plus a field in the UI.

## F-11 · Medium · CSV formula injection in the audit export (fix pending)
- **Steps:**
  1. Anonymous `POST /api/auth/login {"email":"=HYPERLINK(\"http://127.0.0.1:9\",\"qa-ft-…\")","password":"x"}`. This is an unknown email, so no account is touched.
  2. Admin `GET /api/admin/audit/export.csv?action=LOGIN_FAILED`.
- **Result:** the row contains `"=HYPERLINK(""http://127.0.0.1:9"",…)"` unescaped; Excel treats it as a live formula.
- **Location:** `routes/admin.py:160-177`.
- **Status:** the working tree adds `core/csvsafe.py`, wraps the rows at `admin.py:173-175` (and the series export), and adds `tests/test_csv_safety.py`. The running API didn't have this. Restart the API and re-run FT-ADM-07.

## F-12 · Low · Routing 404 without the error format (fix pending)
- **Steps:** `GET /api/does-not-exist`.
- **Result:** `404 {"detail":"Not Found"}` instead of the usual error format.
- **Location:** `core/errors.py`. The working tree now registers Starlette's `HTTPException` (`errors.py:34-35`) and adds `test_hardening.py`. Re-run FT-HLT-03.

## F-13 · Low · Users dialog error detail
- **Steps:** create a user with email `bad@localhost`.
- **Result:** the alert says only "Invalid request."; the API's `error.details.fields` is not shown.
- **Location:** `components/Feedback.tsx:37` (`ErrorAlert`); `api/client.ts:61` already keeps `details`.
- **Fix:** render each field and its message.

## F-14 · Low · Unknown module shows 403
- **Steps:** as admin, open `/m/nope_module`.
- **Result:** "403 You don't have access to this module"; the API returns 404 for the same code.
- **Location:** `pages/ModulePage.tsx:98`.
- **Fix:** show 404 for unknown codes and keep 403 for real modules the user lacks.

## F-15 · Low · Stale tab title
- **Steps:** open /change-password from Settings, or /login after signing out.
- **Result:** the tab still shows "Settings · AASTAR" or "Market Overview · AASTAR".
- **Location:** only `AppShell.tsx:158` sets `document.title`; `LoginPage` and `ChangePasswordPage` render outside AppShell.
- **Fix:** set the title in those two pages.

## F-16 · Low · Atlas empty state
- **Steps:** on /atlas, search "zzzz-no-match".
- **Result:** the table body is empty with no message.
- **Location:** `pages/AtlasPage.tsx:189-198`.
- **Fix:** add a "No plants match" row (DASH-14).

## F-17 · Low · Change-password handler (hardening)
- **Steps:** fill in the form with a mismatched confirmation and call `form.requestSubmit()`.
- **Result:** the request is sent and returns 200. A real click or Enter is blocked by the disabled default button.
- **Location:** `pages/ChangePasswordPage.tsx:31-44`.
- **Fix:** add `if (!valid) return;` at the start of `submit`. The login page also has `noValidate` and no handler check.

## Observations (not counted as defects)
- **Queued jobs never expire.** With no worker running, CFTC and sheet refresh jobs stay queued. /cftc and /admin/sheets then show "Refreshing…" with Refresh disabled forever. A queued `recompute` blocks pipeline triggers with 409. `recover_interrupted` only handles running jobs. Consider a timeout or a cancel action.
- **AI reports are shared.** Any user with `ai_analysis` can open any report, and the list shows each requester's email. Confirm this is intended.
- **News upload validation is unreachable here** while Claude is not configured (FT-NEWS-06).
- **Settings rows.** Changing and restoring settings left explicit `ops.app_settings` rows for idle (30) and absolute (12). The values equal the env defaults.

## Test-design corrections (not defects)
- FT-AI-03 first failed because of my own test bug: the check returned an object instead of `true`, and the evidence helper treated the report's `error` text as an error response. Both are fixed in the suite.
- The `requestSubmit()` side effect on the UI viewer's password is recorded as F-17. I restored the password through the UI.
- F-11 and F-12 reflect the running API, not the newer working tree.
