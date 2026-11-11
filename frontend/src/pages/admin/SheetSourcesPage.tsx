import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { SheetSource, SheetSourcesPayload } from '../../api/types';
import { ErrorAlert, useToast } from '../../components/Feedback';
import { Icon } from '../../components/Icon';
import { fmtDateTime, fmtInt, relTime } from '../../lib/format';

const STATUS_CLASS: Record<string, string> = { ok: 'badge-pos', failed: 'badge-neg', skipped: 'badge-accent', never: '' };
const METHOD_TEXT: Record<string, string> = { csv: 'public CSV export', api: 'Sheets API (service account)', upload: 'uploaded workbook' };

function SourceCard({ s, busy, onRefresh }: { s: SheetSource; busy: boolean; onRefresh: (code: string) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const file = useRef<HTMLInputElement>(null);
  const upload = useMutation({
    mutationFn: (f: File) => {
      const fd = new FormData();
      fd.append('code', s.code);
      fd.append('file', f);
      return api.upload<{ tabs: Record<string, number> }>('/admin/sheets/upload', fd);
    },
    onSuccess: (r) => {
      toast(`Workbook stored (${Object.entries(r.tabs).map(([k, n]) => `${k}: ${fmtInt(n)} rows`).join(', ')}) — refresh queued.`);
      qc.invalidateQueries({ queryKey: ['sheet-sources'] });
    },
    onError: (e) => toast((e as Error).message),
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/admin/sheets/upload/${s.code}`),
    onSuccess: () => { toast('Uploaded workbook removed.'); qc.invalidateQueries({ queryKey: ['sheet-sources'] }); },
  });
  return (
    <article className="card" style={{ padding: 16 }}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0 }}>
          <div className="row" style={{ gap: 8 }}>
            <h3 style={{ margin: 0 }}>{s.title}</h3>
            <span className={`badge ${STATUS_CLASS[s.status] ?? ''}`}>{s.status === 'never' ? 'not loaded' : s.status}</span>
            <span className="badge">{s.access === 'public' ? 'link-shared' : 'private'}</span>
          </div>
          <p className="muted" style={{ fontSize: 12.5, margin: '4px 0 0' }}>
            Feeds <b>{s.feeds}</b> · tabs {s.tabs.map((t) => t.title).join(', ')} · {s.series_defined} series ·{' '}
            <a href={s.url} target="_blank" rel="noreferrer noopener">open sheet <Icon name="external" size={12} /></a>
          </p>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn btn-sm" disabled={busy} onClick={() => onRefresh(s.code)}><Icon name="refresh" size={14} /> Refresh</button>
          <button className="btn btn-sm" disabled={upload.isPending} onClick={() => file.current?.click()}>
            <Icon name="upload" size={14} /> {upload.isPending ? 'Uploading…' : 'Upload .xlsx'}</button>
          <input ref={file} type="file" accept=".xlsx,.xlsm" hidden onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload.mutate(f);
            e.target.value = '';
          }} />
        </div>
      </div>
      <div className="row" style={{ gap: 18, marginTop: 10, flexWrap: 'wrap', fontSize: 12.5 }}>
        <span><span className="muted">Last pull</span> {s.pulled_at ? `${relTime(s.pulled_at)} (${fmtDateTime(s.pulled_at)})` : '—'}</span>
        <span><span className="muted">Method</span> {s.method ? METHOD_TEXT[s.method] : '—'}</span>
        <span><span className="muted">Latest observation</span> {s.detail.last ?? '—'}</span>
        <span><span className="muted">Series loaded</span> {s.detail.series ?? '—'}</span>
      </div>
      {s.error && <div className={`alert ${s.status === 'skipped' ? 'alert-info' : 'alert-error'}`} style={{ marginTop: 10 }}>
        <Icon name="alert" size={16} /> {s.error}</div>}
      {s.upload && (
        <div className="row muted" style={{ gap: 8, marginTop: 10, fontSize: 12.5 }}>
          <Icon name="file" size={14} /> Fallback workbook: {s.upload.filename}, uploaded {fmtDateTime(s.upload.uploaded_at)}
          {s.upload.uploaded_by ? ` by ${s.upload.uploaded_by}` : ''} — used when the live read is not possible.
          <button className="btn btn-ghost btn-sm" onClick={() => window.confirm('Remove the uploaded workbook?') && remove.mutate()}>
            <Icon name="trash" size={14} /> Remove</button>
        </div>
      )}
      <ErrorAlert error={upload.error || remove.error} />
    </article>
  );
}

/**
 * Administration → Sheet sources: status of the four Google Sheet workbooks, the service-account key used for the
 * private ones (never shown back), manual refresh and the .xlsx upload fallback.
 */
export function SheetSourcesPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const [keyText, setKeyText] = useState('');
  const { data, error } = useQuery({
    queryKey: ['sheet-sources'],
    queryFn: () => api.get<SheetSourcesPayload>('/admin/sheets'),
    refetchInterval: (q) => (q.state.data?.job && ['queued', 'running'].includes(q.state.data.job.status) ? 3000 : 30000),
  });
  const refresh = useMutation({
    mutationFn: (codes: string[] | null) => api.post('/admin/sheets/refresh', { sources: codes }),
    onSuccess: () => { toast('Sheet refresh queued.'); qc.invalidateQueries({ queryKey: ['sheet-sources'] }); },
    onError: (e) => toast((e as Error).message),
  });
  const saveKey = useMutation({
    mutationFn: (key_json: string) => api.put<{ email: string }>('/admin/sheets/service-account', { key_json }),
    onSuccess: (r) => { setKeyText(''); toast(`Service account ${r.email} saved.`); qc.invalidateQueries({ queryKey: ['sheet-sources'] }); },
  });
  const dropKey = useMutation({
    mutationFn: () => api.del('/admin/sheets/service-account'),
    onSuccess: () => { toast('Service-account key removed.'); qc.invalidateQueries({ queryKey: ['sheet-sources'] }); },
  });
  const job = data?.job;
  const running = !!job && ['queued', 'running'].includes(job.status);
  const sa = data?.service_account;
  return (
    <>
      <div className="page-head">
        <div><h1>Sheet sources</h1>
          <p>Google Sheet workbooks behind Waste/Bio, Tradable Prem and the India/China parity views. Refreshed after every
            pipeline run and on the sheets schedule.</p></div>
        <div className="row" style={{ gap: 8 }}>
          {job && <span className="chip"><span className="dot" style={{ background: job.status === 'failed' ? 'var(--neg)' : undefined }} />
            {running ? 'Refreshing…' : `Last refresh ${job.status} · ${fmtDateTime(job.finished_at ?? job.queued_at)}`}</span>}
          <button className="btn btn-primary" disabled={running || refresh.isPending} onClick={() => refresh.mutate(null)}>
            <Icon name="refresh" size={15} /> Refresh all</button>
        </div>
      </div>
      <ErrorAlert error={error || refresh.error} />
      <article className="card" style={{ padding: 16, marginBottom: 16 }}>
        <h3 style={{ margin: '0 0 4px' }}>Google service account (private sheets)</h3>
        {sa?.configured ? (
          <div className="row" style={{ gap: 10, flexWrap: 'wrap' }}>
            <span className="badge badge-pos">configured</span>
            <span>Share the private sheets as <b>Viewer</b> with <code className="mono">{sa.email}</code></span>
            <span className="muted" style={{ fontSize: 12 }}>({sa.origin === 'file' ? 'from GOOGLE_SERVICE_ACCOUNT_FILE' : 'stored encrypted'})</span>
            {sa.origin === 'admin' && <button className="btn btn-ghost btn-sm" onClick={() => window.confirm('Remove the service-account key? Private sheets will stop refreshing.') && dropKey.mutate()}>
              <Icon name="trash" size={14} /> Remove key</button>}
          </div>
        ) : (
          <>
            <p className="muted" style={{ fontSize: 12.5, margin: '0 0 8px' }}>
              Paste the JSON key of a Google Cloud service account with the Sheets API enabled, then share the Argus and KPN Market
              Price workbooks with its e-mail as Viewer. The key is stored encrypted and never shown again.
            </p>
            <textarea className="input mono" rows={4} value={keyText} onChange={(e) => setKeyText(e.target.value)}
                      placeholder='{"type": "service_account", "client_email": "…", "private_key": "…"}'
                      style={{ width: '100%', fontSize: 12, height: 'auto' }} spellCheck={false} autoComplete="off" />
            <div className="row" style={{ gap: 8, marginTop: 8 }}>
              <button className="btn btn-primary btn-sm" disabled={keyText.trim().length < 50 || saveKey.isPending}
                      onClick={() => saveKey.mutate(keyText.trim())}><Icon name="key" size={14} /> Save key</button>
              <label className="btn btn-sm"><Icon name="upload" size={14} /> Load from file
                <input type="file" accept=".json,application/json" hidden onChange={async (e) => {
                  const f = e.target.files?.[0];
                  if (f) setKeyText(await f.text());
                  e.target.value = '';
                }} /></label>
            </div>
            <ErrorAlert error={saveKey.error} />
          </>
        )}
      </article>
      <div className="stack" style={{ gap: 12 }}>
        {!data && <div className="card skeleton" style={{ height: 300 }} />}
        {data?.sources.map((s) => <SourceCard key={s.code} s={s} busy={running || refresh.isPending}
                                               onRefresh={(code) => refresh.mutate([code])} />)}
      </div>
    </>
  );
}
