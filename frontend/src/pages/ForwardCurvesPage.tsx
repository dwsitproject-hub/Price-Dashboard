import { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api, qs } from '../api/client';
import type { Curve } from '../api/types';
import { ForwardCurveChart } from '../components/charts';
import { Segmented, UnitToggle, YearPicker } from '../components/Controls';
import { Empty, ErrorAlert } from '../components/Feedback';
import { useControls } from '../lib/controls';

type Kind = 'outright' | 'product_spread';

export function ForwardCurvesPage() {
  const { years, unit } = useControls();
  const [kind, setKind] = useState<Kind>('outright');
  const today = new Date();
  const [snap, setSnap] = useState(today.toISOString().slice(0, 10));
  const md = snap.slice(5);
  const { data, error, isFetching } = useQuery({
    queryKey: ['fwd', kind, years, md, unit],
    queryFn: ({ signal }) => api.get<{ curves: Curve[] }>(
      `/market/forward-curves${qs({ kind, years, snapshot: md, unit: kind === 'outright' ? unit : 'usd' })}`, signal),
    enabled: years.length > 0,
    placeholderData: keepPreviousData,
  });
  return (
    <>
      <div className="page-head">
        <div><h1>Forward Curves</h1>
          <p>One line per year, each sampled on the same day-month (last observation on or before it).</p></div>
        {isFetching && <span className="spinner" />}
      </div>
      <div className="card controls">
        <div className="control">
          <span className="label">Curve</span>
          <Segmented<Kind> label="Curve type" value={kind} onChange={setKind}
                           options={[{ value: 'outright', label: 'Futures' }, { value: 'product_spread', label: 'Product spreads' }]} />
        </div>
        <div className="control">
          <label className="label" htmlFor="snap">Snapshot</label>
          <input id="snap" className="input" type="date" value={snap} onChange={(e) => e.target.value && setSnap(e.target.value)}
                 style={{ height: 32, width: 160 }} />
        </div>
        <YearPicker />
        {kind === 'outright' && <UnitToggle />}
      </div>
      <ErrorAlert error={error} />
      <div className="chart-grid">
        {!data && Array.from({ length: 6 }, (_, i) => <div key={i} className="card skeleton" style={{ height: 350 }} />)}
        {data?.curves.map((c) => (
          <article key={c.key} className="card chart-card">
            <header className="card-head"><div><div className="card-title">{c.title}</div><div className="card-sub">{c.unit}</div></div></header>
            <div className="chart-body">
              {c.traces.length ? <ForwardCurveChart curve={c} /> : <Empty title="No data">No curve for the selected years.</Empty>}
            </div>
          </article>
        ))}
      </div>
    </>
  );
}
