import { useState, type FormEvent } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Icon } from '../components/Icon';
import { LogoLockup } from '../components/Logo';

const SSO_ERRORS: Record<string, string> = {
  sso_state_invalid: 'Your sign-in link expired or was already used. Please try again.',
  sso_token_failed: 'Downstream Hub could not complete the sign-in. Please try again.',
  sso_token_invalid: 'The sign-in response from Downstream Hub could not be verified.',
  sso_not_registered: 'Your Downstream Hub account is not registered in AASTAR. Ask an administrator for access.',
  sso_email_unverified:
    'Downstream Hub has not verified your email yet. Use the verification link the Hub emailed you, then try again.',
  sso_disabled_user: 'Your AASTAR account is disabled. Contact an administrator.',
  sso_disabled: 'Downstream Hub sign-in is not enabled in this environment.',
  sso_unavailable: 'Downstream Hub is unreachable right now. Try again shortly or use your password.',
  sso_denied: 'Sign-in was cancelled at Downstream Hub.',
};

function safeNext(n: string | null): string {
  return n && n.startsWith('/') && !n.startsWith('//') && !n.startsWith('/api/') ? n : '/';
}

function Waves() {
  // Decorative seasonal overlay — the product's core idiom.
  const lines = [0, 1, 2, 3, 4];
  return (
    <svg className="waves" viewBox="0 0 800 220" preserveAspectRatio="none" aria-hidden="true">
      {lines.map((i) => {
        const amp = 26 + i * 7;
        const off = i * 14;
        const d = Array.from({ length: 41 }, (_, k) => {
          const x = (k / 40) * 800;
          const y = 120 - Math.sin(k / 6.2 + i * 0.7) * amp - Math.cos(k / 3.1 + i) * 8 - off * 0.6 + i * 10;
          return `${k ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(' ');
        const last = i === lines.length - 1;
        return <path key={i} d={d} fill="none" stroke={last ? '#F2B84B' : `rgba(160,214,200,${0.25 + i * 0.1})`}
                     strokeWidth={last ? 3 : 1.4} />;
      })}
    </svg>
  );
}

export function LoginPage() {
  const { me, config, login } = useAuth();
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(SSO_ERRORS[params.get('error') ?? ''] ?? null);
  const next = safeNext(params.get('next'));

  if (me) return <Navigate to={next} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      nav(next, { replace: true });
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <section className="login-brand">
        <LogoLockup size={46} tone="onDark" />
        <div style={{ position: 'relative', zIndex: 1 }}>
          <h2>Seasonal intelligence for the edible-oils &amp; biofuels complex.</h2>
          <p>Palm, soy and energy spreads, basis, crush, forward curves and open interest — normalised to
            USD/MT and overlaid year on year.</p>
        </div>
        <Waves />
        <div className="feat" style={{ position: 'relative', zIndex: 1 }}>
          <span><Icon name="line" size={15} /> 170+ derived series</span>
          <span><Icon name="database" size={15} /> LSEG DataScope daily</span>
          <span><Icon name="shield" size={15} /> Downstream Hub SSO</span>
        </div>
      </section>
      <section className="login-form-wrap">
        <div className="card login-card">
          <div className="stack" style={{ gap: 18 }}>
            <div>
              <h1>Sign in</h1>
              <p className="muted" style={{ margin: '4px 0 0' }}>AASTAR Price Dashboard</p>
            </div>
            {error && <div className="alert alert-error" role="alert"><Icon name="alert" size={16} /> {error}</div>}
            {config?.sso_enabled && (
              <>
                <a className="btn btn-lg btn-block hub-btn"
                   href={`/api/auth/sso/login?next=${encodeURIComponent(next)}`}>
                  <span className="hub-mark">DH</span> {config.sso_label}
                </a>
                {config.local_enabled && <div className="divider">or use your password</div>}
              </>
            )}
            {config?.local_enabled !== false && (
              <form className="stack" style={{ gap: 14 }} onSubmit={submit} noValidate>
                <div className="field">
                  <label htmlFor="email">Email</label>
                  <input id="email" className="input" type="email" autoComplete="username" required value={email}
                         onChange={(e) => setEmail(e.target.value)} autoFocus />
                </div>
                <div className="field">
                  <label htmlFor="password">Password</label>
                  <input id="password" className="input" type="password" autoComplete="current-password" required
                         value={password} onChange={(e) => setPassword(e.target.value)} />
                </div>
                <button className="btn btn-primary btn-lg btn-block" disabled={busy || !email || !password}>
                  {busy ? <span className="spinner" /> : 'Sign in'}
                </button>
              </form>
            )}
            <p className="faint" style={{ fontSize: 12, margin: 0 }}>
              Access is granted by your administrator. Activity is audited.
              {config && <> · {config.environment.toUpperCase()} · v{config.app_version}</>}
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
