// Revoke all sessions of one qa-ft user (by id) as the QA admin — used by the UI session-expiry scenario.
import { Client, loadEnv } from '../lib.mjs';
const id = process.argv[2];
if (!id) { console.error('usage: revoke.mjs <user-id>'); process.exit(2); }
const env = loadEnv();
const a = new Client('admin');
await a.login(env.BOOTSTRAP_ADMIN_EMAIL, env.BOOTSTRAP_ADMIN_PASSWORD);
const u = await a.get(`/api/admin/users/${id}`);
if (!u.data?.email?.startsWith('qa-ft-')) { console.error('refusing: not a qa-ft user'); process.exit(1); }
const r = await a.post(`/api/admin/users/${id}/revoke-sessions`, {});
console.log('revoked', r.status, r.data?.revoked);
await a.post('/api/auth/logout', {});
