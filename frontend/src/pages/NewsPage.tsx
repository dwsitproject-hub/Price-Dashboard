import { useMemo, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, qs } from '../api/client';
import type { NewsDocument } from '../api/types';
import { Empty, ErrorAlert, useToast } from '../components/Feedback';
import { Icon } from '../components/Icon';
import { Lines } from '../components/intelCharts';
import { fmtDate, fmtDateTime } from '../lib/format';

interface DocsPayload {
  documents: NewsDocument[]; can_upload: boolean; can_delete: boolean; claude_configured: boolean;
  doc_types: string[]; geographies: string[]; products: string[]; max_mb: number;
}
interface Panel {
  lseg: { ric: string; name: string; unit: string; x: string[]; y: number[] }[];
  doc_prices: { price_type: string; x: string[]; y: number[]; unit: string[] }[];
  feed: { kind: 'price' | 'sentiment'; date: string; source: string; detail: string; sentiment?: string; raw_text: string | null }[];
  sentiment: { date: string; score: number }[];
  themes: [string, number][];
  expectations: { date: string; source: string; type: string; value: number; unit: string | null; tenor: string | null }[];
  snd: { item: string; value: number; unit: string; period: string }[];
}
const TYPE_LABEL: Record<string, string> = { broker_commentary: 'Broker commentary', oil_world: 'Oil World', energy: 'Energy / biofuels' };
const SENT_COLOR: Record<string, string> = { bullish: '#27ae60', bearish: '#e74c3c', neutral: '#7f8c8d' };
const LINE_COLORS = ['#0e6b5c', '#e3a21a', '#5b7db1', '#c0392b', '#9467bd', '#2bb39c'];
const stem = (f: string) => f.replace(/\.[^.]+$/, '').slice(0, 20);

function Documents({ d }: { d: DocsPayload }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [type, setType] = useState('auto');
  const [date, setDate] = useState('');
  const done = () => { qc.invalidateQueries({ queryKey: ['news-docs'] }); qc.invalidateQueries({ queryKey: ['news-panel'] }); };
  const upload = useMutation({
    mutationFn: () => {
      const f = new FormData();
      f.append('file', file as File);
      f.append('doc_type', type);
      if (date) f.append('doc_date', date);
      return api.upload<NewsDocument>('/intel/news/documents', f);
    },
    onSuccess: (doc) => { toast(`${doc.filename} queued for extraction.`); setFile(null); done(); },
  });
  const act = useMutation({
    mutationFn: ({ id, kind }: { id: string; kind: 'reprocess' | 'delete' }) =>
      kind === 'delete' ? api.del(`/intel/news/documents/${id}`) : api.post(`/intel/news/documents/${id}/reprocess`),
    onSuccess: done, onError: (e) => toast((e as ApiError).message),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (file) upload.mutate(); };
  return (
    <details className="card card-pad" style={{ marginBottom: 16 }} open={!d.documents.length}>
      <summary style={{ cursor: 'pointer', fontWeight: 650 }}>Documents · {d.documents.length}</summary>
      <p className="muted" style={{ fontSize: 12.5 }}>
        Broker commentary, Oil World and energy/biofuel reports (PDF, Word or Excel, up to {d.max_mb} MB). Claude extracts
        prices and sentiment; the document text is sent to Anthropic for that. The date and type are taken from the file name
        when it contains them (e.g. <span className="mono">broker_comment_2026-04-07.pdf</span>).
      </p>
      {d.can_upload && d.claude_configured && (
        <form className="row" style={{ alignItems: 'flex-end', marginBottom: 12 }} onSubmit={submit}>
          <div className="control"><label className="label" htmlFor="doc-file">Document</label>
            <input id="doc-file" type="file" accept=".pdf,.docx,.xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
          <div className="control"><label className="label" htmlFor="doc-type">Type</label>
            <select id="doc-type" className="select" value={type} onChange={(e) => setType(e.target.value)}>
              <option value="auto">Detect automatically</option>
              {d.doc_types.map((t) => <option key={t} value={t}>{TYPE_LABEL[t] ?? t}</option>)}
            </select></div>
          <div className="control"><label className="label" htmlFor="doc-date">Document date (optional)</label>
            <input id="doc-date" className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></div>
          <button className="btn btn-primary" disabled={!file || upload.isPending}>
            {upload.isPending ? <span className="spinner" /> : <><Icon name="upload" size={15} /> Upload</>}</button>
        </form>)}
      {d.can_upload && !d.claude_configured && <div className="alert alert-info"><Icon name="alert" size={16} /> Claude is not configured on this server, so documents cannot be processed yet.</div>}
      <ErrorAlert error={upload.error} />
      <div className="table-wrap">
        <table className="tbl compact">
          <thead><tr><th>File</th><th>Type</th><th>Date</th><th>Status</th><th className="r">Prices</th><th className="r">Signals</th><th>Uploaded</th><th /></tr></thead>
          <tbody>
            {d.documents.map((x) => (
              <tr key={x.id}>
                <td className="mono" style={{ fontSize: 12 }}>{x.filename}</td>
                <td>{x.doc_type ? TYPE_LABEL[x.doc_type] : <span className="faint">detecting…</span>}</td>
                <td>{fmtDate(x.doc_date)}</td>
                <td>{x.status === 'failed'
                  ? <span className="status-pill neg" title={x.error ?? ''}>Failed</span>
                  : x.status === 'succeeded' ? <span className="status-pill pos">Done</span>
                    : <span className="row" style={{ gap: 6 }}><span className="spinner" /> {x.status}</span>}
                  {x.status === 'failed' && x.error && <div className="faint" style={{ fontSize: 11.5, maxWidth: 320 }}>{x.error}</div>}</td>
                <td className="r num">{x.num_prices ?? '—'}</td><td className="r num">{x.num_sentiment ?? '—'}</td>
                <td className="faint" style={{ fontSize: 12 }}>{fmtDateTime(x.uploaded_at)} · {x.uploaded_by ?? '—'}</td>
                <td className="row" style={{ gap: 4, justifyContent: 'flex-end' }}>
                  {d.can_upload && ['failed', 'succeeded'].includes(x.status) && (
                    <button className="btn btn-ghost btn-sm icon-btn" title="Process again" aria-label={`Process ${x.filename} again`}
                            onClick={() => act.mutate({ id: x.id, kind: 'reprocess' })}><Icon name="refresh" size={14} /></button>)}
                  {d.can_delete && (
                    <button className="btn btn-ghost btn-sm icon-btn" title="Delete" aria-label={`Delete ${x.filename}`}
                            onClick={() => window.confirm(`Delete ${x.filename} and everything extracted from it?`) && act.mutate({ id: x.id, kind: 'delete' })}>
                      <Icon name="trash" size={14} /></button>)}
                </td>
              </tr>
            ))}
            {!d.documents.length && <tr><td colSpan={8} className="faint">No documents yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function PanelView({ p, geo, product }: { p: Panel; geo: string; product: string }) {
  const byUnit = useMemo(() => {
    const m = new Map<string, Panel['lseg']>();
    p.lseg.forEach((s) => m.set(s.unit, [...(m.get(s.unit) ?? []), s]));
    return [...m.entries()];
  }, [p.lseg]);
  const avg = p.sentiment.length ? p.sentiment[p.sentiment.length - 1].score : null;
  const tone = avg === null ? null : avg > 0.2 ? 'bullish' : avg < -0.2 ? 'bearish' : 'neutral';
  return (
    <div className="stack">
      <section>
        <h4 className="news-h">Price Trend</h4>
        {byUnit.length ? (
          <div className="chart-grid">
            {byUnit.map(([unit, ss]) => (
              <article key={unit} className="card chart-card"><header className="card-head"><div className="card-title">{geo} — {product} · {unit}</div></header>
                <div className="chart-body"><Lines unit={unit} series={ss.map((s, i) => ({ name: s.name, x: s.x, y: s.y, color: LINE_COLORS[i % 6] }))} /></div></article>
            ))}
          </div>
        ) : p.doc_prices.length ? (
          <div className="card chart-card"><header className="card-head"><div className="card-title">Prices extracted from documents</div>
            <div className="card-sub">mixed units — see Market Expectation</div></header>
            <div className="chart-body"><Lines unit="" series={p.doc_prices.map((s, i) => ({ name: s.price_type, x: s.x, y: s.y, color: LINE_COLORS[i % 6] }))} /></div></div>
        ) : <div className="card"><Empty title={`No LSEG data or document-extracted prices for ${geo} + ${product}.`} /></div>}
      </section>
      <div className="grid-2">
        <section>
          <h4 className="news-h">Recent News</h4>
          <div className="card card-pad stack" style={{ gap: 10 }}>
            {p.feed.length ? p.feed.map((f, i) => (
              <div key={i}>
                <span className="badge" style={{ background: f.kind === 'price' ? '#3498db' : '#8e44ad', color: '#fff' }}>{stem(f.source)}, {f.date.slice(5)}</span>{' '}
                <b style={{ color: f.sentiment ? SENT_COLOR[f.sentiment] : undefined }}>{f.detail}</b>
                {f.raw_text && <details><summary className="faint" style={{ fontSize: 12, cursor: 'pointer' }}>Show excerpt</summary>
                  <blockquote className="muted" style={{ margin: '4px 0 0 8px', fontSize: 12.5 }}>{f.raw_text}</blockquote></details>}
              </div>
            )) : <Empty title="No document extractions yet." icon="file">Upload documents above.</Empty>}
          </div>
        </section>
        <section>
          <h4 className="news-h">Sentiment</h4>
          <div className="card card-pad">
            {p.sentiment.length >= 2 ? (
              <Lines unit="score" height={240} series={[{ name: 'Sentiment', x: p.sentiment.map((s) => s.date), y: p.sentiment.map((s) => s.score),
                color: '#0e6b5c', fill: 'tozeroy', fillcolor: 'rgba(14,128,110,.12)' }]} />
            ) : tone ? (
              <div style={{ fontSize: 18, fontWeight: 650, color: SENT_COLOR[tone] }}>Current: {tone[0].toUpperCase() + tone.slice(1)} (score: {avg! >= 0 ? '+' : ''}{avg!.toFixed(2)})</div>
            ) : <Empty title="No sentiment data available yet." />}
            {p.themes.length > 0 && <div className="row" style={{ marginTop: 10, gap: 6 }}>
              {p.themes.map(([t, n]) => <span key={t} className="chip">{t} ({n})</span>)}</div>}
          </div>
        </section>
      </div>
      <div className="grid-2">
        <section>
          <h4 className="news-h">Market Expectation</h4>
          <div className="card table-wrap">
            {p.expectations.length ? (
              <table className="tbl compact"><thead><tr><th>Source</th><th>Type</th><th className="r">Value</th><th>Unit</th><th>Tenor</th></tr></thead>
                <tbody>{p.expectations.map((e, i) => (
                  <tr key={i}><td><span className="badge badge-accent">{stem(e.source)}, {e.date}</span></td><td>{e.type}</td>
                    <td className="r num">{e.value.toLocaleString()}</td><td>{e.unit}</td><td>{e.tenor ?? '—'}</td></tr>))}</tbody></table>
            ) : <Empty title="No forecasts or targets extracted yet." />}
          </div>
        </section>
        <section>
          <h4 className="news-h">SND Snapshot</h4>
          <div className="card table-wrap">
            {p.snd.length ? (
              <table className="tbl compact"><thead><tr><th>Item</th><th className="r">Latest</th><th>Month</th></tr></thead>
                <tbody>{p.snd.map((s) => <tr key={s.item}><td>{s.item}</td><td className="r num">{Math.round(s.value).toLocaleString()} {s.unit}</td><td>{s.period}</td></tr>)}</tbody></table>
            ) : <Empty title={`SND data not yet available for ${geo} + ${product}.`} />}
          </div>
        </section>
      </div>
    </div>
  );
}

export function NewsPage() {
  const [geo, setGeo] = useState('Asia');
  const [product, setProduct] = useState('Palm');
  const { data: docs } = useQuery({
    queryKey: ['news-docs'], queryFn: () => api.get<DocsPayload>('/intel/news/documents'),
    refetchInterval: (q) => (q.state.data?.documents.some((d) => d.status === 'queued' || d.status === 'processing') ? 4000 : false),
  });
  const busy = docs?.documents.some((d) => d.status === 'queued' || d.status === 'processing');
  const { data: panel, error, isFetching } = useQuery({
    queryKey: ['news-panel', geo, product, busy],
    queryFn: ({ signal }) => api.get<Panel>(`/intel/news/panel${qs({ geography: geo, product })}`, signal),
  });
  return (
    <>
      <div className="page-head"><div><h1>News</h1>
        <p>Prices and sentiment from broker, Oil World and energy reports, next to LSEG prices.</p></div>
        {isFetching && <span className="spinner" />}</div>
      {docs && <Documents d={docs} />}
      <div className="card controls">
        <div className="control"><label className="label" htmlFor="n-geo">Geography</label>
          <select id="n-geo" className="select" value={geo} onChange={(e) => setGeo(e.target.value)}>
            {(docs?.geographies ?? ['Asia']).map((g) => <option key={g}>{g}</option>)}</select></div>
        <div className="control"><label className="label" htmlFor="n-prod">Product</label>
          <select id="n-prod" className="select" value={product} onChange={(e) => setProduct(e.target.value)}>
            {(docs?.products ?? ['Palm']).map((g) => <option key={g}>{g}</option>)}</select></div>
      </div>
      <ErrorAlert error={error} />
      {panel ? <PanelView p={panel} geo={geo} product={product} /> : <div className="card skeleton" style={{ height: 380 }} />}
    </>
  );
}
