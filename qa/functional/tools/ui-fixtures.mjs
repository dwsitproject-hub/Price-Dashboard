// UI-test fixtures: creates qa-ft-ui-* users (admin, viewer) through the admin API, or disables them afterwards.
//   node qa/functional/tools/ui-fixtures.mjs create <out.json>   -> writes {tag: {id, email, password}} to <out.json>
//   node qa/functional/tools/ui-fixtures.mjs disable <out.json>
// Keep <out.json> OUTSIDE the repository (e.g. the session scratchpad); it holds throwaway passwords.
import fs from 'node:fs';
import { Client, loadEnv, strongPassword } from '../lib.mjs';

const [cmd, out] = process.argv.slice(2);
if (!cmd || !out) { console.error('usage: ui-fixtures.mjs create|disable <out.json>'); process.exit(2); }
const env = loadEnv();
const admin = new Client('admin');
const l = await admin.login(env.BOOTSTRAP_ADMIN_EMAIL, env.BOOTSTRAP_ADMIN_PASSWORD);
if (l.status !== 200) { console.error('admin login failed', l.status); process.exit(1); }
const suffix = Date.now();
if (cmd === 'create') {
  const res = {};
  for (const [tag, role] of [['ui-admin', 'admin'], ['ui-viewer', 'viewer'], ['ui-analyst', 'analyst']]) {
    const email = `qa-ft-${tag}-${suffix}@example.com`;
    const password = strongPassword();
    const r = await admin.post('/api/admin/users', { email, display_name: `QA FT ${tag}`, role, auth_provider: 'local', password });
    if (r.status !== 201) { console.error(tag, r.status, r.text.slice(0, 200)); process.exit(1); }
    res[tag] = { id: r.data.user.id, email, password };
    console.log(`created ${tag} ${email}`);
  }
  fs.writeFileSync(out, JSON.stringify(res, null, 2));
} else if (cmd === 'disable') {
  const res = JSON.parse(fs.readFileSync(out, 'utf8'));
  for (const [tag, u] of Object.entries(res)) {
    const r = await admin.patch(`/api/admin/users/${u.id}`, { status: 'disabled' });
    console.log(`disabled ${tag}: ${r.status}`);
  }
}
await admin.post('/api/auth/logout', {});
