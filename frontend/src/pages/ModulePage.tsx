import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api, qs } from '../api/client';
import type { SeriesPayload } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { useCatalog } from '../components/AppShell';
import { SeasonalChart } from '../components/charts';
import { Segmented, UnitToggle, YearPicker } from '../components/Controls';
import { Empty, ErrorAlert } from '../components/Feedback';
import { Icon } from '../components/Icon';
import { useControls } from '../lib/controls';
import { fmtDate, fmtNum } from '../lib/format';
import { hub } from '../lib/hubSdk';
import { ForbiddenPage, NotFoundPage } from './StatusPages';

const TENORS = ['c1', 'c2', 'c3', 'c5', 'c7'] as const;

export function ChartCard({ s, band, exportable, unit }: {
  s: SeriesPayload; band?: boolean; exportable: boolean; unit: string;
}) {
  const empty = s.traces.length === 0;
  return (
    <article className="card chart-card">
      <header className="card-head">
        <div style={{ minWidth: 0 }}>
          <div className="card-title" title={s.title}>{s.title}</div>
          <div className="card-sub">
            {s.unit}
            {s.last && <> · last <b className="num" style={{ color: 'var(--ink-2)' }}>{fmtNum(s.last.value)}</b> on {fmtDate(s.last.date)}</>}
          </div>
        </div>
        {exportable && !empty && (
          <div className="chart-tools">
            <a className="btn btn-ghost btn-sm icon-btn" title="Download CSV" aria-label={`Download ${s.title} as CSV`}
               href={`/api/market/series/${encodeURIComponent(s.key)}/export.csv?unit=${unit}`} download
               onClick={() => hub.track('chart_exported', { series: s.key, format: 'csv' })}>
              <Icon name="download" size={16} />
            </a>
          </div>
        )}
      </header>
      <div className="chart-body">
        {empty ? <Empty title="No data" icon="inbox">No observations in the selected years.</Empty>
               : <SeasonalChart payload={s} band={band} />}
      </div>
    </article>
  );
}

export function ModulePage() {
  const { code = '' } = useParams();
  const { data: catalog, isLoading: catLoading } = useCatalog();
  const { can, hasModule } = useAuth();
  const { years, unit } = useControls();
  const mod = catalog?.modules.find((m) => m.code === code);
  const [group, setGroup] = useState<string | null>(null);
  const [tenor, setTenor] = useState<(typeof TENORS)[number]>('c3');

  useEffect(() => setGroup(null), [code]);
  const activeGroup = group ?? mod?.groups[0]?.name ?? null;
  const showTenor = mod?.has_tenor && activeGroup === 'Futures';
  const groupHasLocal = useMemo(
    () => !!mod?.groups.find((g) => g.name === activeGroup)?.series.some((s) => s.local_unit), [mod, activeGroup]);

  const { data, error, isFetching } = useQuery({
    queryKey: ['section', code, activeGroup, years, unit, showTenor ? tenor : null],
    queryFn: ({ signal }) => api.get<{ series: SeriesPayload[] }>(
      `/market/sections/${code}${qs({ group: activeGroup, years, unit, tenor: showTenor ? tenor : undefined })}`, signal),
    enabled: !!mod && !!activeGroup && years.length > 0,
    placeholderData: keepPreviousData,
  });

  // The catalog only lists modules this user may open; anything else is forbidden (or unknown).
  if (!catLoading && (!mod || !hasModule(code))) return mod === undefined && !hasModule(code) ? <ForbiddenPage /> : <NotFoundPage />;
  if (!mod || mod.kind !== 'seasonal') return mod ? <NotFoundPage /> : <div className="skeleton" style={{ height: 300 }} />;

  return (
    <>
      <div className="page-head">
        <div><h1>{mod.name}</h1><p>{mod.description}</p></div>
        {isFetching && <span className="spinner" aria-label="Loading" />}
      </div>
      <div className="tabs" role="tablist">
        {mod.groups.map((g) => (
          <button key={g.name} role="tab" className="tab" aria-selected={g.name === activeGroup} onClick={() => setGroup(g.name)}>
            {g.name}<span className="count">{g.series.length}</span>
          </button>
        ))}
      </div>
      <div className="card controls">
        <YearPicker />
        {groupHasLocal && <UnitToggle />}
        {showTenor && (
          <div className="control">
            <span className="label">Contract</span>
            <Segmented label="Contract" value={tenor} onChange={setTenor}
                       options={TENORS.map((t) => ({ value: t, label: t.toUpperCase() }))} />
          </div>
        )}
      </div>
      <ErrorAlert error={error} />
      <div className="chart-grid">
        {!data && Array.from({ length: 6 }, (_, i) => <div key={i} className="card skeleton" style={{ height: 350 }} />)}
        {data?.series.map((s) => (
          <ChartCard key={s.key} s={s} band={code === 'leading_signals'} exportable={can('data.export')}
                     unit={groupHasLocal ? unit : 'usd'} />
        ))}
      </div>
    </>
  );
}
