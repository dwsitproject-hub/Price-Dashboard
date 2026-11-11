import { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api, qs } from '../api/client';
import type { YtdProduct } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { YtdChart } from '../components/charts';
import { SingleYearSelect, UnitToggle } from '../components/Controls';
import { Empty, ErrorAlert, NoMarketData } from '../components/Feedback';
import { useControls } from '../lib/controls';

/**
 * Calendar Spreads → YTD (legacy cs-ytd): per futures product, the C1−C2 … C1−C7 spreads on the actual trading
 * dates of one year, so the term structure is comparable on one axis, with the C1 price for context.
 */
export function YtdPage() {
  const { unit, loaded, available } = useControls();
  const { can } = useAuth();
  const noData = loaded && available.length === 0;
  const [year, setYear] = useState<number | null>(null);
  const y = year ?? available[available.length - 1] ?? null;

  const { data, error, isFetching } = useQuery({
    queryKey: ['ytd', y, unit],
    queryFn: ({ signal }) => api.get<{ products: YtdProduct[] }>(`/market/ytd-spreads${qs({ year: y as number, unit })}`,
                                                                 signal),
    enabled: y !== null,
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <div className="page-head">
        <div><h1>Calendar Spreads — YTD</h1>
          <p>C1 minus each later contract on the trading days of one year; the dotted line is the C1 price.</p></div>
        {isFetching && <span className="spinner" />}
      </div>
      <div className="card controls">
        <SingleYearSelect id="ytd-year" label="Year" value={y} onChange={setYear} />
        <UnitToggle />
      </div>
      <ErrorAlert error={error} />
      <div className="chart-grid">
        {noData && <NoMarketData canRunPipeline={can('pipeline.run')} />}
        {!data && !noData && Array.from({ length: 6 }, (_, i) => <div key={i} className="card skeleton" style={{ height: 370 }} />)}
        {data?.products.map((p) => (
          <article key={p.key} className="card chart-card">
            <header className="card-head">
              <div><div className="card-title">{p.title} — {p.year}</div><div className="card-sub">{p.unit}</div></div>
            </header>
            <div className="chart-body">
              {p.legs.length ? <YtdChart item={p} /> : <Empty title="No data">No contracts for {p.year}.</Empty>}
            </div>
          </article>
        ))}
      </div>
    </>
  );
}
