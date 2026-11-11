import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { ParitySnapshots, SnapshotBlock } from '../api/types';
import { SnapshotChart } from '../components/charts';
import { Empty, ErrorAlert } from '../components/Feedback';
import { fmtDateTime } from '../lib/format';

function Block({ b }: { b: SnapshotBlock }) {
  return (
    <article className="card chart-card">
      <header className="card-head">
        <div><div className="card-title">{b.title} ({b.period === 'daily' ? 'Daily' : 'Weekly'})</div>
          <div className="card-sub">USD/MT · {b.period === 'daily' ? 'Today and the 4 prior days' : 'Today and the 4 prior weeks'}</div></div>
      </header>
      <div className="chart-body">
        {b.records.length ? <SnapshotChart records={b.records} /> : <Empty title="No data">The sheet block is empty.</Empty>}
      </div>
    </article>
  );
}

/**
 * Parity → Ind/China (legacy pa-indchina): forward-curve snapshots from the India and China parity workbooks —
 * one line per snapshot row, x = the sheet's maturities. India first, then China, as in the legacy.
 */
export function ParitySnapshotPage() {
  const { data, error, isLoading } = useQuery({
    queryKey: ['parity-snapshots'], queryFn: () => api.get<ParitySnapshots>('/sheets/parity-snapshots'),
  });
  return (
    <>
      <div className="page-head">
        <div><h1>Parity — Ind/China</h1>
          <p>Import-parity forward curves from the India and China parity sheets; one line per snapshot row (Today boldest).</p></div>
      </div>
      <ErrorAlert error={error} />
      {isLoading && <div className="card skeleton" style={{ height: 360 }} />}
      {data && (
        <>
          <h3 style={{ margin: '4px 0 10px' }}>India {data.india_updated_at && <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>· loaded {fmtDateTime(data.india_updated_at)}</span>}</h3>
          {data.india.length ? <div className="chart-grid">{data.india.map((b) => <Block key={`${b.key}-${b.period}`} b={b} />)}</div>
            : <div className="card"><Empty title="India parity not loaded yet">See Administration → Sheet sources.</Empty></div>}
          <h3 style={{ margin: '24px 0 10px' }}>China {data.china_updated_at && <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>· loaded {fmtDateTime(data.china_updated_at)}</span>}</h3>
          {data.china.length ? <div className="chart-grid">{data.china.map((b) => <Block key={`${b.key}-${b.period}`} b={b} />)}</div>
            : <div className="card"><Empty title="China parity not loaded yet">See Administration → Sheet sources.</Empty></div>}
        </>
      )}
    </>
  );
}
