import { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { TaInstrument, TaPayload } from '../api/types';
import { TaChart } from '../components/charts';
import { UnitToggle } from '../components/Controls';
import { Empty, ErrorAlert } from '../components/Feedback';
import { useControls } from '../lib/controls';
import { fmtNum } from '../lib/format';

export function TechnicalAnalysisPage() {
  const { unit, available } = useControls();
  const [key, setKey] = useState('FCPOc3');
  const [startYear, setStartYear] = useState(2020);
  const { data: inst } = useQuery({
    queryKey: ['ta-inst'],
    queryFn: ({ signal }) => api.get<{ instruments: TaInstrument[] }>('/market/ta/instruments', signal),
  });
  const selected = inst?.instruments.find((i) => i.key === key);
  const { data, error, isFetching } = useQuery({
    queryKey: ['ta', key, startYear, unit],
    queryFn: ({ signal }) => api.get<TaPayload>(`/market/ta/${encodeURIComponent(key)}?start_year=${startYear}&unit=${unit}`, signal),
    placeholderData: keepPreviousData,
  });
  const years = available.length ? available : [2020];
  return (
    <>
      <div className="page-head">
        <div><h1>Technical Analysis</h1>
          <p>Support/resistance from K-Means clusters of rolling 30-day highs and lows (elbow-selected k), with 50/100/200-day SMAs.</p></div>
        {isFetching && <span className="spinner" />}
      </div>
      <div className="card controls">
        <div className="control">
          <label className="label" htmlFor="inst">Instrument</label>
          <select id="inst" className="select" value={key} onChange={(e) => setKey(e.target.value)} style={{ height: 32, minWidth: 280 }}>
            <optgroup label="Prices">
              {inst?.instruments.filter((i) => i.kind === 'price').map((i) => <option key={i.key} value={i.key}>{i.title}</option>)}
            </optgroup>
            <optgroup label="Spreads">
              {inst?.instruments.filter((i) => i.kind === 'spread').map((i) => <option key={i.key} value={i.key}>{i.title}</option>)}
            </optgroup>
          </select>
        </div>
        <div className="control">
          <label className="label" htmlFor="sy">From</label>
          <select id="sy" className="select" value={startYear} onChange={(e) => setStartYear(Number(e.target.value))} style={{ height: 32, width: 100 }}>
            {years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </div>
        {selected?.kind === 'price' && !key.endsWith('=') && <UnitToggle />}
      </div>
      <ErrorAlert error={error} />
      <div className="grid-2" style={{ gridTemplateColumns: 'minmax(0, 3fr) minmax(220px, 1fr)' }}>
        <article className="card chart-card">
          <header className="card-head"><div><div className="card-title">{data?.title ?? '…'}</div><div className="card-sub">{data?.unit}</div></div></header>
          <div style={{ padding: '4px 8px 8px' }}>
            {data && data.dates.length ? <TaChart ta={data} /> : <div style={{ height: 460 }}>{data && <Empty title="No data" />}</div>}
          </div>
        </article>
        <article className="card card-pad">
          <div className="card-title" style={{ marginBottom: 10 }}>Ranked levels</div>
          {data?.levels.length ? (
            <table className="tbl compact num">
              <thead><tr><th>#</th><th className="r">Level</th><th className="r">vs last</th></tr></thead>
              <tbody>
                {[...data.levels].reverse().map((l, i) => {
                  const last = [...data.price].reverse().find((v) => v !== null) ?? null;
                  const d = last === null ? null : ((l - last) / Math.abs(last)) * 100;
                  return (
                    <tr key={i}><td className="muted">{i + 1}</td><td className="r">{fmtNum(l)}</td>
                      <td className={`r ${d !== null && d >= 0 ? 'pos' : 'neg'}`}>{d === null ? '–' : `${d > 0 ? '+' : ''}${d.toFixed(1)}%`}</td></tr>
                  );
                })}
              </tbody>
            </table>
          ) : <p className="muted">Not enough history for levels.</p>}
        </article>
      </div>
    </>
  );
}
