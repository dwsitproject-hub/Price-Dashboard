import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, qs } from '../../api/client';
import type { AdminModule, AdminUser, Page, Role } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { ErrorAlert, useToast } from '../../components/Feedback';
import { Icon } from '../../components/Icon';
import { fmtDateTime, initials, relTime } from '../../lib/format';
import { hub } from '../../lib/hubSdk';

type Provider = 'local' | 'sso' | 'both';
const PROVIDER_LABEL: Record<Provider, string> = { local: 'Password', sso: 'Hub SSO', both: 'Password + SSO' };

function StatusPill({ u }: { u: AdminUser }) {
  if (u.status === 'disabled') return <span className="status-pill" style={{ color: 'var(--faint)' }}>Disabled</span>;
  if (u.locked) return <span className="status-pill neg">Locked</span>;
  return <span className="status-pill pos">Active</span>;
}

export function SecretModal({ title, secret, onClose, note }: { title: string; secret: string; onClose: () => void; note: string }) {
  const toast = useToast();
  return (
    <>
      <div className="overlay" />
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="secret-title">
        <div className="stack" style={{ gap: 14 }}>
          <h3 id="secret-title">{title}</h3>
          <p className="muted" style={{ margin: 0 }}>{note}</p>
          <div className="secret">
            <span style={{ flex: 1 }}>{secret}</span>
            <button className="btn btn-sm" onClick={() => { navigator.clipboard?.writeText(secret); toast('Copied to clipboard'); }}>
              <Icon name="copy" size={14} /> Copy
            </button>
          </div>
          <div className="alert alert-warn"><Icon name="alert" size={16} /> This password is shown only once. Share it through a secure channel.</div>
          <div className="row" style={{ justifyContent: 'flex-end' }}><button className="btn btn-primary" onClick={onClose}>Done</button></div>
        </div>
      </div>
    </>
  );
}

function ModuleOverrides({ modules, roleModules, value, onChange }: {
  modules: AdminModule[]; roleModules: string[]; value: Record<string, 'grant' | 'deny'>;
  onChange: (v: Record<string, 'grant' | 'deny'>) => void;
}) {
  const set = (code: string, eff: 'inherit' | 'grant' | 'deny') => {
    const next = { ...value };
    if (eff === 'inherit') delete next[code];
    else next[code] = eff;
    onChange(next);
  };
  return (
    <div className="table-wrap" style={{ border: '1px solid var(--line)', borderRadius: 10 }}>
      <table className="tbl compact">
        <thead><tr><th>Module</th><th>From role</th><th>Override</th></tr></thead>
        <tbody>
          {modules.map((m) => {
            const eff = value[m.code] ?? 'inherit';
            const inherited = roleModules.includes(m.code);
            const final = eff === 'grant' ? true : eff === 'deny' ? false : inherited;
            return (
              <tr key={m.code}>
                <td><span className={final ? '' : 'faint'}>{m.name}</span></td>
                <td>{inherited ? <span className="pos"><Icon name="check" size={14} /></span> : <span className="faint">–</span>}</td>
                <td>
                  <select className="select" style={{ height: 30, width: 120 }} value={eff}
                          onChange={(e) => set(m.code, e.target.value as 'inherit' | 'grant' | 'deny')}
                          aria-label={`Override for ${m.name}`}>
                    <option value="inherit">Inherit</option><option value="grant">Grant</option><option value="deny">Deny</option>
                  </select>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function UserDrawer({ user, roles, modules, onClose, onSecret }: {
  user: AdminUser | null; roles: Role[]; modules: AdminModule[]; onClose: () => void;
  onSecret: (title: string, secret: string) => void;
}) {
  const isNew = user === null;
  const { me } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [email, setEmail] = useState(user?.email ?? '');
  const [name, setName] = useState(user?.display_name ?? '');
  const [role, setRole] = useState(user?.role ?? 'viewer');
  const [provider, setProvider] = useState<Provider>(user?.auth_provider ?? 'sso');   // sign-in is via the Hub
  const [overrides, setOverrides] = useState<Record<string, 'grant' | 'deny'>>(
    Object.fromEntries((user?.overrides ?? []).map((o) => [o.module, o.effect])));
  const [error, setError] = useState<unknown>(null);
  const roleObj = roles.find((r) => r.code === role);
  const isSelf = user?.id === me?.id;
  const refresh = () => qc.invalidateQueries({ queryKey: ['admin-users'] });

  const save = useMutation({
    mutationFn: async () => {
      const ov = Object.entries(overrides).map(([module, effect]) => ({ module, effect }));
      if (isNew) {
        return api.post<{ user: AdminUser; temporary_password: string | null }>('/admin/users', {
          email, display_name: name, role, auth_provider: provider, overrides: ov,
        });
      }
      return { user: await api.patch<AdminUser>(`/admin/users/${user!.id}`, { display_name: name, role, auth_provider: provider, overrides: ov }), temporary_password: null };
    },
    onSuccess: (res) => {
      refresh();
      if (isNew) hub.track('user_created', { role });
      toast(isNew ? 'User registered' : 'Changes saved');
      onClose();
      if (res.temporary_password) onSecret(`Temporary password for ${res.user.email}`, res.temporary_password);
    },
    onError: setError,
  });
  const action = useMutation({
    mutationFn: async (kind: 'reset' | 'unlock' | 'revoke' | 'disable' | 'enable') => {
      const id = user!.id;
      if (kind === 'reset') return api.post<{ temporary_password: string }>(`/admin/users/${id}/reset-password`);
      if (kind === 'unlock') return api.post(`/admin/users/${id}/unlock`);
      if (kind === 'revoke') return api.post(`/admin/users/${id}/revoke-sessions`);
      return api.patch(`/admin/users/${id}`, { status: kind === 'disable' ? 'disabled' : 'active' });
    },
    onSuccess: (res, kind) => {
      refresh();
      const msgs = { reset: 'Password reset', unlock: 'Account unlocked', revoke: 'Sessions revoked', disable: 'User disabled', enable: 'User enabled' };
      toast(msgs[kind]);
      if (kind === 'reset') {
        onClose();
        onSecret(`New temporary password for ${user!.email}`, (res as { temporary_password: string }).temporary_password);
      } else if (kind === 'disable' || kind === 'enable') onClose();
    },
    onError: setError,
  });

  const submit = (e: FormEvent) => { e.preventDefault(); setError(null); save.mutate(); };
  const confirmDo = (kind: 'reset' | 'unlock' | 'revoke' | 'disable' | 'enable', msg: string) => {
    if (window.confirm(msg)) { setError(null); action.mutate(kind); }
  };

  return (
    <>
      <div className="overlay" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <form onSubmit={submit} style={{ display: 'contents' }}>
          <div className="drawer-head">
            <div>
              <h3 id="drawer-title">{isNew ? 'Register new user' : user.display_name}</h3>
              {!isNew && <div className="muted" style={{ fontSize: 12.5 }}>{user.email} · created {fmtDateTime(user.created_at)}</div>}
            </div>
            <button type="button" className="btn btn-ghost icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
          </div>
          <div className="drawer-body">
            <ErrorAlert error={error} />
            <div className="field">
              <label htmlFor="u-email">Email</label>
              <input id="u-email" className="input" type="email" required value={email} disabled={!isNew}
                     onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
              {!isNew && <span className="hint">Email is the account identifier and cannot be changed.</span>}
            </div>
            <div className="field">
              <label htmlFor="u-name">Display name</label>
              <input id="u-name" className="input" required value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="grid-2" style={{ gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div className="field">
                <label htmlFor="u-role">Role</label>
                <select id="u-role" className="select" value={role} onChange={(e) => setRole(e.target.value)} disabled={isSelf}>
                  {roles.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
                </select>
              </div>
              <div className="field">
                <label htmlFor="u-prov">Sign-in method</label>
                <select id="u-prov" className="select" value={provider} onChange={(e) => setProvider(e.target.value as Provider)}>
                  {(Object.keys(PROVIDER_LABEL) as Provider[]).map((p) => <option key={p} value={p}>{PROVIDER_LABEL[p]}</option>)}
                </select>
              </div>
            </div>
            {roleObj && <div className="hint">{roleObj.description}</div>}
            <div className="hint">
              {provider === 'sso'
                ? 'No password. The account links to the Downstream Hub identity (sub) on first SSO sign-in, matched by email.'
                : isNew ? 'A strong password is generated and shown once. Give it to the user securely; they can change it from the user menu.' : ''}
            </div>
            {!roleObj?.permissions.includes('admin') && (
              <div className="field">
                <span className="label">Module access</span>
                <ModuleOverrides modules={modules} roleModules={roleObj?.modules ?? []} value={overrides} onChange={setOverrides} />
                <span className="hint">Deny always wins over a role grant.</span>
              </div>
            )}
            {!isNew && (
              <div className="card card-pad" style={{ background: 'var(--surface-2)' }}>
                <div className="label" style={{ marginBottom: 10 }}>Account actions</div>
                <dl className="kv" style={{ marginBottom: 12 }}>
                  <dt>Hub identity</dt><dd>{user.hub_linked ? 'Linked' : 'Not linked'}</dd>
                  <dt>Last sign-in</dt><dd>{user.last_login_at ? `${relTime(user.last_login_at)} (${user.last_login_method})` : 'Never'}</dd>
                </dl>
                <div className="row">
                  {user.auth_provider !== 'sso' && (
                    <button type="button" className="btn btn-sm" onClick={() => confirmDo('reset', 'Generate a new temporary password? Their sessions will be signed out.')}>
                      <Icon name="key" size={14} /> Reset password</button>)}
                  {user.locked && <button type="button" className="btn btn-sm" onClick={() => action.mutate('unlock')}><Icon name="unlock" size={14} /> Unlock</button>}
                  <button type="button" className="btn btn-sm" onClick={() => confirmDo('revoke', 'Sign this user out everywhere?')}>
                    <Icon name="logout" size={14} /> Revoke sessions</button>
                  {!isSelf && (user.status === 'active'
                    ? <button type="button" className="btn btn-sm btn-danger" onClick={() => confirmDo('disable', `Disable ${user.email}? They will be signed out immediately.`)}>Disable</button>
                    : <button type="button" className="btn btn-sm" onClick={() => action.mutate('enable')}>Enable</button>)}
                </div>
              </div>
            )}
          </div>
          <div className="drawer-foot">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" disabled={save.isPending || !email || !name}>
              {save.isPending ? <span className="spinner" /> : isNew ? 'Register user' : 'Save changes'}
            </button>
          </div>
        </form>
      </aside>
    </>
  );
}

export function UsersPage() {
  const [q, setQ] = useState('');
  const [debounced, setDebounced] = useState('');
  const [role, setRole] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<AdminUser | null | undefined>(undefined);
  const [secret, setSecret] = useState<{ title: string; secret: string } | null>(null);
  useEffect(() => { const t = setTimeout(() => { setDebounced(q); setPage(1); }, 250); return () => clearTimeout(t); }, [q]);

  const users = useQuery({
    queryKey: ['admin-users', debounced, role, status, page],
    queryFn: ({ signal }) => api.get<Page<AdminUser>>(`/admin/users${qs({ q: debounced, role, status, page, page_size: 25 })}`, signal),
    placeholderData: keepPreviousData,
  });
  const roles = useQuery({ queryKey: ['admin-roles'], queryFn: () => api.get<{ roles: Role[] }>('/admin/roles') });
  const modules = useQuery({ queryKey: ['admin-modules'], queryFn: () => api.get<{ modules: AdminModule[] }>('/admin/modules') });
  const pages = useMemo(() => Math.max(1, Math.ceil((users.data?.total ?? 0) / 25)), [users.data]);

  return (
    <>
      <div className="page-head">
        <div><h1>Users &amp; access</h1><p>Register users, assign roles and module access, reset passwords.</p></div>
        <button className="btn btn-primary" onClick={() => setEditing(null)}><Icon name="plus" size={16} /> New user</button>
      </div>
      <div className="card controls">
        <div className="control" style={{ flex: '1 1 260px' }}>
          <Icon name="search" size={16} />
          <input className="input" placeholder="Search name or email" value={q} onChange={(e) => setQ(e.target.value)} style={{ height: 32 }} aria-label="Search users" />
        </div>
        <select className="select" style={{ height: 32, width: 150 }} value={role} onChange={(e) => { setRole(e.target.value); setPage(1); }} aria-label="Filter by role">
          <option value="">All roles</option>
          {roles.data?.roles.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
        </select>
        <select className="select" style={{ height: 32, width: 140 }} value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="Filter by status">
          <option value="">All statuses</option><option value="active">Active</option><option value="disabled">Disabled</option>
        </select>
        <span className="spacer" />
        <span className="muted">{users.data?.total ?? 0} users</span>
      </div>
      <ErrorAlert error={users.error} />
      <div className="card table-wrap">
        <table className="tbl">
          <thead><tr><th>User</th><th>Role</th><th className="hide-sm">Sign-in</th><th>Status</th><th className="hide-sm">Last sign-in</th><th className="hide-sm">Modules</th></tr></thead>
          <tbody>
            {users.data?.items.map((u) => (
              <tr key={u.id} className="row-click" onClick={() => setEditing(u)} tabIndex={0}
                  onKeyDown={(e) => e.key === 'Enter' && setEditing(u)}>
                <td>
                  <div className="row" style={{ flexWrap: 'nowrap' }}>
                    <span className="avatar" style={{ opacity: u.status === 'disabled' ? 0.4 : 1 }}>{initials(u.display_name)}</span>
                    <div style={{ minWidth: 0 }}><div style={{ fontWeight: 600 }}>{u.display_name}</div><div className="muted" style={{ fontSize: 12 }}>{u.email}</div></div>
                  </div>
                </td>
                <td><span className="badge badge-brand">{u.role_name}</span></td>
                <td className="hide-sm">{PROVIDER_LABEL[u.auth_provider]}{u.hub_linked && <span className="badge badge-info" style={{ marginLeft: 6 }}>linked</span>}</td>
                <td><StatusPill u={u} /></td>
                <td className="hide-sm muted">{relTime(u.last_login_at)}</td>
                <td className="hide-sm muted">{u.effective_modules.length}</td>
              </tr>
            ))}
            {users.data?.items.length === 0 && <tr><td colSpan={6} className="muted" style={{ textAlign: 'center', padding: 28 }}>No users match.</td></tr>}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="row" style={{ justifyContent: 'flex-end', marginTop: 12 }}>
          <button className="btn btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
          <span className="muted">Page {page} / {pages}</span>
          <button className="btn btn-sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      )}
      {editing !== undefined && roles.data && modules.data && (
        <UserDrawer user={editing} roles={roles.data.roles} modules={modules.data.modules}
                    onClose={() => setEditing(undefined)} onSecret={(title, s) => setSecret({ title, secret: s })} />
      )}
      {secret && <SecretModal title={secret.title} secret={secret.secret} onClose={() => setSecret(null)}
                              note="Give this to the user. They will be asked to choose their own password at first sign-in." />}
    </>
  );
}

