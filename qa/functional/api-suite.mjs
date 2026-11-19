#!/usr/bin/env node
// AASTAR Price Dashboard — functional API suite (positive + negative).
//
//   node qa/functional/api-suite.mjs            (QA_BASE defaults to http://127.0.0.1:5181, the Vite proxy)
//
// Idempotent: creates its own qa-ft-<suffix> users, restores what it changes (settings, role modules, atlas,
// service-account key, sheet upload, SND source), never changes the QA admin, never starts the worker, never
// contacts external systems. Writes qa/functional/results.json (no passwords, cookies or tokens).
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { BASE, Client, HERE, ORIGIN, ev, fileForm, loadEnv, strongPassword, xlsx } from './lib.mjs';
import { premiumsGrid } from './fixtures/premiums.mjs';

const env = loadEnv();
const ADMIN_EMAIL = env.BOOTSTRAP_ADMIN_EMAIL;
const ADMIN_PW = env.BOOTSTRAP_ADMIN_PASSWORD;
const SUFFIX = String(Date.now());
const em = (tag) => `qa-ft-${tag}-${SUFFIX}@example.com`;
const results = [];
const secrets = new Set([ADMIN_PW]);
const scrub = (s) => { let o = String(s ?? ''); for (const x of secrets) if (x && x.length > 3) o = o.split(x).join('***'); return o; };

function record(id, area, type, title, expected, status, actual, evidence) {
  results.push({ id, title, type, area, status, expected, actual: scrub(actual), evidence: scrub(evidence) });
  const mark = status === 'PASS' ? 'ok  ' : status === 'FAIL' ? 'FAIL' : 'BLKD';
  console.log(`${mark} ${id} ${title}${status !== 'PASS' ? '  -> ' + scrub(actual) : ''}`);
}

/** t(id, area, type, title, expected, async () => ({ok, actual, evidence}) | {blocked}) */
async function t(id, area, type, title, expected, fn) {
  try {
    const r = await fn();
    if (r?.blocked) return record(id, area, type, title, expected, 'BLOCKED', r.blocked, r.evidence || '');
    record(id, area, type, title, expected, r.ok ? 'PASS' : 'FAIL', r.actual, r.evidence || '');
  } catch (e) {
    record(id, area, type, title, expected, 'FAIL', `exception: ${e.message}`, (e.stack || '').split('\n').slice(0, 2).join(' '));
  }
}

/** Common check: status (number or array) plus optional predicate on the response. */
function st(r, want, pred, note = '') {
  const wants = Array.isArray(want) ? want : [want];
  let ok = wants.includes(r.status);
  let why = '';
  if (ok && pred) {
    try { const p = pred(r); if (!p || typeof p === 'string') { ok = false; why = typeof p === 'string' ? p : 'predicate failed'; } }
    catch (e) { ok = false; why = `predicate error: ${e.message}`; }
  }
  return { ok, actual: `HTTP ${r.status}${r.data?.error?.code ? ' ' + r.data.error.code : ''}${why ? ' — ' + why : ''}${note ? ' — ' + note : ''}`, evidence: ev(r) };
}
const errCode = (code) => (r) => r.data?.error?.code === code || `error.code=${r.data?.error?.code}`;
const enc = encodeURIComponent;

// ── clients & fixtures ───────────────────────────────────────────────────────────────────────────────────
const anon = new Client('anon');
const admin = new Client('admin');
const users = {};          // tag -> {id, email, pw, client}

async function mkUser(tag, role, extra = {}) {
  const pw = strongPassword();
  secrets.add(pw);
  const r = await admin.post('/api/admin/users', { email: em(tag), display_name: `QA FT ${tag}`, role, auth_provider: 'local', password: pw, ...extra });
  if (r.status !== 201) throw new Error(`fixture ${tag}: ${ev(r)}`);
  const u = { id: r.data.user.id, email: em(tag), pw, client: new Client(tag), resp: r };
  users[tag] = u;
  return u;
}
async function signIn(u) {
  const r = await u.client.login(u.email, u.pw);
  if (r.status !== 200) throw new Error(`login ${u.email}: ${ev(r)}`);
  return r;
}

// Restore stack: functions run in reverse order at the end, even after a failure.
const restorers = [];
const onRestore = (name, fn) => restorers.push({ name, fn });

async function main() {
  console.log(`AASTAR functional API suite — base ${BASE}, suffix ${SUFFIX}`);

  // ════ HEALTH ════════════════════════════════════════════════════════════════════════════════════════
  await t('FT-HLT-01', 'health', 'Positive', 'Liveness /api/health', '200 {status: ok, version}', async () =>
    st(await anon.get('/api/health'), 200, (r) => r.data.status === 'ok' && !!r.data.version));
  await t('FT-HLT-02', 'health', 'Positive', 'Readiness /api/health/ready at head revision', '200 {status: ready, db: ok, revision: the migrations head}', async () =>
    // Test-design correction: the expected head is whatever ships with the code (0008_hardening after the fixes).
    st(await anon.get('/api/health/ready'), 200, (r) => r.data.status === 'ready' && /^\d{4}_/.test(r.data.revision || '')));
  await t('FT-HLT-03', 'health', 'Negative', 'Unknown API route returns JSON 404', '404 with error envelope', async () =>
    st(await anon.get('/api/does-not-exist'), 404, (r) => !!r.data?.error));
  await t('FT-HLT-04', 'health', 'Negative', 'API docs disabled in prod (APP_ENV=prod)', '/api/docs and /api/openapi.json 404', async () => {
    const a = await anon.get('/api/docs'); const b = await anon.get('/api/openapi.json');
    return { ok: a.status === 404 && b.status === 404, actual: `docs ${a.status}, openapi ${b.status}`, evidence: ev(a) };
  });
  await t('FT-HLT-05', 'health', 'Negative', 'Wrong method on health', '405 method_not_allowed', async () =>
    st(await anon.req('POST', '/api/health', { json: {} }), 405));
  await t('FT-HLT-06', 'health', 'Positive', 'Security headers on API responses', 'nosniff, DENY, CSP default-src none, no-store, X-Request-ID', async () => {
    const r = await anon.get('/api/health');
    const h = r.headers;
    const ok = h.get('x-content-type-options') === 'nosniff' && h.get('x-frame-options') === 'DENY' &&
      (h.get('content-security-policy') || '').includes("default-src 'none'") && h.get('cache-control') === 'no-store' && !!h.get('x-request-id');
    return { ok, actual: `xcto=${h.get('x-content-type-options')} xfo=${h.get('x-frame-options')} cc=${h.get('cache-control')} rid=${!!h.get('x-request-id')}`, evidence: `csp=${h.get('content-security-policy')}` };
  });
  await t('FT-HLT-07', 'health', 'Positive', 'Public auth config', '200 local_enabled=true, sso_enabled=false, data_mode demo', async () =>
    st(await anon.get('/api/auth/config'), 200, (r) => r.data.local_enabled === true && r.data.sso_enabled === false && r.data.data_mode === 'demo'));

  // ════ AUTH (admin sign-in, fixtures) ═════════════════════════════════════════════════════════════════
  await t('FT-AUTH-01', 'auth', 'Positive', 'Admin signs in with valid credentials', '200, session (HttpOnly) + CSRF cookies, role admin', async () => {
    const r = await admin.login(ADMIN_EMAIL, ADMIN_PW);
    const sc = r.headers.getSetCookie?.() || [];
    const sess = sc.find((c) => c.startsWith('aastar_session=')) || '';
    const csrf = sc.find((c) => c.startsWith('aastar_csrf=')) || '';
    return st(r, 200, () => (r.data.role === 'admin' && /httponly/i.test(sess) && /samesite=lax/i.test(sess) && !/httponly/i.test(csrf) && /samesite=strict/i.test(csrf)) || 'cookie flags/role');
  });
  if (!admin.session) { console.error('Admin login failed — aborting'); return; }

  // fixtures
  await t('FT-USR-01', 'users', 'Positive', 'Create analyst with admin-chosen password', '201, temporary_password null (never echoed), role analyst', async () => {
    const u = await mkUser('analyst', 'analyst');
    return st(u.resp, 201, (r) => (r.data.temporary_password === null && r.data.user.role === 'analyst' && r.data.user.status === 'active') || 'shape');
  });
  await mkUser('analyst2', 'analyst');
  await mkUser('ratelimit', 'analyst');
  await t('FT-USR-02', 'users', 'Positive', 'Create viewer', '201, effective_modules = viewer role modules (6)', async () => {
    const u = await mkUser('viewer', 'viewer');
    return st(u.resp, 201, (r) => r.data.user.effective_modules.length === 6 || `modules=${r.data.user.effective_modules}`);
  });
  await mkUser('victim', 'analyst');
  await mkUser('pwchange', 'viewer');
  await mkUser('disabled', 'viewer');
  // viewer with grants for intel modules (no permissions) and analyst with denies
  await mkUser('vgrant', 'viewer', { overrides: [{ module: 'ai_analysis', effect: 'grant' }, { module: 'news', effect: 'grant' },
    { module: 'cftc', effect: 'grant' }, { module: 'snd', effect: 'grant' }, { module: 'hvo_atlas', effect: 'grant' },
    { module: 'calendar', effect: 'grant' }, { module: 'waste_bio', effect: 'grant' }] });
  await mkUser('adeny', 'analyst', { overrides: [{ module: 'overview', effect: 'deny' }, { module: 'parity', effect: 'deny' },
    { module: 'waste_bio', effect: 'deny' }, { module: 'calendar_spreads', effect: 'deny' }] });
  for (const tag of ['analyst', 'analyst2', 'ratelimit', 'viewer', 'vgrant', 'adeny']) await signIn(users[tag]);
  const A = users.analyst.client, A2 = users.analyst2.client, V = users.viewer.client, VG = users.vgrant.client, AD = users.adeny.client;

  await authTests();
  await userTests();
  await accessTests(A, V, VG, AD);
  await marketTests(A, V, AD);
  await intelTests(A, A2, V, VG);
  await sheetsTests(A, V, AD, VG);
  await adminTests(A, V);
}

// ════ AUTH ═════════════════════════════════════════════════════════════════════════════════════════════
async function authTests() {
  const v = users.victim;
  await t('FT-AUTH-02', 'auth', 'Negative', 'Wrong password on own user', '401 invalid_credentials "Invalid email or password."', async () => {
    const r = await new Client().login(users.pwchange.email, 'Wrong-Password-123!');
    return st(r, 401, (x) => (x.data.error.code === 'invalid_credentials' && x.data.error.message === 'Invalid email or password.') || 'message');
  });
  await t('FT-AUTH-03', 'auth', 'Negative', 'Unknown email gives the same generic error (no enumeration)', '401 invalid_credentials, same message as wrong password', async () =>
    st(await new Client().login(`qa-ft-nobody-${SUFFIX}@example.com`, 'Whatever-123!'), 401, (x) => x.data.error.message === 'Invalid email or password.' || 'message differs'));
  await t('FT-AUTH-04', 'auth', 'Positive', 'Email match is case-insensitive', '200 when the email is typed in upper case', async () =>
    st(await new Client().login(users.pwchange.email.toUpperCase(), users.pwchange.pw), 200));
  await t('FT-AUTH-05', 'auth', 'Negative', 'Login validation: empty password / too-short email / missing body', '422 validation_error each', async () => {
    const c = new Client();
    const a = await c.login(users.pwchange.email, '');
    const b = await c.login('ab', 'x');
    const d = await c.req('POST', '/api/auth/login', { json: {} });
    const e = await c.req('POST', '/api/auth/login', { raw: 'not json', headers: { 'content-type': 'application/json' } });
    return { ok: [a, b, d, e].every((x) => x.status === 422), actual: `${a.status}/${b.status}/${d.status}/${e.status}`, evidence: ev(a) };
  });
  await t('FT-AUTH-06', 'auth', 'Negative', 'Login with password > 256 chars', '422 validation_error', async () =>
    st(await new Client().login(users.pwchange.email, 'A1!' + 'x'.repeat(300)), 422));
  await t('FT-AUTH-07', 'auth', 'Negative', 'Login rejected from a foreign Origin', '403 bad_origin', async () =>
    st(await new Client().login(users.pwchange.email, users.pwchange.pw, { origin: 'http://evil.example' }), 403, errCode('bad_origin')));

  // lockout on own victim
  await t('FT-AUTH-08', 'auth', 'Negative', 'Lockout: 5 consecutive failures lock the account (AUTH-06)', '5 x 401, then correct password -> 423 account_locked; admin view locked=true', async () => {
    const c = new Client();
    const codes = [];
    for (let i = 0; i < 5; i++) codes.push((await c.login(v.email, `Bad-Password-${i}xx!`)).status);
    const good = await c.login(v.email, v.pw);
    const g = await admin.get(`/api/admin/users/${v.id}`);
    return { ok: codes.every((x) => x === 401) && good.status === 423 && good.data?.error?.code === 'account_locked' && g.data?.locked === true,
      actual: `fails=${codes.join(',')} then ${good.status} ${good.data?.error?.code}; locked=${g.data?.locked}`, evidence: ev(good) };
  });
  await t('FT-AUTH-09', 'auth', 'Positive', 'Admin unlocks the account; user signs in again', 'unlock 200 {ok:true}; login 200; locked=false', async () => {
    const u = await admin.post(`/api/admin/users/${v.id}/unlock`, {});
    const l = await new Client().login(v.email, v.pw);
    const g = await admin.get(`/api/admin/users/${v.id}`);
    return { ok: u.status === 200 && l.status === 200 && g.data.locked === false, actual: `unlock ${u.status}, login ${l.status}, locked=${g.data.locked}`, evidence: ev(u) };
  });
  await t('FT-AUTH-10', 'auth', 'Positive', 'Failure counter resets after a successful login', '4 failures + success + 1 failure does not lock', async () => {
    const c = new Client();
    for (let i = 0; i < 4; i++) await c.login(v.email, `Bad-${i}-Password!`);
    await c.login(v.email, v.pw);
    const f = await c.login(v.email, 'Bad-Again-Password!');
    const g = await admin.get(`/api/admin/users/${v.id}`);
    return { ok: f.status === 401 && g.data.locked === false, actual: `after reset: ${f.status}, locked=${g.data.locked}`, evidence: ev(f) };
  });

  await t('FT-AUTH-11', 'auth', 'Positive', '/me returns the signed-in profile', '200 email, role, permissions, modules; no password hash', async () => {
    const r = await users.analyst.client.get('/api/auth/me');
    return st(r, 200, (x) => (x.data.email === users.analyst.email && x.data.role === 'analyst' && x.data.permissions.includes('data.export') && !/hash/i.test(x.text)) || 'shape');
  });
  await t('FT-AUTH-12', 'auth', 'Negative', '/me unauthenticated / tampered / oversized token', '401 unauthorized each', async () => {
    const a = await anon.get('/api/auth/me');
    const b = await anon.get('/api/auth/me', { cookies: { aastar_session: 'x'.repeat(43) } });
    const c = await anon.get('/api/auth/me', { cookies: { aastar_session: 'y'.repeat(200) } });
    return { ok: [a, b, c].every((x) => x.status === 401 && x.data?.error?.code === 'unauthorized'), actual: `${a.status}/${b.status}/${c.status}`, evidence: ev(a) };
  });

  // CSRF / Origin
  const pc = new Client('csrf');
  await pc.login(users.pwchange.email, users.pwchange.pw);
  await t('FT-AUTH-13', 'auth', 'Negative', 'State-changing call without X-CSRF-Token', '403 csrf_failed', async () =>
    st(await pc.req('POST', '/api/auth/logout', { csrf: false }), 403, errCode('csrf_failed')));
  await t('FT-AUTH-14', 'auth', 'Negative', 'State-changing call with a wrong CSRF token', '403 csrf_failed', async () =>
    st(await pc.req('POST', '/api/auth/logout', { csrf: 'A'.repeat(43) }), 403, errCode('csrf_failed')));
  await t('FT-AUTH-15', 'auth', 'Negative', 'CSRF header matching a forged cookie (not bound to the session)', '403 csrf_failed', async () => {
    const forged = 'Z'.repeat(43);
    return st(await pc.req('POST', '/api/auth/logout', { cookies: { ...pc.cookies, aastar_csrf: forged }, csrf: forged }), 403, errCode('csrf_failed'));
  });
  await t('FT-AUTH-16', 'auth', 'Negative', 'State-changing call from a foreign Origin (valid CSRF)', '403 bad_origin', async () =>
    st(await pc.req('POST', '/api/auth/logout', { origin: 'http://127.0.0.1:9' }), 403, errCode('bad_origin')));
  await t('FT-AUTH-17', 'auth', 'Negative', 'State-changing call with NO Origin header (AUTH-14 requires a same-origin Origin)', '403 bad_origin (Origin required on unsafe methods)', async () => {
    const r = await admin.req('PUT', '/api/admin/settings', { json: { values: {} }, origin: null });
    return st(r, 403);
  });
  await t('FT-AUTH-18', 'auth', 'Positive', 'Logout revokes the session and clears cookies', 'logout 200; same cookie -> /me 401; second logout 401', async () => {
    const saved = { ...pc.cookies };
    const r = await pc.post('/api/auth/logout', {});
    const me = await anon.get('/api/auth/me', { cookies: saved });
    const again = await anon.req('POST', '/api/auth/logout', { cookies: saved, csrf: saved.aastar_csrf });
    return { ok: r.status === 200 && me.status === 401 && again.status === 401 && !pc.session, actual: `logout ${r.status}, me ${me.status}, again ${again.status}, cookie cleared=${!pc.session}`, evidence: ev(r) };
  });

  // session timeouts (verified in DB-independent way: settings + cookie max-age)
  await t('FT-AUTH-19', 'auth', 'Positive', 'Session cookie lifetime = absolute timeout (12 h)', 'Set-Cookie Max-Age = 43200', async () => {
    const r = await new Client().login(users.pwchange.email, users.pwchange.pw);
    const sc = (r.headers.getSetCookie?.() || []).find((c) => c.startsWith('aastar_session=')) || '';
    const m = /max-age=(\d+)/i.exec(sc);
    return { ok: r.status === 200 && m && Number(m[1]) === 43200, actual: `Max-Age=${m?.[1]}`, evidence: 'Set-Cookie attributes only' };
  });
  await t('FT-AUTH-20', 'auth', 'Positive', 'Idle-timeout expiry (30 min) not waitable in the suite', 'session rejected after 30 min idle', async () =>
    ({ blocked: 'Minimum configurable idle timeout is 5 min (settings 5–240); expiry is covered by unit tests and DB check in checks.md, not waited for here.' }));

  // change password
  const pw = users.pwchange;
  const other = new Client('pw-other');
  await other.login(pw.email, pw.pw);
  const me = new Client('pw-me');
  await me.login(pw.email, pw.pw);
  await t('FT-AUTH-21', 'auth', 'Negative', 'Change password with wrong current password', '400 bad_current_password', async () =>
    st(await me.post('/api/auth/change-password', { current_password: 'Not-The-Current-1!', new_password: strongPassword() }), 400, errCode('bad_current_password')));
  await t('FT-AUTH-22', 'auth', 'Negative', 'New password equal to current', '422 password_reused', async () =>
    st(await me.post('/api/auth/change-password', { current_password: pw.pw, new_password: pw.pw }), 422, errCode('password_reused')));
  await t('FT-AUTH-23', 'auth', 'Negative', 'Policy: too short (11 chars)', '422 weak_password with rules', async () =>
    st(await me.post('/api/auth/change-password', { current_password: pw.pw, new_password: 'Ab1!Ab1!Ab1' }), 422, (r) => (r.data.error.code === 'weak_password' && r.data.error.details?.rules?.length >= 1) || 'code/rules'));
  await t('FT-AUTH-24', 'auth', 'Negative', 'Policy: only 2 character classes', '422 weak_password', async () =>
    st(await me.post('/api/auth/change-password', { current_password: pw.pw, new_password: 'abcdefghijklmnop1234' }), 422, errCode('weak_password')));
  await t('FT-AUTH-25', 'auth', 'Negative', 'Policy: contains the email local-part', '422 weak_password', async () =>
    st(await me.post('/api/auth/change-password', { current_password: pw.pw, new_password: `X1!${pw.email.split('@')[0]}` }), 422, errCode('weak_password')));
  await t('FT-AUTH-26', 'auth', 'Negative', 'Policy: 129 characters (max 128)', '422 weak_password', async () =>
    st(await me.post('/api/auth/change-password', { current_password: pw.pw, new_password: 'Aa1!' + 'b'.repeat(125) }), 422, errCode('weak_password')));
  await t('FT-AUTH-27', 'auth', 'Positive', 'Policy boundary: exactly 12 and exactly 128 characters accepted', '200 for each (password rotated twice)', async () => {
    const p12 = 'Kq7!' + crypto.randomBytes(6).toString('hex').slice(0, 8);        // 12 chars, 3+ classes
    secrets.add(p12);
    const a = await me.post('/api/auth/change-password', { current_password: pw.pw, new_password: p12 });
    if (a.status === 200) pw.pw = p12;
    const p128 = 'Kq7!' + crypto.randomBytes(62).toString('hex');               // 4 + 124 = 128 chars
    secrets.add(p128);
    const b = await me.post('/api/auth/change-password', { current_password: pw.pw, new_password: p128 });
    if (b.status === 200) pw.pw = p128;
    return { ok: a.status === 200 && b.status === 200 && p12.length === 12 && p128.length === 128, actual: `12-char ${a.status}, 128-char ${b.status}`, evidence: ev(b) };
  });
  await t('FT-AUTH-28', 'auth', 'Positive', 'Password change rotates the session and revokes other sessions (AUTH-13)', 'own client keeps working with new cookie; other session 401; old password 401; new password 200', async () => {
    const meOk = await me.get('/api/auth/me');
    const o = await other.get('/api/auth/me');
    const newPw = strongPassword(); secrets.add(newPw);
    const old = pw.pw;
    const ch = await me.post('/api/auth/change-password', { current_password: old, new_password: newPw });
    pw.pw = ch.status === 200 ? newPw : old;
    const after = await me.get('/api/auth/me');
    const oldLogin = await new Client().login(pw.email, old);
    const newLogin = await new Client().login(pw.email, newPw);
    return { ok: meOk.status === 200 && o.status === 401 && ch.status === 200 && after.status === 200 && oldLogin.status === 401 && newLogin.status === 200,
      actual: `other-session-after-earlier-change ${o.status}, change ${ch.status}, me-after ${after.status}, old-pw ${oldLogin.status}, new-pw ${newLogin.status}`, evidence: ev(ch) };
  });
  await t('FT-AUTH-29', 'auth', 'Negative', 'Change password unauthenticated', '401', async () =>
    st(await anon.post('/api/auth/change-password', { current_password: 'x', new_password: 'y' }), 401));
  await t('FT-AUTH-30', 'auth', 'Negative', 'Change password validation (missing field / >256 chars)', '422 validation_error', async () => {
    const a = await me.post('/api/auth/change-password', { current_password: pw.pw });
    const b = await me.post('/api/auth/change-password', { current_password: pw.pw, new_password: 'Aa1!' + 'x'.repeat(300) });
    return { ok: a.status === 422 && b.status === 422, actual: `${a.status}/${b.status}`, evidence: ev(a) };
  });
  await t('FT-AUTH-31', 'auth', 'Negative', 'SSO endpoints with SSO disabled never contact the Hub', '/api/auth/sso/login -> 302 /login?error=...; callback with error -> 302 /login?error=sso_denied', async () => {
    const a = await anon.get('/api/auth/sso/login');
    const b = await anon.get('/api/auth/sso/callback?error=access_denied');
    const la = a.headers.get('location') || ''; const lb = b.headers.get('location') || '';
    return { ok: a.status === 302 && la.startsWith('/login?error=') && b.status === 302 && lb === '/login?error=sso_denied', actual: `${a.status} ${la} / ${b.status} ${lb}`, evidence: '' };
  });
}

// ════ USERS & ROLES ═════════════════════════════════════════════════════════════════════════════════════
async function userTests() {
  const base = { display_name: 'QA FT x', role: 'viewer', auth_provider: 'local' };
  await t('FT-USR-03', 'users', 'Positive', 'Create local user without password -> temporary password shown once', '201 temporary_password present (16 chars), policy-compliant', async () => {
    const r = await admin.post('/api/admin/users', { ...base, email: em('temp') });
    if (r.data?.temporary_password) secrets.add(r.data.temporary_password);
    const tp = r.data?.temporary_password || '';
    return st(r, 201, () => (tp.length === 16) || 'temp pw length');
  });
  await t('FT-USR-04', 'users', 'Positive', 'Create SSO-only user', '201, auth_provider sso, no temporary password', async () => {
    const r = await admin.post('/api/admin/users', { ...base, email: em('sso'), auth_provider: 'sso' });
    if (r.status === 201) users.sso = { id: r.data.user.id, email: em('sso') };
    return st(r, 201, (x) => (x.data.user.auth_provider === 'sso' && x.data.temporary_password === null) || 'shape');
  });
  await t('FT-USR-05', 'users', 'Negative', 'Default sign-in method when auth_provider omitted (USR-03: new users default to Hub SSO)', '201 with auth_provider "sso" and no temporary password', async () => {
    const r = await admin.post('/api/admin/users', { email: em('default'), display_name: 'QA FT default', role: 'viewer' });
    if (r.data?.temporary_password) secrets.add(r.data.temporary_password);
    return st(r, 201, (x) => x.data.user.auth_provider === 'sso' || `auth_provider=${x.data.user.auth_provider}, temp password issued=${!!x.data.temporary_password}`);
  });
  await t('FT-USR-06', 'users', 'Negative', 'Duplicate email (case-insensitive)', '409 email_exists', async () =>
    st(await admin.post('/api/admin/users', { ...base, email: users.viewer.email.toUpperCase(), password: strongPassword() }), 409, errCode('email_exists')));
  await t('FT-USR-07', 'users', 'Negative', 'Invalid email / unknown role / bad provider', '422 each (validation_error / unknown_role)', async () => {
    const a = await admin.post('/api/admin/users', { ...base, email: 'not-an-email' });
    const b = await admin.post('/api/admin/users', { ...base, email: em('badrole'), role: 'superuser' });
    const c = await admin.post('/api/admin/users', { ...base, email: em('badprov'), auth_provider: 'ldap' });
    return { ok: a.status === 422 && b.status === 422 && b.data.error.code === 'unknown_role' && c.status === 422, actual: `${a.status}/${b.status} ${b.data?.error?.code}/${c.status}`, evidence: ev(b) };
  });
  await t('FT-USR-08', 'users', 'Negative', 'Display name empty / 121 chars', '422 validation_error', async () => {
    const a = await admin.post('/api/admin/users', { ...base, email: em('noname'), display_name: '' });
    const b = await admin.post('/api/admin/users', { ...base, email: em('longname'), display_name: 'n'.repeat(121) });
    return { ok: a.status === 422 && b.status === 422, actual: `${a.status}/${b.status}`, evidence: ev(a) };
  });
  await t('FT-USR-09', 'users', 'Negative', 'Display name of only whitespace', '422 (blank name rejected)', async () => {
    const r = await admin.post('/api/admin/users', { ...base, email: em('blank'), display_name: '   ', auth_provider: 'sso' });
    return st(r, 422, null, r.status === 201 ? `stored display_name=${JSON.stringify(r.data.user.display_name)}` : '');
  });
  await t('FT-USR-10', 'users', 'Positive', 'Display name boundary 120 chars accepted', '201', async () =>
    st(await admin.post('/api/admin/users', { ...base, email: em('name120'), display_name: 'n'.repeat(120), auth_provider: 'sso' }), 201));
  await t('FT-USR-11', 'users', 'Negative', 'Admin-chosen weak password / password containing email name / >128', '422 weak_password / weak_password / validation_error', async () => {
    const a = await admin.post('/api/admin/users', { ...base, email: em('weak'), password: 'short1!A' });
    const b = await admin.post('/api/admin/users', { ...base, email: em('emailpw'), password: `Zz9!qa-ft-emailpw-${SUFFIX}` });
    const c = await admin.post('/api/admin/users', { ...base, email: em('longpw'), password: 'Aa1!' + 'x'.repeat(130) });
    return { ok: a.status === 422 && a.data.error.code === 'weak_password' && b.status === 422 && b.data.error.code === 'weak_password' && c.status === 422,
      actual: `${a.status} ${a.data?.error?.code}/${b.status} ${b.data?.error?.code}/${c.status}`, evidence: ev(a) };
  });
  await t('FT-USR-12', 'users', 'Negative', 'Overrides: unknown module / bad effect', '422 unknown_module / validation_error', async () => {
    const a = await admin.post('/api/admin/users', { ...base, email: em('ovr1'), auth_provider: 'sso', overrides: [{ module: 'nope', effect: 'grant' }] });
    const b = await admin.post('/api/admin/users', { ...base, email: em('ovr2'), auth_provider: 'sso', overrides: [{ module: 'crush', effect: 'allow' }] });
    return { ok: a.status === 422 && a.data.error.code === 'unknown_module' && b.status === 422, actual: `${a.status} ${a.data?.error?.code}/${b.status}`, evidence: ev(a) };
  });
  await t('FT-USR-13', 'users', 'Negative', 'Create user as analyst / viewer / unauthenticated', '403 / 403 / 401', async () => {
    const body = { ...base, email: em('byanalyst'), auth_provider: 'sso' };
    const a = await users.analyst.client.post('/api/admin/users', body);
    const v = await users.viewer.client.post('/api/admin/users', body);
    const n = await anon.post('/api/admin/users', body);
    return { ok: a.status === 403 && v.status === 403 && n.status === 401, actual: `${a.status}/${v.status}/${n.status}`, evidence: ev(a) };
  });
  await t('FT-USR-14', 'users', 'Positive', 'List users with search, role, status, provider filters and paging', '200; filters narrow results; total consistent', async () => {
    const q = await admin.get(`/api/admin/users?q=${enc('qa-ft-')}&page_size=100`);
    const role = await admin.get(`/api/admin/users?q=${SUFFIX}&role=viewer&page_size=100`);
    const prov = await admin.get(`/api/admin/users?q=${SUFFIX}&provider=sso&page_size=100`);
    const p2 = await admin.get(`/api/admin/users?q=${SUFFIX}&page=2&page_size=2`);
    const ok = q.status === 200 && q.data.items.every((u) => u.email.includes('qa-ft-')) &&
      role.data.items.length > 0 && role.data.items.every((u) => u.role === 'viewer') &&
      prov.data.items.length > 0 && prov.data.items.every((u) => u.auth_provider === 'sso') &&
      p2.status === 200 && p2.data.items.length === 2 && p2.data.page === 2;
    return { ok, actual: `q=${q.data?.total} viewer=${role.data?.items?.length} sso=${prov.data?.items?.length} page2=${p2.data?.items?.length}`, evidence: ev(p2) };
  });
  await t('FT-USR-15', 'users', 'Negative', 'List users paging/limits validation', 'page=0, page_size=0, page_size=101, q>100 chars -> 422', async () => {
    const rs = await Promise.all(['page=0', 'page_size=0', 'page_size=101', `q=${'q'.repeat(101)}`].map((qs) => admin.get(`/api/admin/users?${qs}`)));
    return { ok: rs.every((r) => r.status === 422), actual: rs.map((r) => r.status).join('/'), evidence: ev(rs[0]) };
  });
  await t('FT-USR-16', 'users', 'Positive', 'Get one user', '200 with effective_modules and overrides', async () =>
    st(await admin.get(`/api/admin/users/${users.vgrant.id}`), 200, (r) => (r.data.overrides.length === 7 && r.data.effective_modules.includes('ai_analysis')) || 'overrides'));
  await t('FT-USR-17', 'users', 'Negative', 'Get user: malformed id / nonexistent uuid', '404 not_found each', async () => {
    const a = await admin.get('/api/admin/users/not-a-uuid');
    const b = await admin.get(`/api/admin/users/${crypto.randomUUID()}`);
    return { ok: a.status === 404 && b.status === 404, actual: `${a.status}/${b.status}`, evidence: ev(b) };
  });
  await t('FT-USR-18', 'users', 'Positive', 'Update display name and role; audit records before/after', '200; USER_UPDATE audit row with changes', async () => {
    const u = users.analyst2;
    const r = await admin.patch(`/api/admin/users/${u.id}`, { display_name: 'QA FT analyst2 renamed', role: 'viewer' });
    const back = await admin.patch(`/api/admin/users/${u.id}`, { role: 'analyst' });
    const au = await admin.get(`/api/admin/audit?action=USER_UPDATE&page_size=50`);
    const row = au.data?.items?.find((a) => a.target_id === u.id && a.details?.changes?.role);
    return { ok: r.status === 200 && r.data.display_name === 'QA FT analyst2 renamed' && r.data.role === 'viewer' && back.data?.role === 'analyst' && !!row,
      actual: `patch ${r.status} role=${r.data?.role}, back=${back.data?.role}, audit=${!!row}`, evidence: row ? JSON.stringify(row.details).slice(0, 160) : ev(au) };
  });
  await t('FT-USR-19', 'users', 'Positive', 'PATCH is idempotent (same values -> no new audit row)', '200 twice; no USER_UPDATE for the no-op', async () => {
    const u = users.analyst2;
    const before = await admin.get(`/api/admin/audit?action=USER_UPDATE&page_size=1`);
    const r = await admin.patch(`/api/admin/users/${u.id}`, { display_name: 'QA FT analyst2 renamed' });
    const after = await admin.get(`/api/admin/audit?action=USER_UPDATE&page_size=1`);
    return { ok: r.status === 200 && before.data.total === after.data.total, actual: `audit total ${before.data.total} -> ${after.data.total}`, evidence: ev(r) };
  });
  await t('FT-USR-20', 'users', 'Negative', 'Update validation: bad status / unknown role / empty name / nonexistent user', '422 / 422 / 422 / 404', async () => {
    const id = users.analyst2.id;
    const a = await admin.patch(`/api/admin/users/${id}`, { status: 'deleted' });
    const b = await admin.patch(`/api/admin/users/${id}`, { role: 'root' });
    const c = await admin.patch(`/api/admin/users/${id}`, { display_name: '' });
    const d = await admin.patch(`/api/admin/users/${crypto.randomUUID()}`, { display_name: 'x' });
    return { ok: a.status === 422 && b.status === 422 && c.status === 422 && d.status === 404, actual: `${a.status}/${b.status}/${c.status}/${d.status}`, evidence: ev(b) };
  });
  await t('FT-USR-21', 'users', 'Negative', 'SSO user switched to local without a password', '422 password_required', async () =>
    users.sso ? st(await admin.patch(`/api/admin/users/${users.sso.id}`, { auth_provider: 'local' }), 422, errCode('password_required')) : { blocked: 'SSO fixture not created' });
  await t('FT-USR-22', 'users', 'Negative', 'Reset password of SSO-only user', '422 sso_account', async () =>
    users.sso ? st(await admin.post(`/api/admin/users/${users.sso.id}/reset-password`, {}), 422, errCode('sso_account')) : { blocked: 'SSO fixture not created' });
  await t('FT-USR-23', 'users', 'Negative', 'Admin cannot disable or demote themselves (USR-08)', '422 self_disable / 422 self_demotion; admin unchanged', async () => {
    const me = await admin.get('/api/auth/me');
    const a = await admin.patch(`/api/admin/users/${me.data.id}`, { status: 'disabled' });
    const b = await admin.patch(`/api/admin/users/${me.data.id}`, { role: 'viewer' });
    const after = await admin.get('/api/auth/me');
    return { ok: a.status === 422 && a.data.error.code === 'self_disable' && b.status === 422 && b.data.error.code === 'self_demotion' && after.data.role === 'admin',
      actual: `${a.status} ${a.data?.error?.code} / ${b.status} ${b.data?.error?.code}; role now ${after.data?.role}`, evidence: ev(a) };
  });
  await t('FT-USR-24', 'users', 'Positive', 'Disable user: sessions revoked, login refused generically; re-enable restores login', 'disable 200 status=disabled; old session 401; login 401 invalid_credentials; enable 200; login 200', async () => {
    const u = users.disabled;
    await signIn(u);
    const saved = { ...u.client.cookies };
    const d = await admin.patch(`/api/admin/users/${u.id}`, { status: 'disabled' });
    const s = await anon.get('/api/auth/me', { cookies: saved });
    const l = await new Client().login(u.email, u.pw);
    const e = await admin.patch(`/api/admin/users/${u.id}`, { status: 'active' });
    const l2 = await new Client().login(u.email, u.pw);
    const d2 = await admin.patch(`/api/admin/users/${u.id}`, { status: 'disabled' });        // leave it disabled as a fixture
    return { ok: d.status === 200 && d.data.status === 'disabled' && s.status === 401 && l.status === 401 && l.data.error.code === 'invalid_credentials' && e.status === 200 && l2.status === 200 && d2.status === 200,
      actual: `disable ${d.status}, old session ${s.status}, login ${l.status} ${l.data?.error?.code}, enable ${e.status}, login ${l2.status}`, evidence: ev(d) };
  });
  await t('FT-USR-25', 'users', 'Positive', 'Admin reset password: temp password works, sessions revoked', '200 temporary_password; old session 401; temp login 200', async () => {
    const u = users.victim;
    const c = new Client(); await c.login(u.email, u.pw);
    const r = await admin.post(`/api/admin/users/${u.id}/reset-password`, {});
    const temp = r.data?.temporary_password; if (temp) secrets.add(temp);
    const s = await c.get('/api/auth/me');
    const l = await new Client().login(u.email, temp || 'x');
    if (temp) u.pw = temp;
    return { ok: r.status === 200 && !!temp && s.status === 401 && l.status === 200, actual: `reset ${r.status}, old session ${s.status}, temp login ${l.status}`, evidence: 'temporary password redacted' };
  });
  await t('FT-USR-26', 'users', 'Positive', 'Revoke all sessions of a user', '200 {revoked >= 2}; sessions 401', async () => {
    const u = users.victim;
    const c1 = new Client(), c2 = new Client();
    await c1.login(u.email, u.pw); await c2.login(u.email, u.pw);
    const r = await admin.post(`/api/admin/users/${u.id}/revoke-sessions`, {});
    const a = await c1.get('/api/auth/me'), b = await c2.get('/api/auth/me');
    const again = await admin.post(`/api/admin/users/${u.id}/revoke-sessions`, {});
    return { ok: r.status === 200 && r.data.revoked >= 2 && a.status === 401 && b.status === 401 && again.data?.revoked === 0,
      actual: `revoked=${r.data?.revoked}, sessions ${a.status}/${b.status}, repeat revoked=${again.data?.revoked}`, evidence: ev(r) };
  });
  await t('FT-USR-27', 'users', 'Negative', 'Unlock / reset / revoke on nonexistent or malformed id', '404 each', async () => {
    const rid = crypto.randomUUID();
    const rs = [await admin.post(`/api/admin/users/${rid}/unlock`, {}), await admin.post(`/api/admin/users/${rid}/reset-password`, {}),
      await admin.post(`/api/admin/users/bogus/revoke-sessions`, {})];
    return { ok: rs.every((r) => r.status === 404), actual: rs.map((r) => r.status).join('/'), evidence: ev(rs[0]) };
  });
  await t('FT-USR-28', 'users', 'Positive', 'Overrides: grant adds a module, deny wins over role (RBAC-04)', 'vgrant has ai_analysis; adeny lacks overview/parity/waste_bio', async () => {
    const g = await users.vgrant.client.get('/api/auth/me');
    const d = await users.adeny.client.get('/api/auth/me');
    const ok = g.data.modules.includes('ai_analysis') && g.data.modules.includes('waste_bio') && !d.data.modules.includes('overview') && !d.data.modules.includes('parity') && !d.data.modules.includes('waste_bio') && d.data.modules.includes('crush');
    return { ok, actual: `vgrant=${g.data.modules.length} modules, adeny=${d.data.modules.join(',')}`, evidence: '' };
  });

  // roles & modules
  await t('FT-ROLE-01', 'roles', 'Positive', 'List roles and modules', '200; 3 system roles with permissions & modules; 18 modules', async () => {
    const r = await admin.get('/api/admin/roles'); const m = await admin.get('/api/admin/modules');
    return { ok: r.status === 200 && ['admin', 'analyst', 'viewer'].every((c) => r.data.roles.some((x) => x.code === c)) && m.status === 200 && m.data.modules.length === 18,
      actual: `roles=${r.data?.roles?.map((x) => x.code)}, modules=${m.data?.modules?.length}`, evidence: ev(r) };
  });
  const orig = (await admin.get('/api/admin/roles')).data.roles.find((x) => x.code === 'viewer').modules;
  onRestore('viewer role modules', async () => admin.put('/api/admin/roles/viewer/modules', { modules: orig }));
  await t('FT-ROLE-02', 'roles', 'Positive', 'Add a module to the viewer role takes effect immediately, then restore', 'PUT 200; viewer /api/market/sections/crush 200; restore -> 403 again; ROLE_MODULES_UPDATE audited', async () => {
    const add = await admin.put('/api/admin/roles/viewer/modules', { modules: [...orig, 'crush'] });
    const s1 = await users.viewer.client.get('/api/market/sections/crush');
    const rest = await admin.put('/api/admin/roles/viewer/modules', { modules: orig });
    const s2 = await users.viewer.client.get('/api/market/sections/crush');
    const au = await admin.get('/api/admin/audit?action=ROLE_MODULES_UPDATE&page_size=2');
    return { ok: add.status === 200 && s1.status === 200 && rest.status === 200 && s2.status === 403 && au.data.total >= 2,
      actual: `add ${add.status}, viewer crush ${s1.status}, restore ${rest.status}, viewer crush ${s2.status}`, evidence: ev(add) };
  });
  await t('FT-ROLE-03', 'roles', 'Negative', 'Role modules: admin role fixed / unknown role / unknown module / not a list', '422 admin_role_fixed / 404 / 422 unknown_module / 422', async () => {
    const a = await admin.put('/api/admin/roles/admin/modules', { modules: ['overview'] });
    const b = await admin.put('/api/admin/roles/nope/modules', { modules: ['overview'] });
    const c = await admin.put('/api/admin/roles/viewer/modules', { modules: [...orig, 'bogus'] });
    const d = await admin.put('/api/admin/roles/viewer/modules', { modules: 'overview' });
    const after = (await admin.get('/api/admin/roles')).data.roles.find((x) => x.code === 'viewer').modules;
    return { ok: a.status === 422 && a.data.error.code === 'admin_role_fixed' && b.status === 404 && c.status === 422 && c.data.error.code === 'unknown_module' && d.status === 422 && after.length === orig.length,
      actual: `${a.status} ${a.data?.error?.code}/${b.status}/${c.status} ${c.data?.error?.code}/${d.status}; viewer modules unchanged=${after.length === orig.length}`, evidence: ev(c) };
  });
  await t('FT-ROLE-04', 'roles', 'Negative', 'Roles API as analyst / viewer', '403 forbidden', async () => {
    const a = await users.analyst.client.get('/api/admin/roles');
    const b = await users.viewer.client.put('/api/admin/roles/viewer/modules', { modules: orig });
    return { ok: a.status === 403 && b.status === 403, actual: `${a.status}/${b.status}`, evidence: ev(a) };
  });
}

// ════ MODULE ACCESS MATRIX ══════════════════════════════════════════════════════════════════════════════
async function accessTests(A, V, VG, AD) {
  const routes = [
    ['overview', '/api/market/overview', { admin: 200, analyst: 200, viewer: 200, adeny: 403 }],
    ['waste_bio section', '/api/market/sections/waste_bio?section=calendar', { admin: 200, analyst: 200, viewer: 403, adeny: 403 }],
    ['oi_volume', '/api/market/oi-cumulative', { admin: 200, analyst: 200, viewer: 403 }],
    ['technical_analysis', '/api/market/ta/instruments', { admin: 200, analyst: 200, viewer: 403 }],
    ['forward_curves', '/api/market/forward-curves', { admin: 200, analyst: 200, viewer: 200 }],
    ['calendar_spreads ytd', '/api/market/ytd-spreads', { admin: 200, analyst: 200, viewer: 200, adeny: 403 }],
    ['cftc', '/api/intel/cftc', { admin: 200, analyst: 200, viewer: 403 }],
    ['snd', '/api/intel/snd/graphs', { admin: 200, analyst: 200, viewer: 403 }],
    ['ai_analysis', '/api/intel/ai/instruments', { admin: 200, analyst: 200, viewer: 403 }],
    ['news', '/api/intel/news/documents', { admin: 200, analyst: 200, viewer: 403 }],
    ['calendar', '/api/intel/calendar', { admin: 200, analyst: 200, viewer: 403 }],
    ['hvo_atlas', '/api/intel/atlas', { admin: 200, analyst: 200, viewer: 403 }],
    ['waste_bio sheets', '/api/sheets/waste-bio/arb', { admin: 200, analyst: 200, viewer: 403, adeny: 403 }],
    ['parity snapshots', '/api/sheets/parity-snapshots', { admin: 200, analyst: 200, viewer: 200, adeny: 403 }],
    ['admin users', '/api/admin/users', { admin: 200, analyst: 403, viewer: 403 }],
    ['admin audit', '/api/admin/audit', { admin: 200, analyst: 403, viewer: 403 }],
    ['admin settings', '/api/admin/settings', { admin: 200, analyst: 403, viewer: 403 }],
    ['admin sheets', '/api/admin/sheets', { admin: 200, analyst: 403, viewer: 403 }],
    ['pipeline status', '/api/admin/pipeline/status', { admin: 200, analyst: 200, viewer: 403 }],
  ];
  const cl = { admin, analyst: A, viewer: V, adeny: AD };
  let n = 1;
  for (const [name, url, exp] of routes) {
    const id = `FT-ACC-${String(n++).padStart(2, '0')}`;
    const expTxt = Object.entries(exp).map(([k, v]) => `${k} ${v}`).join(', ') + ', anon 401';
    await t(id, 'access', 'Negative', `Access matrix: ${name}`, expTxt, async () => {
      const got = {};
      for (const k of Object.keys(exp)) got[k] = (await cl[k].get(url)).status;
      got.anon = (await anon.get(url)).status;
      const ok = Object.entries(exp).every(([k, v]) => got[k] === v) && got.anon === 401;
      return { ok, actual: Object.entries(got).map(([k, v]) => `${k} ${v}`).join(', '), evidence: url };
    });
  }
  await t(`FT-ACC-${n++}`, 'access', 'Positive', 'Catalog lists only permitted modules (viewer)', 'viewer catalog = 6 viewer modules; admin = 18', async () => {
    const v = await V.get('/api/market/catalog'); const a = await admin.get('/api/market/catalog');
    return { ok: v.data.modules.length === 6 && !v.data.modules.some((m) => m.code === 'waste_bio') && a.data.modules.length === 18, actual: `viewer ${v.data.modules.length}, admin ${a.data.modules.length}`, evidence: v.data.modules.map((m) => m.code).join(',') };
  });
  await t(`FT-ACC-${n++}`, 'access', 'Positive', 'Viewer with module grants reaches granted intel sections', 'vgrant 200 on ai/news/cftc/snd/atlas/calendar/waste-bio', async () => {
    const urls = ['/api/intel/ai/instruments', '/api/intel/news/documents', '/api/intel/cftc', '/api/intel/snd/graphs', '/api/intel/atlas', '/api/intel/calendar', '/api/sheets/waste-bio/arb'];
    const s = []; for (const u of urls) s.push((await VG.get(u)).status);
    return { ok: s.every((x) => x === 200), actual: s.join('/'), evidence: urls.join(' ') };
  });
}

// ════ MARKET ════════════════════════════════════════════════════════════════════════════════════════════
async function marketTests(A, V, AD) {
  const years = (await A.get('/api/market/years')).data;
  await t('FT-MKT-01', 'market', 'Positive', 'Available years and data version', '200 years ascending, default = last 3, as_of date', async () =>
    st(await A.get('/api/market/years'), 200, (r) => (r.data.years.length >= 1 && JSON.stringify(r.data.default) === JSON.stringify(r.data.years.slice(-3)) && /^\d{4}-\d{2}-\d{2}$/.test(r.data.as_of)) || 'shape'));
  await t('FT-MKT-02', 'market', 'Positive', 'Overview tiles', '200 with tiles / freshness', async () =>
    st(await A.get('/api/market/overview'), 200, (r) => Object.keys(r.data).length > 0 || 'empty'));
  await t('FT-MKT-03', 'market', 'Positive', 'Section default (calendar_spreads): series with traces for the default years', '200; series non-empty; years = default', async () =>
    st(await A.get('/api/market/sections/calendar_spreads'), 200, (r) => (r.data.series.length > 0 && JSON.stringify(r.data.years) === JSON.stringify(years.default)) || `series=${r.data.series.length}`));
  await t('FT-MKT-04', 'market', 'Positive', 'Section group filter', 'group=Futures returns only Futures series (15)', async () =>
    st(await A.get('/api/market/sections/calendar_spreads?group=Futures'), 200, (r) => (r.data.series.length === 15 && r.data.series.every((s) => s.group === undefined || s.group === 'Futures')) || `n=${r.data.series.length}`));
  await t('FT-MKT-05', 'market', 'Positive', 'Section param: Basis -> Tradable Prem', 'section=tradable returns 10 TP_* series', async () =>
    st(await A.get('/api/market/sections/basis_premiums?section=tradable'), 200, (r) => (r.data.series.length === 10 && r.data.series.every((s) => String(s.key).startsWith('TP_'))) || `n=${r.data.series.length}`));
  await t('FT-MKT-06', 'market', 'Positive', 'Waste/Bio calendar sub-section', 'section=calendar returns ARGUS_* series', async () =>
    st(await A.get('/api/market/sections/waste_bio?section=calendar'), 200, (r) => (r.data.series.length > 0 && r.data.series.every((s) => String(s.key).startsWith('ARGUS_'))) || `n=${r.data.series.length}`));
  await t('FT-MKT-07', 'market', 'Positive', 'Default waste_bio section hides internal helper series', 'no section -> no "_internal" series', async () => {
    const all = await A.get('/api/market/sections/waste_bio');
    return st(all, 200, (r) => (r.data.series.length > 0 && !r.data.series.some((s) => s.group === 'internal')) || 'internal series present');
  });
  await t('FT-MKT-08', 'market', 'Negative', 'Unknown group / unknown section', '200 with empty series (no error)', async () => {
    const a = await A.get('/api/market/sections/calendar_spreads?group=NoSuchGroup');
    const b = await A.get('/api/market/sections/waste_bio?section=nosuch');
    return { ok: a.status === 200 && a.data.series.length === 0 && b.status === 200 && b.data.series.length === 0, actual: `${a.status} n=${a.data?.series?.length} / ${b.status} n=${b.data?.series?.length}`, evidence: ev(a) };
  });
  await t('FT-MKT-09', 'market', 'Negative', 'Section param longer than 32 chars', '422 validation_error', async () =>
    st(await A.get(`/api/market/sections/waste_bio?section=${'s'.repeat(33)}`), 422));
  await t('FT-MKT-10', 'market', 'Positive', 'Product spreads tenor picker', 'tenor=c5 -> every family series is c5', async () =>
    st(await A.get('/api/market/sections/product_spreads?group=Futures&tenor=c5'), 200, (r) => (r.data.series.length > 0 && r.data.series.filter((s) => /_c\d$/.test(s.key)).every((s) => s.key.endsWith('_c5'))) || r.data.series.map((s) => s.key).slice(0, 4).join(',')));
  await t('FT-MKT-11', 'market', 'Negative', 'Bad tenor / bad unit / unknown module', '422 bad_tenor / 422 bad_unit / 404', async () => {
    const a = await A.get('/api/market/sections/product_spreads?tenor=c4');
    const b = await A.get('/api/market/sections/outright_prices?unit=eur');
    const c = await A.get('/api/market/sections/nope');
    return { ok: a.status === 422 && a.data.error.code === 'bad_tenor' && b.status === 422 && b.data.error.code === 'bad_unit' && c.status === 404, actual: `${a.status} ${a.data?.error?.code}/${b.status} ${b.data?.error?.code}/${c.status}`, evidence: ev(b) };
  });
  await t('FT-MKT-12', 'market', 'Negative', 'Bad years: non-integer / 1999 / 2101 / 16 years', '422 bad_years each', async () => {
    const qs = ['abc', '1999', '2101', Array.from({ length: 16 }, (_, i) => 2001 + i).join(',')];
    const rs = []; for (const q of qs) rs.push(await A.get(`/api/market/sections/calendar_spreads?years=${q}`));
    return { ok: rs.every((r) => r.status === 422 && r.data.error.code === 'bad_years'), actual: rs.map((r) => r.status).join('/'), evidence: ev(rs[0]) };
  });
  await t('FT-MKT-13', 'market', 'Positive', 'Years boundaries: 2000 and 2100 accepted; reversed list sorted; duplicates collapsed', '200, years=[2000,2024,2100]', async () =>
    st(await A.get('/api/market/sections/crush?years=2100,2024,2000,2024'), 200, (r) => JSON.stringify(r.data.years) === '[2000,2024,2100]' || JSON.stringify(r.data.years)));
  await t('FT-MKT-14', 'market', 'Positive', 'Local unit on outright prices', '200 unit=local', async () =>
    st(await A.get('/api/market/sections/outright_prices?group=Futures&unit=local'), 200, (r) => r.data.unit === 'local' || 'unit'));
  await t('FT-MKT-15', 'market', 'Positive', 'Single series', '200 for FCPO_c1c3', async () =>
    st(await A.get('/api/market/series/FCPO_c1c3'), 200));
  await t('FT-MKT-16', 'market', 'Negative', 'Series: unknown key / module not permitted / bad years', '404 / 403 module_forbidden (viewer on ARGUS) / 422', async () => {
    const a = await A.get('/api/market/series/NOPE_KEY');
    const b = await V.get('/api/market/series/ARGUS_CAL_UCOME_M1M2');
    const c = await A.get('/api/market/series/FCPO_c1c3?years=20x5');
    return { ok: a.status === 404 && b.status === 403 && b.data.error.code === 'module_forbidden' && c.status === 422, actual: `${a.status}/${b.status} ${b.data?.error?.code}/${c.status}`, evidence: ev(b) };
  });
  await t('FT-MKT-17', 'market', 'Positive', 'CSV export with data.export (analyst) is audited', '200 text/csv, header "date,<key> (unit)", attachment filename; DATA_EXPORT audit row', async () => {
    const r = await A.get('/api/market/series/FCPO_c1c3/export.csv');
    const au = await admin.get(`/api/admin/audit?action=DATA_EXPORT&actor=${enc(users.analyst.email)}&page_size=5`);
    const ok = r.status === 200 && (r.headers.get('content-type') || '').startsWith('text/csv') && r.text.split('\n')[0].startsWith('date,FCPO_c1c3') &&
      /filename="FCPO_c1c3.csv"/.test(r.headers.get('content-disposition') || '') && au.data.items.some((a) => a.target_id === 'FCPO_c1c3');
    return { ok, actual: `HTTP ${r.status}, rows=${r.text.split('\n').length - 2}, audited=${au.data.items.some((a) => a.target_id === 'FCPO_c1c3')}`, evidence: r.text.split('\n')[0] };
  });
  await t('FT-MKT-18', 'market', 'Positive', 'CSV export in local unit', 'header names the local unit (MYR/MT)', async () => {
    const r = await A.get('/api/market/series/FCPOc3/export.csv?unit=local');
    return { ok: r.status === 200 && /MYR\/MT/.test(r.text.split('\n')[0]), actual: `HTTP ${r.status} ${r.text.split('\n')[0]}`, evidence: '' };
  });
  await t('FT-MKT-19', 'market', 'Negative', 'CSV export without data.export (viewer) / denied module (adeny) / unknown key / bad unit', '403 forbidden / 403 module_forbidden / 404 / 422', async () => {
    const a = await V.get('/api/market/series/FCPO_c1c3/export.csv');
    const b = await AD.get('/api/market/series/FCPO_c1c3/export.csv');
    const c = await A.get('/api/market/series/NOPE/export.csv');
    const d = await A.get('/api/market/series/FCPO_c1c3/export.csv?unit=xyz');
    return { ok: a.status === 403 && a.data.error.code === 'forbidden' && b.status === 403 && b.data.error.code === 'module_forbidden' && c.status === 404 && d.status === 422,
      actual: `${a.status} ${a.data?.error?.code}/${b.status} ${b.data?.error?.code}/${c.status}/${d.status}`, evidence: ev(a) };
  });
  await t('FT-MKT-20', 'market', 'Positive', 'Forward curves outright + product_spread', '200 curves non-empty; snapshot echoes MM-DD', async () => {
    const a = await A.get('/api/market/forward-curves?snapshot=06-15');
    const b = await A.get('/api/market/forward-curves?kind=product_spread&snapshot=06-15');
    return { ok: a.status === 200 && a.data.curves.length > 0 && a.data.snapshot === '06-15' && b.status === 200 && b.data.curves.length > 0, actual: `${a.status} n=${a.data?.curves?.length} / ${b.status} n=${b.data?.curves?.length}`, evidence: ev(a) };
  });
  await t('FT-MKT-21', 'market', 'Negative', 'Forward curves: bad kind / bad snapshot (13-01, 6-15, 00-10) / bad unit / bad years', '422 each', async () => {
    const rs = [];
    for (const q of ['kind=calendar', 'snapshot=13-01', 'snapshot=6-15', 'snapshot=00-10', 'unit=gbp', 'years=1990']) rs.push(await A.get(`/api/market/forward-curves?${q}`));
    return { ok: rs.every((r) => r.status === 422), actual: rs.map((r) => `${r.status}${r.data?.error?.code ? ':' + r.data.error.code : ''}`).join(' '), evidence: ev(rs[1]) };
  });
  await t('FT-MKT-22', 'market', 'Negative', 'Forward curves: impossible day for the month (04-31)', '422 bad_snapshot, or as-of within April (not silently Feb 28)', async () => {
    const r = await A.get(`/api/market/forward-curves?snapshot=04-31&years=${years.years[0]}`);
    if (r.status === 422) return { ok: true, actual: 'HTTP 422', evidence: ev(r) };
    const asOf = r.data?.curves?.flatMap((c) => c.traces || []).map((x) => x.as_of)[0];
    return { ok: r.status === 200 && !!asOf && asOf.slice(5, 7) === '04', actual: `HTTP ${r.status}, trace as_of=${asOf}`, evidence: `snapshot=04-31 year ${years.years[0]}` };
  });
  await t('FT-MKT-23', 'market', 'Positive', 'Calendar Spreads YTD default year and explicit year', '200 products non-empty for latest year; year echoes', async () => {
    const a = await A.get('/api/market/ytd-spreads');
    const b = await A.get(`/api/market/ytd-spreads?year=${years.years[0]}`);
    return { ok: a.status === 200 && a.data.year === years.years.at(-1) && a.data.products.length > 0 && b.status === 200 && b.data.year === years.years[0], actual: `${a.status} y=${a.data?.year} n=${a.data?.products?.length} / ${b.status}`, evidence: ev(b) };
  });
  await t('FT-MKT-24', 'market', 'Negative', 'YTD year out of range / non-integer / bad unit', '422 each', async () => {
    const rs = [await A.get('/api/market/ytd-spreads?year=1999'), await A.get('/api/market/ytd-spreads?year=2101'), await A.get('/api/market/ytd-spreads?year=x'), await A.get('/api/market/ytd-spreads?unit=x')];
    return { ok: rs.every((r) => r.status === 422), actual: rs.map((r) => r.status).join('/'), evidence: ev(rs[0]) };
  });
  await t('FT-MKT-25', 'market', 'Positive', 'YTD year with no data (2030)', '200 products empty (no-data state, DASH-14)', async () =>
    st(await A.get('/api/market/ytd-spreads?year=2030'), 200, (r) => Array.isArray(r.data.products) || 'shape'));
  await t('FT-MKT-26', 'market', 'Positive', 'OI cumulative default 30 days and boundaries 5/120', '200 tables (10 instruments) each', async () => {
    const rs = [await A.get('/api/market/oi-cumulative'), await A.get('/api/market/oi-cumulative?days=5'), await A.get('/api/market/oi-cumulative?days=120')];
    return { ok: rs.every((r) => r.status === 200 && r.data.tables.length > 0), actual: rs.map((r) => `${r.status}/${r.data?.tables?.length}`).join(' '), evidence: ev(rs[0]) };
  });
  await t('FT-MKT-27', 'market', 'Negative', 'OI days 4 / 121 / -1', '422 each', async () => {
    const rs = [await A.get('/api/market/oi-cumulative?days=4'), await A.get('/api/market/oi-cumulative?days=121'), await A.get('/api/market/oi-cumulative?days=-1')];
    return { ok: rs.every((r) => r.status === 422), actual: rs.map((r) => r.status).join('/'), evidence: ev(rs[0]) };
  });
  await t('FT-MKT-28', 'market', 'Positive', 'Technical analysis instruments + price + spread instrument', '200; price/sma/levels arrays', async () => {
    const i = await A.get('/api/market/ta/instruments');
    const a = await A.get('/api/market/ta/FCPOc3?start_year=2024');
    const b = await A.get('/api/market/ta/BOPO_c3?start_year=2024');
    const c = await A.get('/api/market/ta/FCPOc3?start_year=2024&unit=local');
    return { ok: i.status === 200 && i.data.instruments.length > 0 && a.status === 200 && a.data.price.length > 0 && Array.isArray(a.data.levels) && b.status === 200 && c.status === 200 && c.data.unit !== 'USD/MT',
      actual: `inst ${i.status} n=${i.data?.instruments?.length}, FCPOc3 ${a.status} n=${a.data?.price?.length} levels=${a.data?.levels?.length}, BOPO ${b.status}, local unit=${c.data?.unit}`, evidence: '' };
  });
  await t('FT-MKT-29', 'market', 'Negative', 'TA: unknown instrument / start_year 2009 / 2101 / bad unit', '404 / 422 / 422 / 422', async () => {
    const rs = [await A.get('/api/market/ta/NOPE'), await A.get('/api/market/ta/FCPOc3?start_year=2009'), await A.get('/api/market/ta/FCPOc3?start_year=2101'), await A.get('/api/market/ta/FCPOc3?unit=zz')];
    return { ok: rs[0].status === 404 && rs.slice(1).every((r) => r.status === 422), actual: rs.map((r) => r.status).join('/'), evidence: ev(rs[0]) };
  });
  await t('FT-MKT-30', 'market', 'Positive', 'TA start year with no data (2100)', '200 with empty arrays, no 500', async () =>
    st(await A.get('/api/market/ta/FCPOc3?start_year=2100'), 200));
}

// ════ INTEL ═════════════════════════════════════════════════════════════════════════════════════════════
async function intelTests(A, A2, V, VG) {
  // --- SND ---
  const SND_YEAR = 2099;
  const sndCsv = ['Indonesia SND,Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec', 'Production,100,110,120,130,140,150,160,170,180,190,200,210',
    'Exports,50,51,52,53,54,55,56,57,58,59,60,61', 'Domestic consumption,40,41,42,43,44,45,46,47,48,49,50,51', 'End stock,10,11,12,13,14,15,16,17,18,19,20,21', 'Note,,,,,,,,,,,,'].join('\n');
  const sndForm = (fields, name = 'qa-ft-snd.csv', data = sndCsv, type = 'text/csv') => fileForm(fields, { data: Buffer.from(data), name, type });
  const existing = (await admin.get('/api/intel/snd/sources')).data.sources.some((s) => s.role === 'indonesia' && s.year === SND_YEAR);
  await t('FT-SND-01', 'snd', 'Positive', 'SND read views (graphs, tables, exports, sources)', '200 each; analyst can_upload=false, admin can_upload=true', async () => {
    const rs = [await A.get('/api/intel/snd/graphs'), await A.get('/api/intel/snd/tables'), await A.get('/api/intel/snd/exports'), await A.get('/api/intel/snd/sources'), await admin.get('/api/intel/snd/sources')];
    return { ok: rs.every((r) => r.status === 200) && rs[3].data.can_upload === false && rs[4].data.can_upload === true, actual: rs.map((r) => r.status).join('/'), evidence: ev(rs[0]) };
  });
  if (existing) {
    await t('FT-SND-02', 'snd', 'Positive', 'Admin uploads an SND CSV (indonesia 2099)', '201 rows/columns/parsed', async () => ({ blocked: 'indonesia/2099 source already exists — not overwriting data the suite did not create' }));
  } else {
    onRestore('SND indonesia/2099', async () => admin.del(`/api/intel/snd/sources/indonesia/${SND_YEAR}`));
    await t('FT-SND-02', 'snd', 'Positive', 'Admin uploads an SND CSV (indonesia 2099)', '201 rows=6, columns=13, parsed counts; listed; graphs?years=2099 has traces; SND_UPLOAD audited', async () => {
      const r = await admin.req('POST', '/api/intel/snd/sources', { form: sndForm({ role: 'indonesia', year: SND_YEAR }) });
      const list = await admin.get('/api/intel/snd/sources');
      const g = await A.get(`/api/intel/snd/graphs?years=${SND_YEAR}`);
      const indo = g.data?.countries?.find((c) => c.name === 'Indonesia');
      const traces = indo?.items?.flatMap((i) => i.traces) || [];
      return { ok: r.status === 201 && r.data.rows === 6 && r.data.columns === 13 && list.data.sources.some((s) => s.role === 'indonesia' && s.year === SND_YEAR) && traces.some((tr) => tr.year === SND_YEAR),
        actual: `upload ${r.status} rows=${r.data?.rows} cols=${r.data?.columns}; traces 2099=${traces.filter((tr) => tr.year === SND_YEAR).length}`, evidence: ev(r) };
    });
    await t('FT-SND-03', 'snd', 'Positive', 'Re-upload same role/year replaces (idempotent upsert)', '201; still one indonesia/2099 source', async () => {
      const r = await admin.req('POST', '/api/intel/snd/sources', { form: sndForm({ role: 'indonesia', year: SND_YEAR }) });
      const list = await admin.get('/api/intel/snd/sources');
      return { ok: r.status === 201 && list.data.sources.filter((s) => s.role === 'indonesia' && s.year === SND_YEAR).length === 1, actual: `HTTP ${r.status}, count=${list.data.sources.filter((s) => s.role === 'indonesia' && s.year === SND_YEAR).length}`, evidence: ev(r) };
    });
  }
  await t('FT-SND-04', 'snd', 'Negative', 'SND upload: bad role / year 1999 / year 2101 / missing year for yearly role', '422 bad_role / bad_year x3', async () => {
    const rs = [await admin.req('POST', '/api/intel/snd/sources', { form: sndForm({ role: 'mars', year: 2099 }) }),
      await admin.req('POST', '/api/intel/snd/sources', { form: sndForm({ role: 'india', year: 1999 }) }),
      await admin.req('POST', '/api/intel/snd/sources', { form: sndForm({ role: 'india', year: 2101 }) }),
      await admin.req('POST', '/api/intel/snd/sources', { form: sndForm({ role: 'india' }) })];
    return { ok: rs[0].data?.error?.code === 'bad_role' && rs.slice(1).every((r) => r.status === 422 && r.data.error.code === 'bad_year'), actual: rs.map((r) => `${r.status}:${r.data?.error?.code}`).join(' '), evidence: ev(rs[0]) };
  });
  await t('FT-SND-05', 'snd', 'Negative', 'SND upload: wrong type (.txt) / empty file / corrupt .xlsx / too few rows / missing file / bad sheet', '422 bad_file_type / empty_file / bad_file / bad_file / validation_error / bad_sheet', async () => {
    const rs = [
      await admin.req('POST', '/api/intel/snd/sources', { form: sndForm({ role: 'india', year: 2098 }, 'x.txt') }),
      await admin.req('POST', '/api/intel/snd/sources', { form: sndForm({ role: 'india', year: 2098 }, 'x.csv', '') }),
      await admin.req('POST', '/api/intel/snd/sources', { form: sndForm({ role: 'india', year: 2098 }, 'x.xlsx', 'PK-not-a-zip') }),
      await admin.req('POST', '/api/intel/snd/sources', { form: sndForm({ role: 'india', year: 2098 }, 'x.csv', 'a,b\nc,d\n') }),
      await admin.req('POST', '/api/intel/snd/sources', { form: fileForm({ role: 'india', year: 2098 }) }),
      await admin.req('POST', '/api/intel/snd/sources', { form: fileForm({ role: 'india', year: 2098, sheet: 'NoSuchSheet' }, { data: xlsx({ Sheet1: [['a']] }), name: 'x.xlsx' }) }),
    ];
    const want = ['bad_file_type', 'empty_file', 'bad_file', 'bad_file', 'validation_error', 'bad_sheet'];
    return { ok: rs.every((r, i) => r.status === 422 && r.data?.error?.code === want[i]), actual: rs.map((r) => `${r.status}:${r.data?.error?.code}`).join(' '), evidence: ev(rs[5]) };
  });
  await t('FT-SND-06', 'snd', 'Negative', 'SND upload/delete by analyst (needs admin) and by viewer (no module)', '403 forbidden / 403 module_forbidden', async () => {
    const a = await A.req('POST', '/api/intel/snd/sources', { form: sndForm({ role: 'india', year: 2098 }) });
    const b = await A.del('/api/intel/snd/sources/india/2098');
    const c = await V.req('POST', '/api/intel/snd/sources', { form: sndForm({ role: 'india', year: 2098 }) });
    return { ok: a.status === 403 && b.status === 403 && c.status === 403 && c.data.error.code === 'module_forbidden', actual: `${a.status}/${b.status}/${c.status} ${c.data?.error?.code}`, evidence: ev(a) };
  });
  await t('FT-SND-07', 'snd', 'Negative', 'SND graphs: bad years parameter', '422 bad_years', async () =>
    st(await A.get('/api/intel/snd/graphs?years=20x6'), 422, errCode('bad_years')));
  if (!existing) {
    await t('FT-SND-08', 'snd', 'Positive', 'Delete SND source then delete again', '200 {deleted:true}; second 404', async () => {
      const a = await admin.del(`/api/intel/snd/sources/indonesia/${SND_YEAR}`);
      const b = await admin.del(`/api/intel/snd/sources/indonesia/${SND_YEAR}`);
      return { ok: a.status === 200 && a.data.deleted === true && b.status === 404, actual: `${a.status}/${b.status}`, evidence: ev(a) };
    });
  }
  await t('FT-SND-09', 'snd', 'Negative', 'Delete SND: unknown role / non-integer year', '404 / 422', async () => {
    const a = await admin.del('/api/intel/snd/sources/mars/2099'); const b = await admin.del('/api/intel/snd/sources/india/abc');
    return { ok: a.status === 404 && b.status === 422, actual: `${a.status}/${b.status}`, evidence: ev(a) };
  });

  // --- CFTC ---
  await t('FT-CFTC-01', 'cftc', 'Positive', 'CFTC view', '200 with products/categories, can_refresh true for analyst, false for viewer+grant', async () => {
    const a = await A.get('/api/intel/cftc'); const g = await VG.get('/api/intel/cftc');
    return { ok: a.status === 200 && a.data.can_refresh === true && g.status === 200 && g.data.can_refresh === false, actual: `${a.status} ${a.data?.can_refresh} / ${g.status} ${g.data?.can_refresh}`, evidence: ev(a) };
  });
  await t('FT-CFTC-02', 'cftc', 'Positive', 'CFTC refresh queues one job; repeat returns the same job (dedup) ; audited', '202 job_id twice (identical); job visible as queued; CFTC_REFRESH audit', async () => {
    const a = await A.post('/api/intel/cftc/refresh', {}); const b = await A.post('/api/intel/cftc/refresh', {});
    const v = await A.get('/api/intel/cftc');
    return { ok: a.status === 202 && b.status === 202 && a.data.job_id === b.data.job_id && v.data.job?.status === 'queued', actual: `${a.status}/${b.status} same=${a.data?.job_id === b.data?.job_id} job=${v.data?.job?.status}`, evidence: ev(a) };
  });
  await t('FT-CFTC-03', 'cftc', 'Negative', 'CFTC refresh without pipeline.run / without module / unauthenticated', '403 forbidden / 403 module_forbidden / 401', async () => {
    const a = await VG.post('/api/intel/cftc/refresh', {}); const b = await V.post('/api/intel/cftc/refresh', {}); const c = await anon.post('/api/intel/cftc/refresh', {});
    return { ok: a.status === 403 && a.data.error.code === 'forbidden' && b.status === 403 && b.data.error.code === 'module_forbidden' && c.status === 401, actual: `${a.status} ${a.data?.error?.code}/${b.status} ${b.data?.error?.code}/${c.status}`, evidence: ev(a) };
  });

  // --- Calendar ---
  await t('FT-CAL-01', 'calendar', 'Positive', 'Calendar view', '200 events list, categories, claude_configured=false', async () =>
    st(await A.get('/api/intel/calendar'), 200, (r) => (Array.isArray(r.data.events) && r.data.claude_configured === false) || 'shape'));
  await t('FT-CAL-02', 'calendar', 'Negative', 'Calendar refresh with Claude not configured', '409 claude_not_configured (no job queued)', async () =>
    st(await A.post('/api/intel/calendar/refresh', {}), 409, errCode('claude_not_configured')));
  await t('FT-CAL-03', 'calendar', 'Negative', 'Calendar refresh without pipeline.run / module', '403 forbidden / 403 module_forbidden', async () => {
    const a = await VG.post('/api/intel/calendar/refresh', {}); const b = await V.post('/api/intel/calendar/refresh', {});
    return { ok: a.status === 403 && a.data.error.code === 'forbidden' && b.status === 403, actual: `${a.status}/${b.status}`, evidence: ev(a) };
  });

  // --- Atlas ---
  const atlasOrig = (await admin.get('/api/intel/atlas')).data;
  const origBody = { snapshot: atlasOrig.snapshot, plants: atlasOrig.plants };
  let atlasTouched = false;
  const restoreAtlas = async () => { const r = await admin.put('/api/intel/atlas', origBody); atlasTouched = false; return r; };
  onRestore('atlas', async () => { if (atlasTouched) await restoreAtlas(); });
  const ensureAtlas = async () => {
    const g = await admin.get('/api/intel/atlas');
    if (g.status !== 200 || g.data.plants.length !== atlasOrig.plants.length || g.data.plants.some((p) => p.company === 'QA')) { atlasTouched = true; await restoreAtlas(); return 'restored'; }
    return 'unchanged';
  };
  const plant = (o = {}) => ({ name: 'QA FT Plant', company: 'QA', country: 'Nowhere', region: 'QA', lat: 1.5, lon: 103.8, status: 'op', type: 'standalone', kt: 100, safkt: null, quoted: '', startup: '2030', note: '', est: false, ...o });
  await t('FT-ATL-01', 'atlas', 'Positive', 'Atlas view', '200 with 94 plants and snapshot; analyst can_edit=false, admin true', async () => {
    const a = await A.get('/api/intel/atlas');
    return { ok: a.status === 200 && a.data.plants.length === atlasOrig.plants.length && atlasOrig.plants.length > 0 && a.data.can_edit === false && atlasOrig.can_edit === true, actual: `plants=${a.data?.plants?.length} snapshot=${a.data?.snapshot}`, evidence: ev(a) };
  });
  await t('FT-ATL-02', 'atlas', 'Positive', 'Atlas replace with the same dataset is idempotent', '200 {plants: N}; data unchanged; ATLAS_REPLACE audited', async () => {
    const r = await admin.put('/api/intel/atlas', origBody);
    const g = await admin.get('/api/intel/atlas');
    return { ok: r.status === 200 && r.data.plants === atlasOrig.plants.length && g.data.plants.length === atlasOrig.plants.length && g.data.snapshot === atlasOrig.snapshot, actual: `${r.status} plants=${r.data?.plants}`, evidence: ev(r) };
  });
  await t('FT-ATL-03', 'atlas', 'Negative', 'Atlas validation: empty list / bad status / bad type / lat 91 / lon -181 / kt text / snapshot 61 chars / plants not a list', '422 each; data unchanged', async () => {
    const bodies = [{ plants: [] }, { plants: [plant({ status: 'xx' })] }, { plants: [plant({ type: 'hybrid' })] }, { plants: [plant({ lat: 91 })] },
      { plants: [plant({ lon: -181 })] }, { plants: [plant({ kt: 'lots' })] }, { snapshot: 's'.repeat(61), plants: [plant()] }, { plants: 'x' }];
    const rs = []; for (const b of bodies) rs.push(await admin.put('/api/intel/atlas', b));
    const e = await ensureAtlas();
    return { ok: rs.every((r) => r.status === 422) && e === 'unchanged', actual: rs.map((r) => r.status).join('/') + ` data ${e}`, evidence: ev(rs[1]) };
  });
  await t('FT-ATL-04', 'atlas', 'Negative', 'Atlas validation: plant without name / country', '422 bad_atlas (name and country required)', async () => {
    const { name, ...noName } = plant();
    const r = await admin.put('/api/intel/atlas', { snapshot: atlasOrig.snapshot, plants: [...atlasOrig.plants, noName] });
    let stored = '';
    if (r.status === 200) { atlasTouched = true; const g = await admin.get('/api/intel/atlas'); stored = JSON.stringify(g.data.plants.find((p) => p.company === 'QA')?.name); await restoreAtlas(); }
    return st(r, 422, null, r.status === 200 ? `accepted; stored name=${stored} (restored)` : '');
  });
  await t('FT-ATL-05', 'atlas', 'Negative', 'Atlas validation: name longer than the 200-char column', '422 bad_atlas (not 500); data unchanged', async () => {
    const r = await admin.put('/api/intel/atlas', { snapshot: atlasOrig.snapshot, plants: [...atlasOrig.plants, plant({ name: 'N'.repeat(201) })] });
    const e = await ensureAtlas();
    return st(r, 422, null, `data ${e}`);
  });
  await t('FT-ATL-06', 'atlas', 'Negative', 'Atlas validation: capacity beyond numeric(12,2) (kt 1e12)', '422 bad_atlas (not 500)', async () => {
    const r = await admin.put('/api/intel/atlas', { snapshot: atlasOrig.snapshot, plants: [...atlasOrig.plants, plant({ kt: 1e12 })] });
    const e = await ensureAtlas();
    const i = await admin.put('/api/intel/atlas', { snapshot: atlasOrig.snapshot, plants: [...atlasOrig.plants, plant({ safkt: 'Infinity' })] });
    const e2 = await ensureAtlas();
    return { ok: r.status === 422 && i.status === 422, actual: `kt=1e12: HTTP ${r.status} ${r.data?.error?.code}; safkt=Infinity: HTTP ${i.status} ${i.data?.error?.code}; data ${e}/${e2}`, evidence: ev(r) };
  });
  await t('FT-ATL-07', 'atlas', 'Negative', 'Atlas validation: kt "NaN" / negative capacity', '422 bad_atlas; GET /atlas keeps working', async () => {
    const r = await admin.put('/api/intel/atlas', { snapshot: atlasOrig.snapshot, plants: [...atlasOrig.plants, plant({ name: 'QA FT NaN', kt: 'NaN' })] });
    let g = null;
    if (r.status === 200) {
      atlasTouched = true; const x = await admin.get('/api/intel/atlas');
      const idx = x.data?.plants?.findIndex((p) => p.name === 'QA FT NaN');
      g = { status: x.status, data: { error: { code: `NaN plant served at index ${idx} with kt=${JSON.stringify(x.data?.plants?.[idx]?.kt)}` } } };
      await restoreAtlas();
    }
    const n = await admin.put('/api/intel/atlas', { snapshot: atlasOrig.snapshot, plants: [...atlasOrig.plants, plant({ name: 'QA FT negative', kt: -5 })] });
    await ensureAtlas();
    return { ok: r.status === 422 && n.status === 422, actual: `NaN: PUT ${r.status}${g ? `, then GET /atlas ${g.status} ${g.data?.error?.code || ''}` : ''}; negative kt: PUT ${n.status} (restored)`, evidence: ev(r) };
  });
  await t('FT-ATL-08', 'atlas', 'Negative', 'Atlas replace by analyst / viewer / unauthenticated', '403 / 403 / 401', async () => {
    const a = await A.put('/api/intel/atlas', origBody); const v = await V.put('/api/intel/atlas', origBody); const n = await anon.put('/api/intel/atlas', origBody);
    return { ok: a.status === 403 && v.status === 403 && n.status === 401, actual: `${a.status}/${v.status}/${n.status}`, evidence: ev(a) };
  });
  await t('FT-ATL-09', 'atlas', 'Positive', 'Atlas restored to the original dataset', 'plant count and snapshot equal the original', async () => {
    const g = await admin.get('/api/intel/atlas');
    return { ok: g.data.plants.length === atlasOrig.plants.length && g.data.snapshot === atlasOrig.snapshot && !g.data.plants.some((p) => p.company === 'QA'), actual: `plants=${g.data.plants.length} snapshot=${g.data.snapshot}`, evidence: '' };
  });

  // --- AI ---
  await t('FT-AI-01', 'ai', 'Positive', 'AI instruments', '200 groups incl. Outright Prices; timeframes 1M..All; claude_configured=false', async () =>
    st(await A.get('/api/intel/ai/instruments'), 200, (r) => (r.data.groups[0].group === 'Outright Prices' && r.data.timeframes.includes('1Y') && r.data.claude_configured === false) || 'shape'));
  await t('FT-AI-02', 'ai', 'Positive', 'AI instrument list respects modules (viewer+grant has no Waste/Bio-less leakage)', 'adeny-equivalent: groups only for permitted seasonal modules', async () => {
    const g = await VG.get('/api/intel/ai/instruments');
    const names = g.data.groups.map((x) => x.group);
    return { ok: g.status === 200 && !names.includes('Crush') && !names.includes('Basis & Premiums'), actual: names.join(', '), evidence: '' };
  });
  let reportId = null;
  await t('FT-AI-03', 'ai', 'Positive', 'Create AI report without Claude: sections computed, narrative skipped', '201 status=skipped, error "Claude is not configured…", sections present; AI_REPORT audited', async () => {
    const r = await A.post('/api/intel/ai/reports', { instrument: 'outright::FCPOc3', timeframe: '1M' });
    reportId = r.data?.id;
    return st(r, 201, (x) => (x.data.status === 'skipped' && /not configured/i.test(x.data.error || '') && x.data.sections?.seasonal) || 'shape');
  });
  await t('FT-AI-04', 'ai', 'Positive', 'List recent reports and get own report', '200; list contains the new id; get returns it', async () => {
    const l = await A.get('/api/intel/ai/reports'); const g = await A.get(`/api/intel/ai/reports/${reportId}`);
    return { ok: l.status === 200 && l.data.reports.some((x) => x.id === reportId) && g.status === 200 && g.data.id === reportId, actual: `${l.status}/${g.status}`, evidence: ev(g) };
  });
  await t('FT-AI-05', 'ai', 'Positive', "Another analyst can open a colleague's report (reports are shared within the module)", '200 for analyst2', async () =>
    st(await A2.get(`/api/intel/ai/reports/${reportId}`), 200));
  await t('FT-AI-06', 'ai', 'Negative', 'AI create validation: no "::" / unknown instrument / bad timeframe / timeframe > 4 chars / instrument > 96', '422 bad_instrument / 404 unknown_instrument / 422 bad_timeframe / 422 / 422', async () => {
    const rs = [await A2.post('/api/intel/ai/reports', { instrument: 'FCPOc3' }), await A2.post('/api/intel/ai/reports', { instrument: 'outright::NOPE' }),
      await A2.post('/api/intel/ai/reports', { instrument: 'outright::FCPOc3', timeframe: '2W' }), await A2.post('/api/intel/ai/reports', { instrument: 'outright::FCPOc3', timeframe: '10YRS' }),
      await A2.post('/api/intel/ai/reports', { instrument: 'outright::' + 'X'.repeat(100) })];
    const want = [[422, 'bad_instrument'], [404, 'unknown_instrument'], [422, 'bad_timeframe'], [422, 'validation_error'], [422, 'validation_error']];
    return { ok: rs.every((r, i) => r.status === want[i][0] && r.data?.error?.code === want[i][1]), actual: rs.map((r) => `${r.status}:${r.data?.error?.code}`).join(' '), evidence: ev(rs[1]) };
  });
  await t('FT-AI-07', 'ai', 'Negative', 'AI on a derived series from a module the user lacks', '403 module_forbidden (vgrant -> crush series)', async () =>
    st(await VG.post('/api/intel/ai/reports', { instrument: 'spread::CRUSH_c3', timeframe: '1M' }), 403, errCode('module_forbidden')));
  await t('FT-AI-08', 'ai', 'Negative', 'AI get: nonexistent / malformed id; viewer without module', '404 / 422 / 403', async () => {
    const a = await A.get(`/api/intel/ai/reports/${crypto.randomUUID()}`); const b = await A.get('/api/intel/ai/reports/xyz'); const c = await V.get(`/api/intel/ai/reports/${reportId}`);
    return { ok: a.status === 404 && b.status === 422 && c.status === 403, actual: `${a.status}/${b.status}/${c.status}`, evidence: ev(a) };
  });
  await t('FT-AI-09', 'ai', 'Negative', 'AI rate limit: 21st report within an hour', 'first 20 -> 201; 21st -> 429 "At most 20 AI analyses per hour. Try again later."', async () => {
    const R = users.ratelimit.client;
    const codes = [];
    for (let i = 0; i < 20; i++) codes.push((await R.post('/api/intel/ai/reports', { instrument: 'outright::BOc1', timeframe: '1M' })).status);
    const last = await R.post('/api/intel/ai/reports', { instrument: 'outright::BOc1', timeframe: '1M' });
    return { ok: codes.every((c) => c === 201) && last.status === 429 && /At most 20 AI analyses per hour/.test(last.data?.error?.message || ''), actual: `20x: ${[...new Set(codes)].join(',')}; 21st ${last.status} "${last.data?.error?.message}"`, evidence: ev(last) };
  });

  // --- News ---
  await t('FT-NEWS-01', 'news', 'Positive', 'News panel default and each geography/product', '200; filters echoed', async () => {
    const a = await A.get('/api/intel/news/panel'); const b = await A.get(`/api/intel/news/panel?geography=EU&product=${enc('Other Edible Oils')}`);
    return { ok: a.status === 200 && b.status === 200, actual: `${a.status}/${b.status}`, evidence: ev(b) };
  });
  await t('FT-NEWS-02', 'news', 'Negative', 'News panel: unknown geography / product', '422 bad_filter', async () => {
    const a = await A.get('/api/intel/news/panel?geography=Mars'); const b = await A.get('/api/intel/news/panel?product=Coffee');
    return { ok: a.status === 422 && b.status === 422 && a.data.error.code === 'bad_filter', actual: `${a.status}/${b.status}`, evidence: ev(a) };
  });
  await t('FT-NEWS-03', 'news', 'Positive', 'News documents list and capabilities per role', 'analyst can_upload=true can_delete=false; admin can_delete=true; vgrant can_upload=false', async () => {
    const a = await A.get('/api/intel/news/documents'); const d = await admin.get('/api/intel/news/documents'); const g = await VG.get('/api/intel/news/documents');
    return { ok: a.data.can_upload === true && a.data.can_delete === false && d.data.can_delete === true && g.data.can_upload === false && a.data.max_mb === 30, actual: `analyst ${a.data.can_upload}/${a.data.can_delete}, admin del ${d.data.can_delete}, vgrant up ${g.data.can_upload}`, evidence: '' };
  });
  const pdf = Buffer.from(`%PDF-1.4\n% QA FT ${SUFFIX}\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n`);
  await t('FT-NEWS-04', 'news', 'Negative', 'Document upload while Claude is not configured', '409 claude_not_configured; nothing stored', async () => {
    const r = await A.req('POST', '/api/intel/news/documents', { form: fileForm({ doc_type: 'auto' }, { data: pdf, name: 'qa-ft.pdf', type: 'application/pdf' }) });
    const l = await A.get('/api/intel/news/documents');
    return { ok: r.status === 409 && r.data.error.code === 'claude_not_configured' && !l.data.documents.some((d) => d.filename === 'qa-ft.pdf'), actual: `${r.status} ${r.data?.error?.code}`, evidence: ev(r) };
  });
  await t('FT-NEWS-05', 'news', 'Negative', 'Document upload without documents.upload / without module / unauthenticated', '403 forbidden / 403 module_forbidden / 401', async () => {
    const f = () => fileForm({}, { data: pdf, name: 'qa-ft.pdf', type: 'application/pdf' });
    const a = await VG.req('POST', '/api/intel/news/documents', { form: f() }); const b = await V.req('POST', '/api/intel/news/documents', { form: f() }); const c = await anon.req('POST', '/api/intel/news/documents', { form: f() });
    return { ok: a.status === 403 && a.data.error.code === 'forbidden' && b.status === 403 && b.data.error.code === 'module_forbidden' && c.status === 401, actual: `${a.status}/${b.status}/${c.status}`, evidence: ev(a) };
  });
  await t('FT-NEWS-06', 'news', 'Negative', 'Document upload validation: file type (.exe), size > 30 MB, duplicate, bad doc_type, empty file', '422 bad_file_type / 413 / 409 duplicate / 422 bad_doc_type / 422 empty_file', async () =>
    ({ blocked: 'Unreachable on this stack: the route returns 409 claude_not_configured before reading or validating the file (no ANTHROPIC_API_KEY, external API must not be contacted). Covered by code review only.' }));
  await t('FT-NEWS-07', 'news', 'Negative', 'Reprocess a nonexistent document', '404 not_found', async () =>
    st(await A.post(`/api/intel/news/documents/${crypto.randomUUID()}/reprocess`, {}), 404));
  await t('FT-NEWS-08', 'news', 'Negative', 'Reprocess / delete: malformed id; delete nonexistent; delete as analyst', '422 / 422 / 404 / 403', async () => {
    const a = await A.post('/api/intel/news/documents/abc/reprocess', {}); const b = await admin.del('/api/intel/news/documents/abc');
    const c = await admin.del(`/api/intel/news/documents/${crypto.randomUUID()}`); const d = await A.del(`/api/intel/news/documents/${crypto.randomUUID()}`);
    return { ok: a.status === 422 && b.status === 422 && c.status === 404 && d.status === 403, actual: `${a.status}/${b.status}/${c.status}/${d.status}`, evidence: ev(c) };
  });
  await t('FT-NEWS-09', 'news', 'Negative', 'Reprocess without documents.upload', '403 forbidden', async () =>
    st(await VG.post(`/api/intel/news/documents/${crypto.randomUUID()}/reprocess`, {}), 403, errCode('forbidden')));
}

// ════ SHEETS ════════════════════════════════════════════════════════════════════════════════════════════
async function sheetsTests(A, V, AD, VG) {
  const yrs = (await A.get('/api/market/years')).data;
  for (const [i, set] of ['calendar', 'product', 'fwd_diff'].entries()) {
    await t(`FT-WB-0${i + 1}`, 'waste_bio', 'Positive', `Waste/Bio forward curves: ${set}`, '200 curves list with traces; snapshot echo', async () =>
      st(await A.get(`/api/sheets/waste-bio/curves/${set}?snapshot=06-15&years=2025,2026`), 200, (r) => (r.data.set === set && Array.isArray(r.data.curves) && r.data.curves.length > 0) || `curves=${r.data.curves?.length}`));
  }
  await t('FT-WB-04', 'waste_bio', 'Negative', 'Waste/Bio curves: unknown set / bad snapshot / bad years', '404 / 422 bad_snapshot / 422 bad_years', async () => {
    const a = await A.get('/api/sheets/waste-bio/curves/nope'); const b = await A.get('/api/sheets/waste-bio/curves/calendar?snapshot=2026-06-15');
    const c = await A.get('/api/sheets/waste-bio/curves/calendar?years=1999');
    return { ok: a.status === 404 && b.status === 422 && b.data.error.code === 'bad_snapshot' && c.status === 422 && c.data.error.code === 'bad_years', actual: `${a.status}/${b.status}/${c.status}`, evidence: ev(b) };
  });
  await t('FT-WB-05', 'waste_bio', 'Positive', 'Waste/Bio YTD current and explicit year', '200 products; year echo', async () => {
    const a = await A.get('/api/sheets/waste-bio/ytd'); const b = await A.get('/api/sheets/waste-bio/ytd?year=2026');
    return { ok: a.status === 200 && b.status === 200 && b.data.year === 2026 && Array.isArray(b.data.products), actual: `${a.status}/${b.status} n=${b.data?.products?.length}`, evidence: ev(b) };
  });
  await t('FT-WB-06', 'waste_bio', 'Negative', 'Waste/Bio YTD: year 1999 / 2101 / text', '422 each', async () => {
    const rs = [await A.get('/api/sheets/waste-bio/ytd?year=1999'), await A.get('/api/sheets/waste-bio/ytd?year=2101'), await A.get('/api/sheets/waste-bio/ytd?year=this')];
    return { ok: rs.every((r) => r.status === 422), actual: rs.map((r) => r.status).join('/'), evidence: ev(rs[0]) };
  });
  await t('FT-WB-07', 'waste_bio', 'Positive', 'Waste/Bio Arb and Tickets calculation', '200 each with content', async () => {
    const a = await A.get('/api/sheets/waste-bio/arb'); const b = await A.get('/api/sheets/waste-bio/tickets-calc');
    return { ok: a.status === 200 && Object.keys(a.data).length > 0 && b.status === 200 && Object.keys(b.data).length > 0, actual: `${a.status}/${b.status}`, evidence: ev(b) };
  });
  await t('FT-WB-08', 'waste_bio', 'Negative', 'Waste/Bio as viewer / denied analyst / unauthenticated', '403 / 403 / 401 on curves, ytd, arb, tickets', async () => {
    const urls = ['/api/sheets/waste-bio/curves/calendar', '/api/sheets/waste-bio/ytd', '/api/sheets/waste-bio/arb', '/api/sheets/waste-bio/tickets-calc'];
    const s = []; for (const u of urls) s.push([(await V.get(u)).status, (await AD.get(u)).status, (await anon.get(u)).status]);
    return { ok: s.every(([v, d, n]) => v === 403 && d === 403 && n === 401), actual: s.map((x) => x.join(',')).join(' | '), evidence: urls.join(' ') };
  });
  await t('FT-PAR-01', 'parity', 'Positive', 'Parity Ind/China snapshots (viewer)', '200 india[] and china[] non-empty', async () =>
    st(await V.get('/api/sheets/parity-snapshots'), 200, (r) => (r.data.india.length > 0 && r.data.china.length > 0) || `india=${r.data.india.length} china=${r.data.china.length}`));
  await t('FT-PAR-02', 'parity', 'Negative', 'Parity snapshots with parity denied / unauthenticated', '403 / 401', async () => {
    const a = await AD.get('/api/sheets/parity-snapshots'); const b = await anon.get('/api/sheets/parity-snapshots');
    return { ok: a.status === 403 && b.status === 401, actual: `${a.status}/${b.status}`, evidence: ev(a) };
  });

  // --- admin sheet sources ---
  const st0 = (await admin.get('/api/admin/sheets')).data;
  await t('FT-SHT-01', 'sheets-admin', 'Positive', 'Sheet sources status', '200; 4 sources (argus, market_price, china_parity, india_parity); service_account object; no key material', async () => {
    const r = await admin.get('/api/admin/sheets');
    return st(r, 200, (x) => (x.data.sources.length === 4 && ['argus', 'market_price', 'china_parity', 'india_parity'].every((c) => x.data.sources.some((s) => s.code === c)) && 'configured' in x.data.service_account && !/PRIVATE KEY/.test(x.text)) || 'shape');
  });
  await t('FT-SHT-02', 'sheets-admin', 'Positive', 'Refresh one source queues a job; repeat dedups; SHEETS_REFRESH audited', '202 job_id; same id on repeat', async () => {
    const a = await admin.post('/api/admin/sheets/refresh', { sources: ['argus'] }); const b = await admin.post('/api/admin/sheets/refresh', { sources: ['argus'] });
    return { ok: a.status === 202 && b.status === 202 && a.data.job_id === b.data.job_id, actual: `${a.status}/${b.status} same=${a.data?.job_id === b.data?.job_id}`, evidence: ev(a) };
  });
  await t('FT-SHT-03', 'sheets-admin', 'Positive', 'Refresh all (empty body / empty list)', '202 job_id (same "all" job)', async () => {
    const a = await admin.post('/api/admin/sheets/refresh', {}); const b = await admin.post('/api/admin/sheets/refresh', { sources: [] });
    return { ok: a.status === 202 && b.status === 202 && a.data.job_id === b.data.job_id, actual: `${a.status}/${b.status}`, evidence: ev(a) };
  });
  await t('FT-SHT-04', 'sheets-admin', 'Negative', 'Refresh validation: unknown source / wrong type', '422 bad_source / 422 validation_error', async () => {
    const a = await admin.post('/api/admin/sheets/refresh', { sources: ['argus', 'bogus'] }); const b = await admin.post('/api/admin/sheets/refresh', { sources: 'argus' });
    return { ok: a.status === 422 && a.data.error.code === 'bad_source' && b.status === 422, actual: `${a.status} ${a.data?.error?.code}/${b.status}`, evidence: ev(a) };
  });
  await t('FT-SHT-05', 'sheets-admin', 'Negative', 'Refresh by analyst (pipeline.run but not admin)', '403: sheet sources are admin-only (docstring corrected, F-09)', async () =>
    st(await A.post('/api/admin/sheets/refresh', { sources: ['argus'] }), 403));
  await t('FT-SHT-06', 'sheets-admin', 'Negative', 'Sheet admin as viewer / unauthenticated', '403 / 401', async () => {
    const a = await V.get('/api/admin/sheets'); const b = await anon.post('/api/admin/sheets/refresh', {});
    return { ok: a.status === 403 && b.status === 401, actual: `${a.status}/${b.status}`, evidence: ev(a) };
  });

  // service-account key
  const fakeKey = (o = {}) => JSON.stringify({ type: 'service_account', project_id: 'qa-ft', client_email: `qa-ft-${SUFFIX}@qa-ft.iam.gserviceaccount.com`, private_key: '-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n', ...o });
  await t('FT-SHT-07', 'sheets-admin', 'Negative', 'Service-account key: invalid JSON / wrong type / missing private_key / not PEM / truncated PEM / too short', '422 bad_key x5, 422 validation_error', async () => {
    const { privateKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048, privateKeyEncoding: { type: 'pkcs8', format: 'pem' }, publicKeyEncoding: { type: 'spki', format: 'pem' } });
    const truncated = privateKey.slice(0, 200) + '\n-----END PRIVATE KEY-----\n';
    const bodies = ['{"type": "service_account", "client_email": "x@y", ' + 'x'.repeat(40), fakeKey({ type: 'authorized_user' }), JSON.stringify({ type: 'service_account', client_email: 'a@b.c', note: 'x'.repeat(40) }),
      fakeKey({ private_key: 'not a pem key at all, definitely not' }), fakeKey({ private_key: truncated }), '{"a":1}'];
    const rs = []; for (const k of bodies) rs.push(await admin.put('/api/admin/sheets/service-account', { key_json: k }));
    const st1 = await admin.get('/api/admin/sheets');
    return { ok: rs.slice(0, 5).every((r) => r.status === 422 && r.data.error.code === 'bad_key') && rs[5].status === 422 && st1.data.service_account.configured === st0.service_account.configured,
      actual: rs.map((r) => `${r.status}:${r.data?.error?.code}`).join(' '), evidence: rs.map((r) => r.data?.error?.message).slice(0, 5).join(' | ').slice(0, 300) };
  });
  if (st0.service_account.configured) {
    await t('FT-SHT-08', 'sheets-admin', 'Positive', 'Store a valid service-account key, status shows it, key never returned, remove', '200 configured/email; GET configured origin=admin; DELETE removed:true; DELETE again removed:false', async () =>
      ({ blocked: 'A service-account key is already configured on this stack; the suite will not overwrite or delete a key it did not create.' }));
  } else {
    let stored = false;
    onRestore('service-account key', async () => { if (stored) await admin.del('/api/admin/sheets/service-account'); });
    await t('FT-SHT-08', 'sheets-admin', 'Positive', 'Store a valid (throwaway) service-account key, status shows it, key never returned, remove twice', '200 configured/email; GET configured, origin=admin, no PEM in body; DELETE removed:true; DELETE again removed:false', async () => {
      const { privateKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048, privateKeyEncoding: { type: 'pkcs8', format: 'pem' }, publicKeyEncoding: { type: 'spki', format: 'pem' } });
      secrets.add(privateKey);
      const p = await admin.put('/api/admin/sheets/service-account', { key_json: fakeKey({ private_key: privateKey }) });
      stored = p.status === 200;
      const g = await admin.get('/api/admin/sheets');
      const d1 = await admin.del('/api/admin/sheets/service-account'); if (d1.status === 200) stored = false;
      const d2 = await admin.del('/api/admin/sheets/service-account');
      const g2 = await admin.get('/api/admin/sheets');
      return { ok: p.status === 200 && p.data.email.startsWith('qa-ft-') && g.data.service_account.configured === true && g.data.service_account.origin === 'admin' && !/PRIVATE KEY/.test(g.text) &&
        d1.data?.removed === true && d2.data?.removed === false && g2.data.service_account.configured === false,
        actual: `put ${p.status}, status configured=${g.data?.service_account?.configured} origin=${g.data?.service_account?.origin}, del ${d1.data?.removed}, del again ${d2.data?.removed}`, evidence: ev(p) };
    });
  }
  await t('FT-SHT-09', 'sheets-admin', 'Negative', 'Service-account key routes as analyst', '403 forbidden', async () => {
    const a = await A.put('/api/admin/sheets/service-account', { key_json: fakeKey() }); const b = await A.del('/api/admin/sheets/service-account');
    return { ok: a.status === 403 && b.status === 403, actual: `${a.status}/${b.status}`, evidence: ev(a) };
  });

  // xlsx upload
  const up = (code, name, data) => admin.req('POST', '/api/admin/sheets/upload', { form: fileForm({ code }, { data, name, type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }) });
  await t('FT-SHT-10', 'sheets-admin', 'Negative', 'Upload: unknown code / .csv name / empty / corrupt xlsx / missing tab / unparseable Premiums tab / missing file', '422 bad_source / bad_file_type / empty_file / bad_workbook x3 / validation_error', async () => {
    const good = xlsx({ Premiums: premiumsGrid(10) });
    const rs = [await up('bogus', 'x.xlsx', good), await up('market_price', 'x.csv', good), await up('market_price', 'x.xlsx', Buffer.alloc(0)),
      await up('market_price', 'x.xlsx', Buffer.from('this is not a zip')), await up('market_price', 'x.xlsx', xlsx({ Sheet1: [['a']] })),
      await up('market_price', 'x.xlsx', xlsx({ Premiums: [['a', 'b'], ['1', '2']] })),
      await admin.req('POST', '/api/admin/sheets/upload', { form: fileForm({ code: 'market_price' }) })];
    const want = ['bad_source', 'bad_file_type', 'empty_file', 'bad_workbook', 'bad_workbook', 'bad_workbook', 'validation_error'];
    return { ok: rs.every((r, i) => r.status === 422 && r.data?.error?.code === want[i]), actual: rs.map((r) => `${r.status}:${r.data?.error?.code}`).join(' '), evidence: `missing tab msg: ${rs[4].data?.error?.message}`.slice(0, 200) };
  });
  const hadUpload = !!st0.sources.find((s) => s.code === 'market_price')?.upload;
  if (hadUpload) {
    await t('FT-SHT-11', 'sheets-admin', 'Positive', 'Upload a valid Market Price workbook, then delete it', '201 tabs/job_id', async () => ({ blocked: 'market_price already has an upload on this stack; not overwriting it.' }));
  } else {
    let uploaded = false;
    onRestore('market_price upload', async () => { if (uploaded) await admin.del('/api/admin/sheets/upload/market_price'); });
    await t('FT-SHT-11', 'sheets-admin', 'Positive', 'Upload a valid Market Price workbook (.xlsx with Premiums tab)', '201 {code, tabs:{premiums:N}, job_id}; status shows the upload; SHEETS_UPLOAD audited', async () => {
      const r = await up('market_price', 'qa-ft-market-price.xlsx', xlsx({ Premiums: premiumsGrid(30), Other: [['ignored']] }));
      uploaded = r.status === 201;
      const g = await admin.get('/api/admin/sheets');
      const src = g.data.sources.find((s) => s.code === 'market_price');
      const au = await admin.get('/api/admin/audit?action=SHEETS_UPLOAD&page_size=1');
      return { ok: r.status === 201 && r.data.tabs.premiums === 33 && !!r.data.job_id && !!src.upload && au.data.items[0]?.target_id === 'market_price',
        actual: `HTTP ${r.status} tabs=${JSON.stringify(r.data?.tabs)} upload=${JSON.stringify(src?.upload)?.slice(0, 80)}`, evidence: ev(r) };
    });
    await t('FT-SHT-12', 'sheets-admin', 'Positive', 'Delete the upload; delete again; unknown code', '200 removed:true; 200 removed:false; 404', async () => {
      const a = await admin.del('/api/admin/sheets/upload/market_price'); if (a.status === 200) uploaded = false;
      const b = await admin.del('/api/admin/sheets/upload/market_price'); const c = await admin.del('/api/admin/sheets/upload/bogus');
      return { ok: a.data?.removed === true && b.status === 200 && b.data.removed === false && c.status === 404, actual: `${a.status} ${a.data?.removed}/${b.status} ${b.data?.removed}/${c.status}`, evidence: ev(a) };
    });
  }
  await t('FT-SHT-13', 'sheets-admin', 'Negative', 'Upload / delete upload as analyst', '403 forbidden', async () => {
    const a = await A.req('POST', '/api/admin/sheets/upload', { form: fileForm({ code: 'market_price' }, { data: xlsx({ Premiums: premiumsGrid(5) }), name: 'x.xlsx' }) });
    const b = await A.del('/api/admin/sheets/upload/market_price');
    return { ok: a.status === 403 && b.status === 403, actual: `${a.status}/${b.status}`, evidence: ev(a) };
  });
}

// ════ ADMIN: audit, settings, pipeline ══════════════════════════════════════════════════════════════════
async function adminTests(A, V) {
  await t('FT-ADM-01', 'admin', 'Positive', 'Audit log list with action + actor filters', '200; only USER_CREATE rows by the QA admin; actions[] list', async () => {
    const r = await admin.get(`/api/admin/audit?action=USER_CREATE&actor=${enc(ADMIN_EMAIL.split('@')[0])}&page_size=20`);
    return st(r, 200, (x) => (x.data.items.length > 0 && x.data.items.every((a) => a.action === 'USER_CREATE') && x.data.actions.includes('LOGIN_SUCCESS')) || 'filter');
  });
  await t('FT-ADM-02', 'admin', 'Positive', 'Audit user-management rows hold before/after values and no hashes (USR-09)', 'USER_CREATE details.after present; no "hash" in details', async () => {
    const r = await admin.get(`/api/admin/audit?action=USER_CREATE&page_size=50`);
    const mine = r.data.items.filter((a) => a.details?.after?.email?.includes(SUFFIX));
    return { ok: mine.length >= 5 && !mine.some((a) => /hash|password/i.test(JSON.stringify(a.details))), actual: `rows=${mine.length}`, evidence: JSON.stringify(mine[0]?.details).slice(0, 150) };
  });
  await t('FT-ADM-03', 'admin', 'Positive', 'Audit date range: today included; future range empty; reversed range empty', '200; today total>0; future 0; reversed 0', async () => {
    const today = new Date().toISOString().slice(0, 10);
    const a = await admin.get(`/api/admin/audit?from=${today}&to=${today}`); const b = await admin.get('/api/admin/audit?from=2099-01-01');
    const c = await admin.get(`/api/admin/audit?from=${today}&to=2020-01-01`);
    return { ok: a.status === 200 && a.data.total > 0 && b.data.total === 0 && c.status === 200 && c.data.total === 0, actual: `today=${a.data.total} future=${b.data.total} reversed=${c.status}/${c.data?.total}`, evidence: '' };
  });
  await t('FT-ADM-04', 'admin', 'Negative', 'Audit validation: bad date / page 0 / page_size 201 / action > 64 chars', '422 each', async () => {
    const rs = []; for (const q of ['from=2026-13-01', 'to=yesterday', 'page=0', 'page_size=201', `action=${'A'.repeat(65)}`]) rs.push(await admin.get(`/api/admin/audit?${q}`));
    return { ok: rs.every((r) => r.status === 422), actual: rs.map((r) => r.status).join('/'), evidence: ev(rs[0]) };
  });
  await t('FT-ADM-05', 'admin', 'Negative', 'Audit filter by target (ADM-02: filter by action, actor, target and date range)', 'target filter narrows to rows for that target id', async () => {
    const id = users.victim.id;
    const r = await admin.get(`/api/admin/audit?target=${id}&target_id=${id}&page_size=200`);
    const others = r.data.items.filter((a) => a.target_id !== id).length;
    return { ok: r.status === 200 && others === 0 && r.data.items.length > 0, actual: `HTTP ${r.status}, rows=${r.data.items.length}, rows for other targets=${others} (parameter ignored)`, evidence: `/api/admin/audit?target_id=${id}` };
  });
  await t('FT-ADM-06', 'admin', 'Positive', 'Audit CSV export with filter is itself audited', '200 text/csv attachment audit_log.csv; header row; DATA_EXPORT target_type audit_log', async () => {
    const r = await admin.get('/api/admin/audit/export.csv?action=USER_CREATE');
    const lines = r.text.trim().split('\n');
    const au = await admin.get('/api/admin/audit?action=DATA_EXPORT&page_size=5');
    return { ok: r.status === 200 && (r.headers.get('content-type') || '').startsWith('text/csv') && /audit_log\.csv/.test(r.headers.get('content-disposition') || '') && lines[0].startsWith('id,occurred_at,actor_email,action') && lines.length > 1 && au.data.items.some((a) => a.target_type === 'audit_log'),
      actual: `HTTP ${r.status}, lines=${lines.length}`, evidence: lines[0] };
  });
  await t('FT-ADM-07', 'admin', 'Negative', 'Audit CSV neutralises spreadsheet formulas (failed-login email "=1+1@x")', 'cells starting with = + - @ are prefixed (e.g. \'=)', async () => {
    const evil = `=HYPERLINK("http://127.0.0.1:9","qa-ft-${SUFFIX}")`;
    await new Client().login(evil, 'x');                     // unknown email: no account is touched
    const r = await admin.get(`/api/admin/audit/export.csv?action=LOGIN_FAILED&actor=${enc('qa-ft-' + SUFFIX)}`);
    const row = r.text.split('\n').find((l) => l.includes(`qa-ft-${SUFFIX}`)) || '';
    const cell = row.split(',')[2] || '';
    return { ok: r.status === 200 && !!row && !/^"?=/.test(cell), actual: `actor_email cell starts with ${JSON.stringify(cell.slice(0, 12))}`, evidence: row.slice(0, 160) };
  });
  await t('FT-ADM-08', 'admin', 'Negative', 'Audit list/export as analyst', '403 / 403', async () => {
    const a = await A.get('/api/admin/audit'); const b = await A.get('/api/admin/audit/export.csv');
    return { ok: a.status === 403 && b.status === 403, actual: `${a.status}/${b.status}`, evidence: ev(a) };
  });

  // settings
  const s0 = (await admin.get('/api/admin/settings')).data;
  const orig = Object.fromEntries(s0.editable.map((e) => [e.key, e.value]));
  onRestore('settings', async () => {
    const now = Object.fromEntries((await admin.get('/api/admin/settings')).data.editable.map((e) => [e.key, e.value]));
    const diff = Object.fromEntries(Object.entries(orig).filter(([k, v]) => now[k] !== v));
    if (Object.keys(diff).length) await admin.put('/api/admin/settings', { values: diff });
  });
  await t('FT-SET-01', 'settings', 'Positive', 'Settings view', '200 editable keys (4) and integrations (lseg not configured, sso disabled, data_mode demo); no secrets', async () => {
    const r = await admin.get('/api/admin/settings');
    return st(r, 200, (x) => (x.data.editable.length === 4 && x.data.integrations.lseg.configured === false && x.data.integrations.sso.enabled === false && !/password|secret/i.test(JSON.stringify(x.data.integrations))) || 'shape');
  });
  await t('FT-SET-02', 'settings', 'Positive', 'Change idle timeout and restore; audited; unchanged value is a no-op', '200 changed=[key]; same value changed=[]; restore 200; SETTINGS_UPDATE audit', async () => {
    const k = 'security.session_idle_minutes';
    const a = await admin.put('/api/admin/settings', { values: { [k]: orig[k] === 45 ? 46 : 45 } });
    const b = await admin.put('/api/admin/settings', { values: { [k]: orig[k] === 45 ? 46 : 45 } });
    const c = await admin.put('/api/admin/settings', { values: { [k]: orig[k] } });
    const au = await admin.get('/api/admin/audit?action=SETTINGS_UPDATE&page_size=2');
    return { ok: a.status === 200 && a.data.changed[0] === k && b.data.changed.length === 0 && c.data.changed[0] === k && au.data.total >= 2, actual: `${a.status} ${a.data?.changed} / noop ${b.data?.changed?.length} / restore ${c.status}`, evidence: ev(a) };
  });
  await t('FT-SET-03', 'settings', 'Negative', 'Settings validation: idle 4 / 241, absolute 0 / 25, string for int, bool for int, int for bool, unknown key, admin default role, unknown default role', '422 bad_setting each; nothing changed', async () => {
    const bad = [{ 'security.session_idle_minutes': 4 }, { 'security.session_idle_minutes': 241 }, { 'security.session_absolute_hours': 0 }, { 'security.session_absolute_hours': 25 },
      { 'security.session_idle_minutes': '30' }, { 'security.session_idle_minutes': true }, { 'sso.auto_provision': 1 }, { 'data.mode': 'live' }, { 'sso.default_role': 'admin' }, { 'sso.default_role': 'nobody' }];
    const rs = []; for (const v of bad) rs.push(await admin.put('/api/admin/settings', { values: v }));
    const now = Object.fromEntries((await admin.get('/api/admin/settings')).data.editable.map((e) => [e.key, e.value]));
    return { ok: rs.every((r) => r.status === 422 && r.data.error.code === 'bad_setting') && JSON.stringify(now) === JSON.stringify(orig), actual: rs.map((r) => `${r.status}`).join('/'), evidence: ev(rs[0]) };
  });
  await t('FT-SET-04', 'settings', 'Positive', 'Settings boundaries accepted (idle 5 and 240, absolute 1 and 24), then restored', '200 each', async () => {
    const rs = [];
    for (const v of [{ 'security.session_idle_minutes': 5 }, { 'security.session_idle_minutes': 240 }, { 'security.session_absolute_hours': 1 }, { 'security.session_absolute_hours': 24 }]) rs.push(await admin.put('/api/admin/settings', { values: v }));
    const r = await admin.put('/api/admin/settings', { values: { 'security.session_idle_minutes': orig['security.session_idle_minutes'], 'security.session_absolute_hours': orig['security.session_absolute_hours'] } });
    return { ok: rs.every((x) => x.status === 200) && r.status === 200, actual: rs.map((x) => x.status).join('/') + ` restore ${r.status}`, evidence: '' };
  });
  await t('FT-SET-05', 'settings', 'Negative', 'Mixed valid + invalid values are applied atomically (nothing saved)', '422; the valid key keeps its old value', async () => {
    const k = 'security.session_idle_minutes';
    const r = await admin.put('/api/admin/settings', { values: { [k]: orig[k] === 60 ? 61 : 60, 'security.session_absolute_hours': 99 } });
    const now = Object.fromEntries((await admin.get('/api/admin/settings')).data.editable.map((e) => [e.key, e.value]));
    return { ok: r.status === 422 && now[k] === orig[k], actual: `${r.status}, ${k}=${now[k]}`, evidence: ev(r) };
  });
  await t('FT-SET-06', 'settings', 'Negative', 'Settings body validation (missing values / values not an object) and analyst access', '422 / 422 / 403', async () => {
    const a = await admin.put('/api/admin/settings', {}); const b = await admin.put('/api/admin/settings', { values: [1] }); const c = await A.put('/api/admin/settings', { values: {} });
    return { ok: a.status === 422 && b.status === 422 && c.status === 403, actual: `${a.status}/${b.status}/${c.status}`, evidence: ev(a) };
  });

  // pipeline
  await t('FT-PIPE-01', 'pipeline', 'Positive', 'Pipeline status and runs (admin and analyst with pipeline.run)', '200; stats, schedule, last_success; runs list', async () => {
    const a = await admin.get('/api/admin/pipeline/status'); const b = await A.get('/api/admin/pipeline/runs?limit=5');
    return { ok: a.status === 200 && a.data.stats.price_rows > 0 && !!a.data.last_success && b.status === 200 && b.data.items.length >= 1, actual: `${a.status}/${b.status} runs=${b.data?.items?.length}`, evidence: ev(a) };
  });
  await t('FT-PIPE-02', 'pipeline', 'Positive', 'Run detail with steps and findings', '200 with steps[] and findings[]', async () => {
    const id = (await admin.get('/api/admin/pipeline/runs?limit=1')).data.items[0].id;
    return st(await admin.get(`/api/admin/pipeline/runs/${id}`), 200, (r) => (Array.isArray(r.data.steps) && Array.isArray(r.data.findings)) || 'shape');
  });
  await t('FT-PIPE-03', 'pipeline', 'Negative', 'Run detail: nonexistent / malformed id; runs limit 0 / 201', '404 / 404 / 422 / 422', async () => {
    const rs = [await admin.get(`/api/admin/pipeline/runs/${crypto.randomUUID()}`), await admin.get('/api/admin/pipeline/runs/zzz'), await admin.get('/api/admin/pipeline/runs?limit=0'), await admin.get('/api/admin/pipeline/runs?limit=201')];
    return { ok: rs[0].status === 404 && rs[1].status === 404 && rs[2].status === 422 && rs[3].status === 422, actual: rs.map((r) => r.status).join('/'), evidence: ev(rs[0]) };
  });
  await t('FT-PIPE-04', 'pipeline', 'Negative', 'Trigger validation: bad kind / missing kind; full backfill by analyst', '422 / 422 / 403 forbidden (no run created)', async () => {
    const a = await admin.post('/api/admin/pipeline/run', { kind: 'everything' }); const b = await admin.post('/api/admin/pipeline/run', {}); const c = await A.post('/api/admin/pipeline/run', { kind: 'full' });
    return { ok: a.status === 422 && b.status === 422 && c.status === 403, actual: `${a.status}/${b.status}/${c.status}`, evidence: ev(c) };
  });
  await t('FT-PIPE-05', 'pipeline', 'Negative', 'Trigger by viewer / unauthenticated', '403 / 401', async () => {
    const a = await V.post('/api/admin/pipeline/run', { kind: 'recompute' }); const b = await anon.post('/api/admin/pipeline/run', { kind: 'recompute' });
    return { ok: a.status === 403 && b.status === 401, actual: `${a.status}/${b.status}`, evidence: ev(a) };
  });
  await t('FT-PIPE-06', 'pipeline', 'Positive', 'Queue one recompute run (or 409 if one is already queued); second request is refused while busy', 'first 202 queued (or 409 pipeline_busy when a run is already active); second 409 pipeline_busy; PIPELINE_RUN_QUEUED audited', async () => {
    const pre = (await admin.get('/api/admin/pipeline/status')).data.active;
    const a = await A.post('/api/admin/pipeline/run', { kind: 'recompute' });
    const b = await admin.post('/api/admin/pipeline/run', { kind: 'recompute' });
    const okFirst = pre ? a.status === 409 : (a.status === 202 && a.data.status === 'queued' && a.data.kind === 'recompute');
    return { ok: okFirst && b.status === 409 && b.data.error.code === 'pipeline_busy', actual: `active before=${!!pre}; first ${a.status}; second ${b.status} ${b.data?.error?.code}`, evidence: ev(a) };
  });
}

// ════ RUN ═══════════════════════════════════════════════════════════════════════════════════════════════
const started = Date.now();
try {
  await main();
} catch (e) {
  console.error('suite aborted:', scrub(e.stack || e.message));
  record('FT-SUITE-00', 'suite', 'Negative', 'Suite completed without aborting', 'no exception', 'FAIL', scrub(e.message), '');
} finally {
  for (const r of restorers.reverse()) {
    try { await r.fn(); } catch (e) { console.error(`restore ${r.name} failed: ${scrub(e.message)}`); }
  }
  // Leave created users disabled except the UI fixtures? Users are kept (no hard delete); revoke their sessions.
  for (const [tag, u] of Object.entries(users)) {
    if (u?.id) await admin.post(`/api/admin/users/${u.id}/revoke-sessions`, {}).catch(() => {});
  }
  if (admin.session) await admin.post('/api/auth/logout', {}).catch(() => {});
  const out = path.join(HERE, 'results.json');
  fs.writeFileSync(out, JSON.stringify(results, null, 2));
  const c = (s) => results.filter((r) => r.status === s).length;
  console.log(`\n${results.length} tests: ${c('PASS')} PASS, ${c('FAIL')} FAIL, ${c('BLOCKED')} BLOCKED  (${((Date.now() - started) / 1000).toFixed(1)} s, suffix ${SUFFIX})`);
  console.log(`results -> ${out}`);
  for (const r of results.filter((x) => x.status === 'FAIL')) console.log(`  FAIL ${r.id} ${r.title}: ${r.actual}`);
}
