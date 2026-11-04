import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { Overview } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { modulePath, useCatalog } from '../components/AppShell';
import { Sparkline } from '../components/charts';
import { ErrorAlert } from '../components/Feedback';
import { Icon } from '../components/Icon';
import { fmtDate, fmtNum, fmtSigned, relTime } from '../lib/format';

export function OverviewPage() {
  const { me } = useAuth();
  const { data, error, isLoading } = useQuery({
    queryKey: ['overview'],
    queryFn: ({ signal }) => api.get<Overview>('/market/overview', signal),
  });
  const { data: catalog } = useCatalog();
  const hour = new Date().getHours();
  const greet = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';

  return (
    <>
      <section className="hero">
        <h1>{greet}, {me?.display_name.split(' ')[0]}</h1>
        <p>Latest settlement across the palm, soy and energy complex. Every spread and basis series is
          normalised to USD/MT and overlaid by year.</p>
        <div className="hero-stats">
          <div><b>{fmtDate(data?.latest_trade_date)}</b><span>Latest trade date</span></div>
          <div><b>{data ? relTime(data.last_run?.finished_at) : '–'}</b><span>Last refresh</span></div>
          <div><b>{data?.series_with_data ?? '–'}</b><span>Series with data</span></div>
          <div><b>{data?.instruments ?? '–'}</b><span>Instruments tracked</span></div>
        </div>
        <svg className="deco" width="360" height="140" viewBox="0 0 360 140" aria-hidden="true">
          <path d="M0 120 C60 100 80 40 140 60 S230 120 280 50 340 20 360 30" fill="none" stroke="#F2B84B" strokeWidth="3" />
          <path d="M0 130 C60 120 90 70 150 85 S240 120 290 80 340 60 360 66" fill="none" stroke="#7fd1bd" strokeWidth="1.5" />
        </svg>
      </section>

      <ErrorAlert error={error} />
      <div className="page-head" style={{ marginBottom: 12 }}>
        <div><h2 style={{ fontSize: 16 }}>Benchmarks</h2><p>Futures c3 in native units · FX mid</p></div>
      </div>
      <div className="tiles">
        {isLoading && Array.from({ length: 12 }, (_, i) => <div key={i} className="card skeleton" style={{ height: 118 }} />)}
        {data?.tiles.map((t) => {
          const up = (t.change ?? 0) >= 0;
          return (
            <div key={t.ric} className="card tile">
              <div className="t-row"><span className="t-name">{t.title}</span><span className="mono faint">{t.ric}</span></div>
              <div><span className="t-val num">{fmtNum(t.value, t.ric.endsWith('=') ? 4 : undefined)}</span>
                <span className="t-unit">{t.unit}</span></div>
              <div className="t-row">
                <span className={`num ${up ? 'pos' : 'neg'}`} style={{ fontWeight: 650 }}>
                  {up ? '▲' : '▼'} {fmtSigned(t.change, t.ric.endsWith('=') ? 4 : undefined)}
                  {t.change_pct != null && <span style={{ fontWeight: 500 }}> ({fmtSigned(t.change_pct, 2)}%)</span>}
                </span>
                {t.usd_mt != null && t.unit !== 'USD/MT' && <span className="faint num">${fmtNum(t.usd_mt, 0)}/MT</span>}
              </div>
              {t.spark && <Sparkline values={t.spark} up={up} />}
            </div>
          );
        })}
      </div>

      <div className="page-head" style={{ margin: '26px 0 12px' }}>
        <div><h2 style={{ fontSize: 16 }}>Your modules</h2><p>Everything your role can open</p></div>
      </div>
      <div className="quick">
        {catalog?.modules.filter((m) => m.code !== 'overview').map((m) => (
          <Link key={m.code} to={modulePath(m.code)} className="card">
            <span className="q-icon"><Icon name={m.icon} /></span>
            <span><b>{m.name}</b><span>{m.description}</span></span>
          </Link>
        ))}
      </div>
    </>
  );
}
