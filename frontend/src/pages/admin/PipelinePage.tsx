import { Fragment, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { PipelineRun, PipelineStatus } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { ErrorAlert, useToast } from '../../components/Feedback';
import { Icon } from '../../components/Icon';
import { fmtDate, fmtDateTime, fmtInt, relTime } from '../../lib/format';
import { hub } from '../../lib/hubSdk';

const STATUS_CLASS: Record<string, string> = {
  succeeded: 'badge-pos', partial: 'badge-accent', failed: 'badge-neg', running: 'badge-info', queued: '',
};

function RunDetail({ id }: { id: string }) {
  const { data } = useQuery({
    queryKey: ['run', id],
    queryFn: () => api.get<PipelineRun>(`/admin/pipeline/runs/${id}`),
    refetchInterval: (q) => (['queued', 'running'].includes(q.state.data?.status ?? '') ? 2000 : false),
  });
  if (!data) return <div className="skeleton" style={{ height: 120 }} />;
  return (
    <div className="stack" style={{ gap: 12 }}>
      {data.error && <div className="alert alert-error"><Icon name="alert" size={16} /> {data.error}</div>}
      <table className="tbl compact">
        <thead><tr><th>#</th><th>Step</th><th>Status</th><th className="r">Seconds</th><th>Details</th></tr></thead>
        <tbody>
          {data.steps?.map((s) => (
            <tr key={s.seq}>
              <td className="muted">{s.seq}</td><td className="mono">{s.step}</td>
              <td><span className={`badge ${STATUS_CLASS[s.status] ?? ''}`}>{s.status}</span></td>
              <td className="r num">{String(s.metrics?.seconds ?? '–')}</td>
              <td className="muted" style={{ fontSize: 12 }}>
                {s.message && <div>{s.message}</div>}
                {Object.entries(s.metrics ?? {}).filter(([k]) => k !== 'seconds').map(([k, v]) => `${k}: ${typeof v === 'number' ? fmtInt(v) : v}`).join(' · ')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!!data.findings?.length && (
        <div>
          <div className="label" style={{ marginBottom: 6 }}>Data-quality findings</div>
          <table className="tbl compact">
            <thead><tr><th>Check</th><th>Severity</th><th>RIC</th><th>Date</th><th>Detail</th></tr></thead>
            <tbody>{data.findings.map((f, i) => (
              <tr key={i}><td className="mono">{f.check}</td><td>{f.severity}</td><td className="mono">{f.ric ?? '–'}</td>
                <td>{fmtDate(f.date)}</td><td className="mono muted">{JSON.stringify(f.detail)}</td></tr>))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function PipelinePage() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [openRun, setOpenRun] = useState<string | null>(null);
  const status = useQuery({
    queryKey: ['pipe-status'],
    queryFn: () => api.get<PipelineStatus>('/admin/pipeline/status'),
    refetchInterval: (q) => (q.state.data?.active ? 3000 : 20000),
  });
  const runs = useQuery({
    queryKey: ['pipe-runs'],
    queryFn: () => api.get<{ items: PipelineRun[] }>('/admin/pipeline/runs?limit=30'),
    refetchInterval: status.data?.active ? 3000 : 20000,
  });
  const trigger = useMutation({
    mutationFn: (kind: 'incremental' | 'recompute' | 'full') => api.post<PipelineRun>('/admin/pipeline/run', { kind }),
    onSuccess: (r) => {
      hub.track('pipeline_run_triggered', { kind: r.kind });
      toast(`${r.kind} run queued`);
      setOpenRun(r.id);
      qc.invalidateQueries({ queryKey: ['pipe-status'] });
      qc.invalidateQueries({ queryKey: ['pipe-runs'] });
    },
  });
  const s = status.data;
  const busy = !!s?.active || trigger.isPending;
  return (
    <>
      <div className="page-head">
        <div><h1>Data pipeline</h1>
          <p>LSEG DataScope → PostgreSQL → derived series. Scheduled {s?.schedule.cron} ({s?.schedule.timezone}); one run at a time.</p></div>
        <div className="row">
          <button className="btn btn-primary" disabled={busy} onClick={() => trigger.mutate('incremental')}>
            <Icon name="play" size={15} /> Run {s?.data_mode === 'demo' ? 'refresh' : 'incremental'}</button>
          <button className="btn" disabled={busy} onClick={() => trigger.mutate('recompute')}><Icon name="refresh" size={15} /> Recompute series</button>
          {can('admin') && s?.lseg_configured && (
            <button className="btn btn-danger" disabled={busy}
                    onClick={() => window.confirm('Start a FULL backfill from 2015? This pulls ~1M rows in yearly chunks and can take a long time.') && trigger.mutate('full')}>
              Full backfill</button>
          )}
        </div>
      </div>
      <ErrorAlert error={status.error || trigger.error} />
      <div className="tiles" style={{ marginBottom: 16 }}>
        <div className="card tile"><span className="t-name">Data source</span>
          <span className="t-val" style={{ fontSize: 18 }}>{s?.data_mode === 'demo' ? 'Demo (synthetic)' : 'LSEG live'}</span>
          <span className="muted" style={{ fontSize: 12 }}>{s?.lseg_configured ? 'Credentials configured' : 'No LSEG credentials'}</span></div>
        <div className="card tile"><span className="t-name">Last success</span>
          <span className="t-val" style={{ fontSize: 18 }}>{relTime(s?.last_success?.finished_at)}</span>
          <span className="muted" style={{ fontSize: 12 }}>{fmtDateTime(s?.last_success?.finished_at)}</span></div>
        <div className="card tile"><span className="t-name">Latest trade date</span>
          <span className="t-val" style={{ fontSize: 18 }}>{fmtDate(s?.stats.latest_trade_date)}</span>
          <span className="muted" style={{ fontSize: 12 }}>{fmtInt(s?.stats.rics_with_data)} RICs with data</span></div>
        <div className="card tile"><span className="t-name">Stored</span>
          <span className="t-val num" style={{ fontSize: 18 }}>{fmtInt(s?.stats.price_rows)}</span>
          <span className="muted" style={{ fontSize: 12 }}>price rows · {fmtInt(s?.stats.series_points)} series points</span></div>
      </div>
      <div className="card table-wrap">
        <table className="tbl">
          <thead><tr><th>Queued</th><th>Kind</th><th>Trigger</th><th>Status</th><th className="r hide-sm">Rows</th><th className="r hide-sm">Series</th><th className="r hide-sm">Duration</th><th className="hide-sm">Version</th></tr></thead>
          <tbody>
            {runs.data?.items.map((r) => (
              <Fragment key={r.id}>
                <tr className="row-click" onClick={() => setOpenRun(openRun === r.id ? null : r.id)}>
                  <td className="num">{fmtDateTime(r.queued_at)}</td><td className="mono">{r.kind}</td><td>{r.trigger}</td>
                  <td><span className={`badge ${STATUS_CLASS[r.status]}`}>{r.status === 'running' && <span className="spinner" style={{ width: 10, height: 10 }} />}{r.status}</span>
                    {r.warnings_count > 0 && <span className="badge badge-accent" style={{ marginLeft: 6 }}>{r.warnings_count} warn</span>}</td>
                  <td className="r num hide-sm">{fmtInt(r.rows_upserted)}</td><td className="r num hide-sm">{r.series_computed}</td>
                  <td className="r num hide-sm">{r.duration_s == null ? '–' : `${r.duration_s.toFixed(1)} s`}</td>
                  <td className="hide-sm muted">{r.data_version ?? '–'}</td>
                </tr>
                {openRun === r.id && <tr><td colSpan={8} style={{ background: 'var(--surface-2)' }}><RunDetail id={r.id} /></td></tr>}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
