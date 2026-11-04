import { Fragment, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api, qs } from '../../api/client';
import type { AuditEntry, Page } from '../../api/types';
import { ErrorAlert } from '../../components/Feedback';
import { Icon } from '../../components/Icon';
import { fmtDateTime } from '../../lib/format';

export function AuditPage() {
  const [action, setAction] = useState('');
  const [actor, setActor] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<number | null>(null);
  const filters = { action, actor, from, to };
  const { data, error } = useQuery({
    queryKey: ['audit', filters, page],
    queryFn: ({ signal }) => api.get<Page<AuditEntry> & { actions: string[] }>(`/admin/audit${qs({ ...filters, page, page_size: 50 })}`, signal),
    placeholderData: keepPreviousData,
  });
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / 50));
  return (
    <>
      <div className="page-head">
        <div><h1>Audit log</h1><p>Append-only record of sign-ins, account changes, exports, settings and pipeline runs.</p></div>
        <a className="btn" href={`/api/admin/audit/export.csv${qs(filters)}`} download><Icon name="download" size={16} /> Export CSV</a>
      </div>
      <div className="card controls">
        <select className="select" style={{ height: 32, width: 220 }} value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} aria-label="Action">
          <option value="">All actions</option>
          {data?.actions.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <input className="input" style={{ height: 32, width: 220 }} placeholder="Actor email" value={actor}
               onChange={(e) => { setActor(e.target.value); setPage(1); }} aria-label="Actor" />
        <div className="control"><span className="label">From</span>
          <input type="date" className="input" style={{ height: 32 }} value={from} onChange={(e) => { setFrom(e.target.value); setPage(1); }} /></div>
        <div className="control"><span className="label">To</span>
          <input type="date" className="input" style={{ height: 32 }} value={to} onChange={(e) => { setTo(e.target.value); setPage(1); }} /></div>
        <span className="spacer" /><span className="muted">{data?.total ?? 0} entries</span>
      </div>
      <ErrorAlert error={error} />
      <div className="card table-wrap">
        <table className="tbl">
          <thead><tr><th>When</th><th>Actor</th><th>Action</th><th className="hide-sm">Target</th><th>Outcome</th><th className="hide-sm">IP</th></tr></thead>
          <tbody>
            {data?.items.map((a) => (
              <Fragment key={a.id}>
                <tr className="row-click" onClick={() => setOpen(open === a.id ? null : a.id)}>
                  <td className="num" style={{ whiteSpace: 'nowrap' }}>{fmtDateTime(a.occurred_at)}</td>
                  <td>{a.actor_email ?? <span className="faint">system</span>}</td>
                  <td><span className="mono">{a.action}</span></td>
                  <td className="hide-sm muted">{a.target_type ? `${a.target_type}${a.target_id ? ` · ${a.target_id.slice(0, 18)}` : ''}` : '–'}</td>
                  <td><span className={`badge ${a.outcome === 'success' ? 'badge-pos' : 'badge-neg'}`}>{a.outcome}</span></td>
                  <td className="hide-sm mono muted">{a.ip ?? '–'}</td>
                </tr>
                {open === a.id && (
                  <tr><td colSpan={6} style={{ background: 'var(--surface-2)' }}>
                    <pre className="mono" style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{JSON.stringify({ request_id: a.request_id, ...a.details }, null, 2)}</pre>
                  </td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="row" style={{ justifyContent: 'flex-end', marginTop: 12 }}>
          <button className="btn btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
          <span className="muted">Page {page} / {pages}</span>
          <button className="btn btn-sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      )}
    </>
  );
}
