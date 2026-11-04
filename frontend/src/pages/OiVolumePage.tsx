import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { OiRow, OiTable } from '../api/types';
import { Segmented } from '../components/Controls';
import { ErrorAlert } from '../components/Feedback';
import { fmtDate, fmtInt, fmtNum, fmtSigned } from '../lib/format';

function Arrow({ r }: { r: OiRow }) {
  if (r.arrow === 'flat' || !r.arrow_n) return <span className="faint">–</span>;
  const glyph = r.arrow === 'up' ? '▲' : '▼';
  return <span className={`arrow ${r.arrow === 'up' ? 'pos' : 'neg'}`} aria-label={`${r.arrow} ×${r.arrow_n}`}>{glyph.repeat(r.arrow_n)}</span>;
}

function OiVol({ r }: { r: OiRow }) {
  if (r.oi_vol_pct === null) return <span className="faint">n/a</span>;
  const txt = `${fmtSigned(r.oi_vol_pct, 0)}%`;
  if (r.oi_vol_flag === 'anomaly') {
    return <span className="faint" title={`${txt} — |OI/Vol| > 100% is almost always a vendor OI correction or roll artefact`}>–</span>;
  }
  return <b className={r.oi_vol_flag === 'notable' ? '' : undefined} style={r.oi_vol_flag === 'notable' ? { color: 'var(--warn)' } : { fontWeight: 500 }}>{txt}</b>;
}

function Table({ t }: { t: OiTable }) {
  return (
    <article className="card oi-card">
      <header className="card-head" style={{ paddingBottom: 10 }}>
        <div>
          <div className="card-title" style={{ fontSize: 15 }}>{t.title}</div>
          <div className="card-sub">Σ c1–c{t.contracts_scanned} · arrows: ×1 &lt; {fmtNum(t.threshold_median, 2)}% ≤ ×2 &lt; {fmtNum(t.threshold_p75, 2)}% ≤ ×3</div>
        </div>
        <span className="badge">{t.product}</span>
      </header>
      <div className="table-wrap" style={{ maxHeight: 560 }}>
        <table className="tbl compact num">
          <thead>
            <tr>
              <th>Date</th><th className="r">Total OI</th><th className="r">Δ OI</th><th className="r">Total Vol</th>
              <th className="r">Δ Vol</th><th className="r">OI/Vol</th><th className="r">Avg Px ({t.unit})</th><th className="r">Px Chg</th>
            </tr>
          </thead>
          <tbody>
            {t.rows.map((r) => (
              <tr key={r.date} className={r.oi_vol_flag === 'notable' ? 'notable' : ''}>
                <td>{fmtDate(r.date)}</td>
                <td className="r">{fmtInt(r.total_oi)}</td>
                <td className={`r ${(r.oi_chg ?? 0) >= 0 ? 'pos' : 'neg'}`}>{r.oi_chg == null ? '–' : fmtSigned(r.oi_chg, 0)}</td>
                <td className="r">{fmtInt(r.total_volume)}</td>
                <td className="r muted">{r.volume_chg == null ? '–' : fmtSigned(r.volume_chg, 0)}</td>
                <td className="r"><OiVol r={r} /></td>
                <td className="r">{fmtNum(r.avg_price)}</td>
                <td className="r" style={{ whiteSpace: 'nowrap' }}>
                  <Arrow r={r} /> <span className="muted">{r.price_chg_pct == null ? '' : `${fmtSigned(r.price_chg_pct, 2)}%`}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}

export function OiVolumePage() {
  const [days, setDays] = useState<'30' | '60'>('30');
  const { data, error } = useQuery({
    queryKey: ['oi', days],
    queryFn: ({ signal }) => api.get<{ tables: OiTable[] }>(`/market/oi-cumulative?days=${days}`, signal),
  });
  return (
    <>
      <div className="page-head">
        <div>
          <h1>OI / Volume</h1>
          <p>Total open interest and volume summed across every live contract month.
            <span className="badge badge-accent" style={{ marginLeft: 8 }}>amber row = OI/Vol 10–100%</span>
            <span className="badge" style={{ marginLeft: 6 }}>“–” = |OI/Vol| &gt; 100% (likely vendor artefact)</span></p>
        </div>
        <Segmented label="Window" value={days} onChange={setDays}
                   options={[{ value: '30', label: '30 days' }, { value: '60', label: '60 days' }]} />
      </div>
      <ErrorAlert error={error} />
      <div className="oi-grid">
        {!data && Array.from({ length: 4 }, (_, i) => <div key={i} className="card skeleton oi-card" style={{ height: 420 }} />)}
        {data?.tables.map((t) => <Table key={t.product} t={t} />)}
      </div>
    </>
  );
}
