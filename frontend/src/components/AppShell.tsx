import { useEffect, useRef, useState, type ReactNode } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { ModuleCatalog } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { fmtDate, initials } from '../lib/format';
import { hub, hubSdkConfigured } from '../lib/hubSdk';
import { useControls, type Theme } from '../lib/controls';
import { Icon } from './Icon';
import { LogoMark } from './Logo';

export function useCatalog() {
  return useQuery({
    queryKey: ['catalog'],
    queryFn: ({ signal }) => api.get<{ modules: ModuleCatalog[] }>('/market/catalog', signal),
    staleTime: 5 * 60_000,
  });
}

const MODULE_PATH: Record<string, string> = {
  overview: '/', forward_curves: '/forward-curves', oi_volume: '/oi-volume', technical_analysis: '/technical-analysis',
};
export const modulePath = (code: string) => MODULE_PATH[code] ?? `/m/${code}`;

const ADMIN_LINKS = [
  { to: '/admin/users', label: 'Users & access', icon: 'users', perm: 'admin' },
  { to: '/admin/roles', label: 'Roles & modules', icon: 'shield', perm: 'admin' },
  { to: '/admin/pipeline', label: 'Data pipeline', icon: 'database', perm: 'pipeline.run' },
  { to: '/admin/audit', label: 'Audit log', icon: 'list', perm: 'admin' },
  { to: '/admin/settings', label: 'Settings', icon: 'settings', perm: 'admin' },
];

function UserMenu() {
  const { me, logout } = useAuth();
  const { theme, setTheme } = useControls();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const nav = useNavigate();
  useEffect(() => {
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);
  if (!me) return null;
  const themes: { v: Theme; icon: string; label: string }[] = [
    { v: 'system', icon: 'monitor', label: 'System' }, { v: 'light', icon: 'sun', label: 'Light' },
    { v: 'dark', icon: 'moon', label: 'Dark' },
  ];
  return (
    <div className="menu" ref={ref}>
      <button className="menu-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-haspopup="menu">
        <span className="avatar">{initials(me.display_name)}</span>
        <span className="hide-sm" style={{ fontWeight: 600, fontSize: 13 }}>{me.display_name.split(' ')[0]}</span>
        <Icon name="chevron" size={14} />
      </button>
      {open && (
        <div className="menu-pop" role="menu">
          <div className="head">
            <div style={{ fontWeight: 650 }}>{me.display_name}</div>
            <div className="muted" style={{ fontSize: 12 }}>{me.email}</div>
            <div className="row" style={{ marginTop: 8, gap: 6 }}>
              <span className="badge badge-brand">{me.role_name}</span>
              <span className="badge">{me.auth_method === 'sso' ? 'Hub SSO' : 'Password'}</span>
            </div>
          </div>
          <div style={{ padding: '6px 8px' }}>
            <div className="seg" style={{ width: '100%' }}>
              {themes.map((t) => (
                <button key={t.v} aria-pressed={theme === t.v} onClick={() => setTheme(t.v)} title={t.label}
                        style={{ flex: 1, display: 'inline-flex', justifyContent: 'center', alignItems: 'center', gap: 5 }}>
                  <Icon name={t.icon} size={14} /> {t.label}
                </button>
              ))}
            </div>
          </div>
          {me.auth_provider !== 'sso' && (
            <button className="menu-item" role="menuitem" onClick={() => { setOpen(false); nav('/change-password'); }}>
              <Icon name="key" size={16} /> Change password
            </button>
          )}
          {hubSdkConfigured && (
            <button className="menu-item" role="menuitem" onClick={() => { setOpen(false); hub.openBugReport(); }}>
              <Icon name="bug" size={16} /> Report a problem
            </button>
          )}
          <button className="menu-item" role="menuitem" onClick={async () => { await logout(); nav('/login'); }}>
            <Icon name="logout" size={16} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export function AppShell({ title, children }: { title: string; children: ReactNode }) {
  const { me, config, can } = useAuth();
  const { asOf } = useControls();
  const { data } = useCatalog();
  const [navOpen, setNavOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setNavOpen(false), [loc.pathname]);
  useEffect(() => {
    document.title = `${title} · AASTAR`;
  }, [title]);

  const groups = new Map<string, ModuleCatalog[]>();
  for (const m of data?.modules ?? []) groups.set(m.nav_group, [...(groups.get(m.nav_group) ?? []), m]);
  const adminLinks = ADMIN_LINKS.filter((l) => can(l.perm));

  return (
    <div className={`shell${navOpen ? ' nav-open' : ''}`} onClick={(e) => {
      if (navOpen && (e.target as HTMLElement).classList.contains('shell')) setNavOpen(false);
    }}>
      <aside className="sidebar" aria-label="Main navigation">
        <div className="sidebar-brand">
          <LogoMark size={38} />
          <div className="wordmark"><b>AASTAR</b><span>PRICE INTELLIGENCE</span></div>
        </div>
        <nav className="sidebar-nav">
          {[...groups.entries()].map(([g, mods]) => (
            <div className="nav-group" key={g}>
              <div className="nav-group-title">{g}</div>
              {mods.map((m) => (
                <NavLink key={m.code} to={modulePath(m.code)} end className="nav-link">
                  <Icon name={m.icon} size={17} /> {m.name}
                </NavLink>
              ))}
            </div>
          ))}
          {adminLinks.length > 0 && (
            <div className="nav-group">
              <div className="nav-group-title">Administration</div>
              {adminLinks.map((l) => (
                <NavLink key={l.to} to={l.to} className="nav-link"><Icon name={l.icon} size={17} /> {l.label}</NavLink>
              ))}
            </div>
          )}
        </nav>
        <div className="sidebar-foot">
          v{config?.app_version} · {config?.environment?.toUpperCase()}
          {config?.release && config.release !== 'local' ? ` · ${config.release}` : ''}
        </div>
      </aside>
      <div className="main">
        {config?.data_mode === 'demo' && (
          <div className="banner" role="status">
            <Icon name="alert" size={16} />
            <span><b>Demo data.</b> LSEG credentials are not configured — every price shown is synthetic.</span>
          </div>
        )}
        <header className="topbar">
          <button className="btn btn-ghost icon-btn hamburger" aria-label="Open navigation" onClick={() => setNavOpen(true)}>
            <Icon name="menu" />
          </button>
          <div className="topbar-title">{title}</div>
          {asOf && (
            <span className="chip hide-sm" title="Latest observation in the database">
              <span className="dot" /> Data as of {fmtDate(asOf)}
            </span>
          )}
          {me && <UserMenu />}
        </header>
        <main className="content" id="main">{children}</main>
      </div>
    </div>
  );
}
