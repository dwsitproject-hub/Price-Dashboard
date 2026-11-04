import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { Role, SettingsView } from '../../api/types';
import { ErrorAlert, useToast } from '../../components/Feedback';
import { Icon } from '../../components/Icon';
import { fmtDateTime } from '../../lib/format';
import { hubSdkConfigured } from '../../lib/hubSdk';

function Status({ ok, label }: { ok: boolean; label: string }) {
  return <span className={`badge ${ok ? 'badge-pos' : 'badge-accent'}`}><Icon name={ok ? 'check' : 'alert'} size={12} /> {label}</span>;
}

function Copyable({ value }: { value: string }) {
  const toast = useToast();
  return (
    <span className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
      <code className="mono" style={{ wordBreak: 'break-all' }}>{value}</code>
      <button className="btn btn-ghost btn-sm icon-btn" aria-label="Copy" onClick={() => { navigator.clipboard?.writeText(value); toast('Copied'); }}>
        <Icon name="copy" size={13} /></button>
    </span>
  );
}

export function SettingsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data, error } = useQuery({ queryKey: ['settings'], queryFn: () => api.get<SettingsView>('/admin/settings') });
  const roles = useQuery({ queryKey: ['admin-roles'], queryFn: () => api.get<{ roles: Role[] }>('/admin/roles') });
  const [draft, setDraft] = useState<Record<string, boolean | number | string>>({});
  useEffect(() => { if (data) setDraft(Object.fromEntries(data.editable.map((e) => [e.key, e.value]))); }, [data]);
  const save = useMutation({
    mutationFn: () => api.put<{ changed: string[] }>('/admin/settings', { values: draft }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['settings'] }); toast(r.changed.length ? `Saved ${r.changed.length} setting(s)` : 'No changes'); },
  });
  const i = data?.integrations;
  const origin = window.location.origin;
  return (
    <>
      <div className="page-head"><div><h1>Settings &amp; integrations</h1><p>Runtime security settings and the status of LSEG, Downstream Hub SSO, SDK and Sentinel.</p></div></div>
      <ErrorAlert error={error || save.error} />
      <div className="grid-2">
        <section className="card card-pad stack">
          <h3 style={{ fontSize: 15 }}>Security</h3>
          {data?.editable.map((e) => (
            <div key={e.key} className="field">
              <label htmlFor={e.key}>{e.description}</label>
              {e.type === 'bool' ? (
                <label className="row" style={{ gap: 8 }}>
                  <input id={e.key} type="checkbox" className="checkbox" checked={Boolean(draft[e.key])}
                         onChange={(ev) => setDraft({ ...draft, [e.key]: ev.target.checked })} />
                  <span className="muted">{draft[e.key] ? 'Enabled' : 'Disabled'}</span>
                </label>
              ) : e.key === 'sso.default_role' ? (
                <select id={e.key} className="select" value={String(draft[e.key] ?? '')} onChange={(ev) => setDraft({ ...draft, [e.key]: ev.target.value })}>
                  {roles.data?.roles.filter((r) => !r.permissions.includes('admin')).map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
                </select>
              ) : (
                <input id={e.key} className="input" type="number" value={Number(draft[e.key] ?? 0)}
                       onChange={(ev) => setDraft({ ...draft, [e.key]: Number(ev.target.value) })} />
              )}
              <span className="hint mono">{e.key}</span>
            </div>
          ))}
          <div><button className="btn btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>Save settings</button></div>
        </section>

        <section className="card card-pad stack">
          <div className="row"><h3 style={{ fontSize: 15 }}>DWS Downstream Hub — SSO</h3><span className="spacer" />
            {i && <Status ok={i.sso.configured} label={i.sso.configured ? 'Enabled' : 'Not configured'} />}</div>
          <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>Strict OIDC: authorization code + PKCE (S256); ID token verified against the Hub JWKS (iss, aud, exp, nonce, sub).</p>
          {i && (
            <dl className="kv">
              <dt>Issuer</dt><dd><Copyable value={i.sso.issuer} /></dd>
              <dt>Client ID</dt><dd>{i.sso.client_id ? <Copyable value={i.sso.client_id} /> : <span className="faint">not set</span>}</dd>
              <dt>Redirect URI</dt><dd><Copyable value={i.sso.redirect_uri} /></dd>
              <dt>Hub Target URL</dt><dd><Copyable value={i.sso.launch_url} /></dd>
              <dt>Password sign-in</dt><dd>{i.local_login_enabled ? 'Enabled (break-glass for admins)' : 'Disabled'}</dd>
            </dl>
          )}
          <div className="alert alert-info"><Icon name="link" size={16} /><div>Register in Hub Admin → Applications: <b>sso_mode = oidc</b>, the client ID above, and the redirect URI. Set the Target URL so Hub launch signs users straight in.</div></div>
        </section>

        <section className="card card-pad stack">
          <div className="row"><h3 style={{ fontSize: 15 }}>Hub SDK &amp; Sentinel</h3><span className="spacer" />
            <Status ok={hubSdkConfigured} label={hubSdkConfigured ? 'SDK active' : 'No SDK key in this build'} /></div>
          <dl className="kv">
            <dt>Release (data-release)</dt><dd className="mono">{import.meta.env.VITE_GIT_SHA || 'dev'}</dd>
            <dt>Allowed origin</dt><dd><Copyable value={origin} /></dd>
            <dt>Sentinel repository</dt><dd><Copyable value="dwsitproject-hub/Price-Dashboard" /></dd>
            <dt>Application folder</dt><dd className="mono">frontend/</dd>
          </dl>
          <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>Build with <span className="mono">VITE_HUB_SDK_URL</span>, <span className="mono">VITE_HUB_SDK_KEY</span> and <span className="mono">VITE_GIT_SHA</span>.
            The SDK records page paths (no query strings), JS errors and bug reports; users are identified by their Hub <span className="mono">sub</span>.</p>
        </section>

        <section className="card card-pad stack">
          <div className="row"><h3 style={{ fontSize: 15 }}>LSEG DataScope &amp; environment</h3><span className="spacer" />
            {i && <Status ok={i.lseg.configured} label={i.lseg.configured ? 'Live' : 'Demo data'} />}</div>
          {i && (
            <dl className="kv">
              <dt>Environment</dt><dd>{i.environment.toUpperCase()} · v{i.version} · {i.release}</dd>
              <dt>Endpoint</dt><dd className="mono">{i.lseg.base_url}</dd>
              <dt>Token</dt><dd>{i.lseg.token_obtained_at ? `obtained ${fmtDateTime(i.lseg.token_obtained_at)}, expires ${fmtDateTime(i.lseg.token_expires_at)}` : '–'}</dd>
              <dt>Schedule</dt><dd className="mono">{i.pipeline.cron} ({i.pipeline.timezone})</dd>
            </dl>
          )}
          <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>Credentials come from the server environment / KMS only — never from this page. The LSEG token is stored encrypted.</p>
        </section>
      </div>
    </>
  );
}
