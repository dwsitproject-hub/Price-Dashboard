import { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api, qs } from '../api/client';
import type { Curve } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { ForwardCompareChart, ForwardCurveChart } from '../components/charts';
import { SingleYearSelect, UnitToggle, YearPicker } from '../components/Controls';
import { Empty, ErrorAlert, NoMarketData } from '../components/Feedback';
import { useControls } from '../lib/controls';

export type CurveKind = 'outright' | 'product_spread';

const COMPARE_TITLE: Record<CurveKind, string> = {
  outright: 'All Instruments — Forward Curve Comparison',
  product_spread: 'All Product Spreads — Forward Curve Comparison',
};

/**
 * Forward Curve sub-view (legacy cs-forward / out-forward / ps-forward): one chart per instrument with one
 * line per selected year, sampled on the same day-month, plus a comparison chart overlaying every instrument
 * for a single year (always USD/MT, as in the legacy).
 */
export function ForwardCurvesPage({ kind }: { kind: CurveKind }) {
  const { years, unit, loaded, available } = useControls();
  const { can } = useAuth();
  const noData = loaded && available.length === 0;
  const today = new Date();
  const [snap, setSnap] = useState(today.toISOString().slice(0, 10));
  const [compareYear, setCompareYear] = useState<number | null>(null);
  const cmpYear = compareYear ?? available[available.length - 1] ?? null;
  const md = snap.slice(5);
  const curveUnit = kind === 'outright' ? unit : 'usd';

  const { data, error, isFetching } = useQuery({
    queryKey: ['fwd', kind, years, md, curveUnit],
    queryFn: ({ signal }) => api.get<{ curves: Curve[] }>(
      `/market/forward-curves${qs({ kind, years, snapshot: md, unit: curveUnit })}`, signal),
    enabled: years.length > 0,
    placeholderData: keepPreviousData,
  });
  const { data: cmp } = useQuery({
    queryKey: ['fwd', kind, [cmpYear], md, 'usd'],
    queryFn: ({ signal }) => api.get<{ curves: Curve[] }>(
      `/market/forward-curves${qs({ kind, years: [cmpYear as number], snapshot: md, unit: 'usd' })}`, signal),
    enabled: cmpYear !== null,
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <div className="page-head">
        <div><h1>{kind === 'outright' ? 'Forward Curve' : 'Product Spread Forward Curve'}</h1>
          <p>One line per year, each sampled on the same day-month (last observation on or before it).</p></div>
        {isFetching && <span className="spinner" />}
      </div>
      <div className="card controls">
        <div className="control">
          <label className="label" htmlFor="snap">Snapshot</label>
          <input id="snap" className="input" type="date" value={snap} onChange={(e) => e.target.value && setSnap(e.target.value)}
                 style={{ height: 32, width: 160 }} />
        </div>
        <YearPicker />
        {kind === 'outright' && <UnitToggle />}
      </div>
      <ErrorAlert error={error} />
      {noData ? (
        <div className="chart-grid"><NoMarketData canRunPipeline={can('pipeline.run')} /></div>
      ) : (
        <>
          <article className="card chart-card" style={{ marginBottom: 16 }}>
            <header className="card-head">
              <div><div className="card-title">{COMPARE_TITLE[kind]}{cmpYear ? ` — ${cmpYear}` : ''}</div>
                <div className="card-sub">USD/MT · snapshot {md}</div></div>
              <SingleYearSelect id="cmp-year" label="Year" value={cmpYear} onChange={setCompareYear} />
            </header>
            <div className="chart-body">
              {cmp && cmpYear !== null && cmp.curves.some((c) => c.traces.length)
                ? <ForwardCompareChart curves={cmp.curves} year={cmpYear} />
                : <Empty title="No data">No curves for {cmpYear ?? 'the selected year'}.</Empty>}
            </div>
          </article>
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
      )}
    </>
  );
}
