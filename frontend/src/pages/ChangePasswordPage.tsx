import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import type { Me } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { Icon } from '../components/Icon';
import { LogoLockup } from '../components/Logo';

export function passwordRules(pw: string, email?: string) {
  const local = (email ?? '').split('@')[0].toLowerCase();
  const classes = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((r) => r.test(pw)).length;
  return [
    { ok: pw.length >= 12 && pw.length <= 128, text: '12–128 characters' },
    { ok: classes >= 3, text: '3 of: lowercase, uppercase, digit, symbol' },
    { ok: !(local.length >= 3 && pw.toLowerCase().includes(local)), text: 'Does not contain your email name' },
  ];
}

export function ChangePasswordPage() {
  const { me, setMe, logout } = useAuth();
  const nav = useNavigate();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const rules = passwordRules(next, me?.email);
  const mismatch = confirm.length > 0 && confirm !== next;
  const valid = rules.every((r) => r.ok) && next === confirm && current.length > 0;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const m = await api.post<Me>('/auth/change-password', { current_password: current, new_password: next });
      setMe(m);
      nav('/', { replace: true });
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-form-wrap" style={{ minHeight: '100%' }}>
      <div className="card login-card" style={{ width: 'min(460px, 100%)' }}>
        <form className="stack" style={{ gap: 16 }} onSubmit={submit}>
          <LogoLockup size={34} />
          <div>
            <h1>{me?.must_change_password ? 'Set your password' : 'Change password'}</h1>
            <p className="muted" style={{ margin: '4px 0 0' }}>
              {me?.must_change_password
                ? 'You signed in with a temporary password. Choose a new one to continue.'
                : 'All your other sessions will be signed out.'}
            </p>
          </div>
          {error && <div className="alert alert-error" role="alert"><Icon name="alert" size={16} /> {error}</div>}
          <div className="field">
            <label htmlFor="cur">Current password</label>
            <input id="cur" className="input" type="password" autoComplete="current-password" value={current}
                   onChange={(e) => setCurrent(e.target.value)} autoFocus />
          </div>
          <div className="field">
            <label htmlFor="new">New password</label>
            <input id="new" className="input" type="password" autoComplete="new-password" value={next}
                   onChange={(e) => setNext(e.target.value)} />
            <ul className="policy">
              {rules.map((r) => (
                <li key={r.text} className={r.ok ? 'ok' : ''}><Icon name={r.ok ? 'check' : 'x'} size={13} /> {r.text}</li>
              ))}
            </ul>
          </div>
          <div className="field">
            <label htmlFor="conf">Confirm new password</label>
            <input id="conf" className="input" type="password" autoComplete="new-password" value={confirm}
                   aria-invalid={mismatch} aria-describedby={mismatch ? 'conf-hint' : undefined}
                   onChange={(e) => setConfirm(e.target.value)} />
            {mismatch && (
              <span id="conf-hint" className="hint" style={{ color: 'var(--neg)' }}>
                Passwords don’t match — retype the new password.
              </span>
            )}
          </div>
          <div className="row">
            <button className="btn btn-primary" disabled={!valid || busy}>{busy ? <span className="spinner" /> : 'Save password'}</button>
            {me?.must_change_password ? (
              <button type="button" className="btn btn-ghost" onClick={async () => { await logout(); nav('/login'); }}>Sign out</button>
            ) : (
              <button type="button" className="btn btn-ghost" onClick={() => nav(-1)}>Cancel</button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
