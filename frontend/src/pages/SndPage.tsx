import { useMemo, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../api/client';
import type { SndCountry, SndSource, SndTable } from '../api/types';
import { Empty, ErrorAlert, useToast } from '../components/Feedback';
import { Icon } from '../components/Icon';
import { SndChart } from '../components/intelCharts';
import { fmtDate } from '../lib/format';

export type SndView = 'graphs' | 'exports' | 'tables';

const ROLE_LABEL: Record<string, string> = {
  malaysia_history: 'Malaysia history (UOB Excel)', malaysia: 'Malaysia (year sheet)',
  indonesia: 'Indonesia (year sheet)', india: 'India (year sheet)', exports: 'Malindo exports (year sheet)',
};

function Sources() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({ queryKey: ['snd-sources'], queryFn: () => api.get<{ sources: SndSource[]; can_upload: boolean }>('/intel/snd/sources') });
  const [role, setRole] = useState('malaysia');
  const [year, setYear] = useState(String(new Date().getFullYear()));
  const [sheet, setSheet] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<unknown>(null);
  const refresh = () => ['snd-sources', 'snd-graphs', 'snd-tables', 'snd-exports'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const upload = useMutation({
    mutationFn: () => {
      const f = new FormData();
      f.append('file', file as File);
      f.append('role', role);
      f.append('year', role === 'malaysia_history' ? '0' : year);
      if (sheet.trim()) f.append('sheet', sheet.trim());
      return api.upload<{ rows: number }>('/intel/snd/sources', f);
    },
    onSuccess: (r) => { toast(`Uploaded — ${r.rows} rows read.`); setFile(null); setError(null); refresh(); },
    onError: setError,
  });
  const remove = useMutation({
    mutationFn: (s: SndSource) => api.del(`/intel/snd/sources/${s.role}/${s.year}`),
    onSuccess: () => { toast('Source removed.'); refresh(); },
    onError: (e) => toast((e as ApiError).message),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (file) upload.mutate(); };
  return (
    <details className="card card-pad" style={{ marginBottom: 16 }}>
      <summary style={{ cursor: 'pointer', fontWeight: 650 }}>
        Data sources · {data?.sources.length ?? 0} uploaded
      </summary>
      <p className="muted" style={{ fontSize: 12.5 }}>
        The UOB and Google-Sheet sources are uploaded here (the sheets are no longer public). For a Google Sheet tab
        use <b>File → Download → CSV</b> — it keeps the sheet's number formatting for the tables.
      </p>
      <div className="table-wrap">
        <table className="tbl compact">
          <thead><tr><th>Source</th><th>Year</th><th>File</th><th className="r">Rows</th><th>Uploaded</th><th /></tr></thead>
          <tbody>
            {data?.sources.map((s) => (
              <tr key={`${s.role}-${s.year}`}>
                <td>{ROLE_LABEL[s.role] ?? s.role}</td><td>{s.year || '—'}</td>
                <td className="mono" style={{ fontSize: 12 }}>{s.filename}{s.sheet ? ` · ${s.sheet}` : ''}</td>
                <td className="r num">{s.rows}</td>
                <td className="faint" style={{ fontSize: 12 }}>{fmtDate(s.uploaded_at)} · {s.uploaded_by ?? '—'}</td>
                <td>{data.can_upload && (
                  <button className="btn btn-ghost btn-sm icon-btn" title="Remove" aria-label={`Remove ${s.filename}`}
                          onClick={() => window.confirm(`Remove ${s.filename}?`) && remove.mutate(s)}>
                    <Icon name="trash" size={15} /></button>)}</td>
              </tr>
            ))}
            {data && !data.sources.length && <tr><td colSpan={6} className="faint">Nothing uploaded yet.</td></tr>}
          </tbody>
        </table>
      </div>
      {data?.can_upload && (
        <form className="row" style={{ marginTop: 12, alignItems: 'flex-end' }} onSubmit={submit}>
          <div className="control"><label className="label" htmlFor="snd-role">Source</label>
            <select id="snd-role" className="select" value={role} onChange={(e) => setRole(e.target.value)}>
              {Object.entries(ROLE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select></div>
          {role !== 'malaysia_history' && (
            <div className="control"><label className="label" htmlFor="snd-year">Year</label>
              <input id="snd-year" className="input" type="number" min={2000} max={2100} value={year}
                     onChange={(e) => setYear(e.target.value)} style={{ width: 96 }} /></div>)}
          <div className="control"><label className="label" htmlFor="snd-sheet">Excel sheet (optional)</label>
            <input id="snd-sheet" className="input" value={sheet} placeholder={role === 'malaysia_history' ? 'Sheet1' : 'first sheet'}
                   onChange={(e) => setSheet(e.target.value)} style={{ width: 140 }} /></div>
          <div className="control"><label className="label" htmlFor="snd-file">File (.csv or .xlsx)</label>
            <input id="snd-file" type="file" accept=".csv,.xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
          <button className="btn btn-primary" disabled={!file || upload.isPending}>
            {upload.isPending ? <span className="spinner" /> : <><Icon name="upload" size={15} /> Upload</>}
          </button>
        </form>
      )}
      <ErrorAlert error={error} />
    </details>
  );
}

function Graphs() {
  const [years, setYears] = useState<number[] | null>(null);
  const { data, error, isLoading } = useQuery({
    queryKey: ['snd-graphs'], queryFn: () => api.get<{ countries: SndCountry[]; years: number[] }>('/intel/snd/graphs'),
  });
  const sel = years ?? (data?.years.slice(-3) ?? []);
  const toggle = (y: number) => setYears((cur) => {
    const base = cur ?? sel;
    const next = base.includes(y) ? base.filter((x) => x !== y) : [...base, y];
    return next.length ? next.sort() : base;
  });
  if (isLoading) return <div className="chart-grid">{[0, 1, 2].map((i) => <div key={i} className="card skeleton" style={{ height: 330 }} />)}</div>;
  return (
    <>
      <ErrorAlert error={error} />
      {data && data.years.length > 0 && (
        <div className="card controls">
          <div className="control"><span className="label">Years</span>
            <div className="years" role="group" aria-label="Years">
              {[...data.years].reverse().slice(0, 12).map((y) => (
                <button key={y} type="button" className="year-chip" aria-pressed={sel.includes(y)} onClick={() => toggle(y)}>{y}</button>
              ))}
            </div></div>
        </div>
      )}
      {data?.countries.map((c) => (
        <section key={c.name} style={{ marginBottom: 24 }}>
          <h3 style={{ margin: '8px 0 12px' }}>{c.name}</h3>
          {!c.items.length ? (
            <div className="card"><Empty title="No data" icon="layers">No {c.name} source uploaded yet.</Empty></div>
          ) : (
            <div className="chart-grid">
              {c.items.map((it) => (
                <article key={it.name} className="card chart-card">
                  <header className="card-head"><div><div className="card-title">{c.name} — {it.name}</div>
                    <div className="card-sub">{it.unit}</div></div></header>
                  <div className="chart-body">
                    {it.traces.some((t) => t.year && sel.includes(t.year))
                      ? <SndChart traces={it.traces.filter((t) => sel.includes(t.year))} unit={it.unit} />
                      : <Empty title="No data">No observations in the selected years.</Empty>}
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      ))}
    </>
  );
}

const HIGHLIGHT = new Set(['Annual', 'YoY%', 'YoY %', 'Prior Yr']);
const QUARTERS = new Set(['Q1', 'Q2', 'Q3', 'Q4']);
const REGIONS = new Set(['EU 27', 'Oth. Europe', 'C.I.S.', 'Africa', 'America', 'Asia', 'Oceania', 'Other']);
const MONTH_NO: Record<string, number> = { Jan: 1, Feb: 2, Mar: 3, Apr: 4, May: 5, Jun: 6, Jul: 7, Aug: 8, Sep: 9, Oct: 10, Nov: 11, Dec: 12 };

function rowKind(label: string): string {
  if (!label.trim()) return 'blank';
  if (label.includes('▶')) return 'section';
  if (/^[✓✦]/.test(label.trim())) return 'note';
  if (label.includes('Month-on-Month') || label.trim().endsWith('%')) return 'mom';
  if (/Total (Supply|Offtake|Demand|Malindo)/.test(label) || label.trim() === 'TOTAL') return 'total';
  if (REGIONS.has(label.trim())) return 'region';
  return 'regular';
}

/** Legacy _render_snd_table: sheet text as-is, with the legacy row/column classes and the BMD row. */
function SndTableView({ t }: { t: SndTable & { headers: string[]; rows: string[][] } }) {
  const ncol = t.headers.length;
  const isEndStock = (l: string) => (l.includes('End Stock') || l.includes('Ending Stock'))
    && /^(8\.|9\.|End|Ending)/.test(l.trimStart());
  const colStyle = (h: string, head: boolean) => (HIGHLIGHT.has(h) ? (head ? { background: '#2c3e50', color: '#fff' } : { fontWeight: 650 })
    : QUARTERS.has(h) ? (head ? { background: '#d7bde2', color: '#4a235a' } : { background: 'color-mix(in srgb, #d7bde2 18%, transparent)' }) : {});
  return (
    <section style={{ marginBottom: 32 }}>
      <h3 style={{ marginBottom: 2 }}>{t.title} — Supply &amp; Demand Balance Sheet</h3>
      <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>All figures in &apos;000 Metric Tonnes</div>
      <div className="card table-wrap">
        <table className="tbl compact snd-table">
          <thead><tr>{t.headers.map((h, i) => <th key={i} className={i ? 'r' : ''} style={colStyle(h, true)}>{h}</th>)}</tr></thead>
          <tbody>
            {t.rows.flatMap((r, ri) => {
              const label = r[0] ?? '';
              const kind = rowKind(label);
              const out = [];
              if (kind === 'blank') out.push(<tr key={ri}><td colSpan={ncol} style={{ padding: 2, border: 0 }} /></tr>);
              else if (kind === 'section') out.push(<tr key={ri}><td colSpan={ncol} className="snd-section">{label.replace('▶', '').trim()}</td></tr>);
              else if (kind === 'note') out.push(<tr key={ri}><td colSpan={ncol} className="faint" style={{ fontStyle: 'italic' }}>{label}</td></tr>);
              else out.push(
                <tr key={ri} className={`snd-${kind}`}>
                  {r.map((c, ci) => {
                    const neg = /^[-(]/.test(c.trim());
                    const color = kind === 'mom' && ci > 0 && c.trim() ? (neg ? 'var(--neg)' : /^[MI]/.test(c.trim()) ? undefined : 'var(--pos)') : undefined;
                    return <td key={ci} className={ci ? 'r num' : label.startsWith('  ') ? 'snd-sub' : ''}
                               style={{ ...colStyle(t.headers[ci] ?? '', false), color }}>{c}</td>;
                  })}
                </tr>);
              if (t.bmd && isEndStock(label) && Object.keys(t.bmd.months).length) {
                out.push(
                  <tr key={`${ri}-bmd`} className="snd-bmd">
                    <td>BMD (FCPO)</td>
                    {t.headers.slice(1).map((h, ci) => {
                      const m = MONTH_NO[h];
                      const p = m ? t.bmd!.months[String(m)] : undefined;
                      return <td key={ci} className="r num">{p ? Math.round(p).toLocaleString() : ''}</td>;
                    })}
                  </tr>,
                  <tr key={`${ri}-bmd-note`}><td colSpan={ncol} className="faint" style={{ fontSize: 11.5 }}>
                    BMD settlement prices (MYR/MT) as of {t.bmd.as_of ? fmtDate(t.bmd.as_of) : '—'}</td></tr>);
              }
              return out;
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Tables() {
  const { data, error, isLoading } = useQuery({ queryKey: ['snd-tables'], queryFn: () => api.get<{ tables: SndTable[] }>('/intel/snd/tables') });
  if (isLoading) return <div className="card skeleton" style={{ height: 400 }} />;
  return (
    <>
      <ErrorAlert error={error} />
      {data?.tables.map((t) => (t.missing || !t.headers
        ? <div key={t.code} className="card" style={{ marginBottom: 16 }}><Empty title={`${t.title}`} icon="layers">No year sheet uploaded yet.</Empty></div>
        : <SndTableView key={t.code} t={t as SndTable & { headers: string[]; rows: string[][] }} />))}
    </>
  );
}

function Exports() {
  const { data, error, isLoading } = useQuery({ queryKey: ['snd-exports'], queryFn: () => api.get<{ table: SndTable | null }>('/intel/snd/exports') });
  if (isLoading) return <div className="card skeleton" style={{ height: 400 }} />;
  return (
    <>
      <ErrorAlert error={error} />
      {data?.table?.headers
        ? <SndTableView t={{ ...data.table, title: data.table.title.replace(' — By Destination', ''), bmd: null } as SndTable & { headers: string[]; rows: string[][] }} />
        : <div className="card"><Empty title="No exports sheet" icon="layers">Upload the Malindo exports tab under Data sources.</Empty></div>}
    </>
  );
}

export function SndPage({ view }: { view: SndView }) {
  const title = useMemo(() => ({ graphs: 'Graphs', exports: 'Exports by destination', tables: 'Balance sheets' })[view], [view]);
  return (
    <>
      <div className="page-head"><div><h1>SND · {title}</h1>
        <p>Palm-oil supply &amp; demand — Malaysia (UOB history + year sheets), Indonesia and India.</p></div></div>
      <Sources />
      {view === 'graphs' ? <Graphs /> : view === 'tables' ? <Tables /> : <Exports />}
    </>
  );
}

