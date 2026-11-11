import { useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { AiReport } from '../api/types';
import { Empty, ErrorAlert } from '../components/Feedback';
import { Icon } from '../components/Icon';
import { Bars, Lines, SIGNAL_COLORS, XYLines } from '../components/intelCharts';
import { fmtDateTime } from '../lib/format';

interface Groups { groups: { group: string; options: { key: string; label: string }[] }[]; timeframes: string[]; claude_configured: boolean }
const TF_LABEL: Record<string, string> = { '1M': '1 Month', '3M': '3 Months', '6M': '6 Months', '1Y': '1 Year', '3Y': '3 Years', '5Y': '5 Years', All: 'All Available Data' };
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <article className="card chart-card">
      <header className="card-head"><div className="card-title">{title}</div></header>
      <div className="chart-body">{children}</div>
    </article>
  );
}

/** The seven legacy charts (ai_analysis.build_figures), drawn from the report's sections. */
function Charts({ r }: { r: AiReport }) {
  const s = r.sections;
  const ch = s.chart;
  const hist = useMemo(() => {        // legacy: 40-bin histogram of the window
    const v: number[] = ch.values.filter((x: number | null) => x !== null);
    const lo = Math.min(...v), hi = Math.max(...v), n = 40, w = (hi - lo) / n || 1;
    const counts = new Array(n).fill(0);
    v.forEach((x) => { counts[Math.min(n - 1, Math.floor((x - lo) / w))] += 1; });
    return { x: counts.map((_, i) => +(lo + w * (i + 0.5)).toFixed(4)), y: counts, w };
  }, [ch.values]);
  const se = s.seasonal, ex = s.extremes, sr = s.support_resistance, mo = s.momentum, oi = s.oi;
  const levelShapes = useMemo(() => [...(sr.support_levels ?? []).map((y: number) => ({ y, c: '#2ca02c' })),
    ...(sr.resistance_levels ?? []).map((y: number) => ({ y, c: '#d62728' }))].map(({ y, c }) => ({
    type: 'line', xref: 'paper', x0: 0, x1: 1, y0: y, y1: y, line: { color: c, width: 1.2, dash: 'dash' } })), [sr]);
  const resColors = ['#e3a21a', '#5b7db1', '#2bb39c', '#9467bd'];
  const unit = r.instrument_label.match(/\(([^)]+)\)$/)?.[1] ?? '';
  return (
    <div className="chart-grid">
      <Card title={`1. Seasonal Pattern${se.pct_deviation !== null ? ` (${se.pct_deviation >= 0 ? '+' : ''}${se.pct_deviation}% vs avg)` : ''}`}>
        <Bars x={MONTHS.filter((m) => m in se.monthly_averages)} unit={unit}
              y={MONTHS.filter((m) => m in se.monthly_averages).map((m) => se.monthly_averages[m])}
              colors={MONTHS.filter((m) => m in se.monthly_averages).map((m) => (m === se.current_month ? '#0b4f45' : '#9cc9bf'))}
              extra={[{ type: 'scatter', mode: 'markers', x: [se.current_month], y: [se.current_value], name: 'Current',
                        marker: { symbol: 'diamond', size: 12, color: '#d62728' } }]} />
      </Card>
      <Card title={`2. Extreme Values (Z=${ex.z_score}, P${ex.percentile_rank})`}>
        <Bars x={hist.x} y={hist.y} colors="#5b7db1" unit="count"
              extra={[{ type: 'scatter', mode: 'lines', x: [ex.current_value, ex.current_value], y: [0, Math.max(...hist.y)],
                        name: 'Current', line: { color: '#d62728', dash: 'dash', width: 2 } }]} />
      </Card>
      <Card title="3. Highs & Lows">
        <Lines unit={unit} series={[
          { name: '20D high', x: ch.dates, y: ch.roll_high, color: 'rgba(14,128,110,.35)', width: 1 },
          { name: '20D low', x: ch.dates, y: ch.roll_low, color: 'rgba(14,128,110,.35)', width: 1, fill: 'tonexty', fillcolor: 'rgba(14,128,110,.10)' },
          { name: 'Price', x: ch.dates, y: ch.values, color: '#0e6b5c', width: 1.8 }]} />
      </Card>
      <Card title="4. OI Buildup">
        {'note' in oi ? <Empty title="OI data not available for this instrument." />
          : <Bars x={['15D', '30D']} y={[null, null]} unit="days" barmode="stack" extra={Object.keys(SIGNAL_COLORS).map((sig) => ({
              type: 'bar', name: sig, x: ['15D', '30D'], y: [oi.signal_15d[sig], oi.signal_30d[sig]], marker: { color: SIGNAL_COLORS[sig] } }))} />}
      </Card>
      <Card title="5. Support & Resistance">
        <Lines unit={unit} shapes={levelShapes} series={[{ name: 'Price', x: ch.dates, y: ch.values, color: '#0e6b5c', width: 1.6 }]} />
      </Card>
      <Card title="6. Year Resemblance (% from first value of year)">
        <XYLines xTitle="Day of year" unit="%" series={ch.resemblance.map((t: { year: number; x: number[]; y: (number | null)[] }, i: number) => ({
          name: i === 0 ? `${t.year} (current)` : String(t.year), x: t.x, y: t.y, color: resColors[i % 4], width: i === 0 ? 2.6 : 1.3 }))} />
      </Card>
      <Card title={`7. Momentum & Trend — ${mo.trend}`}>
        <Lines unit={unit} series={[{ name: 'Price', x: ch.dates, y: ch.values, color: '#0e6b5c', width: 1.6 },
          { name: 'SMA20', x: ch.dates, y: ch.sma20, color: '#e3a21a', width: 1.4 },
          { name: 'SMA60', x: ch.dates, y: ch.sma60, color: '#c0392b', width: 1.2 }]} />
      </Card>
    </div>
  );
}

function ReportView({ id }: { id: string }) {
  const { data: r, error } = useQuery({
    queryKey: ['ai-report', id], queryFn: () => api.get<AiReport>(`/intel/ai/reports/${id}`),
    refetchInterval: (q) => (q.state.data?.status === 'pending' ? 3000 : false),
  });
  if (error) return <ErrorAlert error={error} />;
  if (!r) return <div className="card skeleton" style={{ height: 380 }} />;
  return (
    <div className="stack">
      <div className="row"><h3 style={{ margin: 0 }}>{r.instrument_label}</h3>
        <span className="badge">{TF_LABEL[r.timeframe] ?? r.timeframe}</span>
        <span className="faint" style={{ fontSize: 12 }}>{fmtDateTime(r.created_at)}</span></div>
      <Charts r={r} />
      <details className="card card-pad" open>
        <summary style={{ cursor: 'pointer', fontWeight: 650 }}>AI Narrative Report</summary>
        {r.status === 'pending' && <div className="row muted" style={{ marginTop: 10 }}><span className="spinner" /> Claude is writing the narrative…</div>}
        {r.status === 'skipped' && <p className="muted" style={{ fontStyle: 'italic' }}>AI narration unavailable — {r.error ?? 'Claude is not configured.'}</p>}
        {r.status === 'failed' && <div className="alert alert-error" role="alert"><Icon name="alert" size={16} /> {r.error}</div>}
        {r.status === 'ready' && r.narrative && <div className="markdown"><ReactMarkdown>{r.narrative}</ReactMarkdown></div>}
        {r.model && <div className="faint" style={{ fontSize: 11.5 }}>Written by {r.model}. Check figures against the charts before acting on them.</div>}
      </details>
      <details className="card card-pad">
        <summary style={{ cursor: 'pointer', fontWeight: 650 }}>Raw Statistics</summary>
        <pre className="mono" style={{ whiteSpace: 'pre-wrap', fontSize: 12, maxHeight: '40vh', overflow: 'auto' }}>{r.raw_text}</pre>
      </details>
    </div>
  );
}

export function AiPage() {
  const qc = useQueryClient();
  const { data: opts } = useQuery({ queryKey: ['ai-instruments'], queryFn: () => api.get<Groups>('/intel/ai/instruments') });
  const { data: recent } = useQuery({ queryKey: ['ai-recent'], queryFn: () => api.get<{ reports: { id: string; instrument_label: string; timeframe: string; status: string; created_at: string; requested_by: string | null }[] }>('/intel/ai/reports') });
  const [instrument, setInstrument] = useState('');
  const [timeframe, setTimeframe] = useState('1Y');
  const [current, setCurrent] = useState<string | null>(null);
  const run = useMutation({
    mutationFn: () => api.post<AiReport>('/intel/ai/reports', { instrument, timeframe }),
    onSuccess: (r) => { qc.setQueryData(['ai-report', r.id], r); setCurrent(r.id); qc.invalidateQueries({ queryKey: ['ai-recent'] }); },
  });
  return (
    <>
      <div className="page-head"><div><h1>AI Analysis</h1>
        <p>Seven quantitative sections for any price or spread, with a narrative written by Claude.</p></div></div>
      {opts && !opts.claude_configured && (
        <div className="alert alert-info" role="status"><Icon name="alert" size={16} /> Claude is not configured on this
          server, so reports show the computed sections without the narrative.</div>)}
      <div className="card controls">
        <div className="control"><label className="label" htmlFor="ai-inst">Instrument</label>
          <select id="ai-inst" className="select" value={instrument} onChange={(e) => setInstrument(e.target.value)} style={{ minWidth: 360 }}>
            <option value="">Select instrument…</option>
            {opts?.groups.map((g) => (
              <optgroup key={g.group} label={g.group}>
                {g.options.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
              </optgroup>
            ))}
          </select></div>
        <div className="control"><label className="label" htmlFor="ai-tf">Timeframe</label>
          <select id="ai-tf" className="select" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
            {(opts?.timeframes ?? Object.keys(TF_LABEL)).map((t) => <option key={t} value={t}>{TF_LABEL[t] ?? t}</option>)}
          </select></div>
        <button className="btn btn-primary" disabled={!instrument || run.isPending} onClick={() => run.mutate()}>
          {run.isPending ? <span className="spinner" /> : <><Icon name="sparkles" size={15} /> Run analysis</>}</button>
      </div>
      <ErrorAlert error={run.error} />
      {current ? <ReportView id={current} /> : (
        <div className="card"><Empty title="Pick an instrument and run an analysis" icon="sparkles">
          Seasonality, extremes, highs and lows, OI build-up, support/resistance, year resemblance and momentum.</Empty></div>)}
      {recent && recent.reports.length > 0 && (
        <div className="card table-wrap" style={{ marginTop: 20 }}>
          <table className="tbl compact">
            <thead><tr><th>Recent analyses</th><th>Timeframe</th><th>Status</th><th>When</th><th>By</th></tr></thead>
            <tbody>
              {recent.reports.map((x) => (
                <tr key={x.id} className="row-click" onClick={() => setCurrent(x.id)} aria-current={x.id === current}>
                  <td style={{ fontWeight: 600 }}>{x.instrument_label}</td><td>{TF_LABEL[x.timeframe] ?? x.timeframe}</td>
                  <td>{x.status}</td><td className="faint">{fmtDateTime(x.created_at)}</td><td className="faint">{x.requested_by ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>)}
    </>
  );
}
