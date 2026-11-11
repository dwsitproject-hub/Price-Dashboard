import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { CalendarEvent, JobInfo } from '../api/types';
import { Empty, ErrorAlert, useToast } from '../components/Feedback';
import { Icon } from '../components/Icon';
import { fmtDateTime } from '../lib/format';

interface Payload {
  events: CalendarEvent[]; fetched_at: string | null; today: string; categories: string[]; regions: string[];
  job: JobInfo | null; can_refresh: boolean; claude_configured: boolean;
}
// Legacy calendar_data category tints.
const CAT_TINT: Record<string, string> = { Agri: '#E2EFDA', Energy: '#FCE4D6', Biofuels: '#E4DFEC', Positioning: '#FFF2CC', Macro: '#DDEBF7' };
type SortKey = 'event_date' | 'event' | 'region' | 'category' | 'agency';

export function CalendarPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data, error, isLoading } = useQuery({
    queryKey: ['calendar'], queryFn: () => api.get<Payload>('/intel/calendar'),
    refetchInterval: (q) => (q.state.data?.job && ['queued', 'running'].includes(q.state.data.job.status) ? 5000 : false),
  });
  const refresh = useMutation({
    mutationFn: () => api.post('/intel/calendar/refresh'),
    onSuccess: () => { toast('Calendar refresh queued — Claude searches the official calendars (a few minutes).'); qc.invalidateQueries({ queryKey: ['calendar'] }); },
    onError: (e) => toast((e as Error).message),
  });
  const [cats, setCats] = useState<string[]>([]);
  const [region, setRegion] = useState('');
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<{ k: SortKey; dir: 1 | -1 }>({ k: 'event_date', dir: 1 });
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (data?.events ?? [])
      .filter((e) => (!cats.length || cats.includes(e.category)) && (!region || e.region === region)
        && (!needle || `${e.event} ${e.agency} ${e.notes}`.toLowerCase().includes(needle)))
      .sort((a, b) => (a[sort.k] < b[sort.k] ? -1 : a[sort.k] > b[sort.k] ? 1 : 0) * sort.dir);
  }, [data, cats, region, q, sort]);
  const today = data?.today ?? '';
  const weekEnd = useMemo(() => {
    if (!today) return '';
    const d = new Date(`${today}T00:00:00`);
    d.setDate(d.getDate() + 7);
    return d.toISOString().slice(0, 10);
  }, [today]);
  const th = (k: SortKey, label: string) => (
    <th aria-sort={sort.k === k ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
      <button className="th-sort" onClick={() => setSort((s) => ({ k, dir: s.k === k ? (s.dir === 1 ? -1 : 1) : 1 }))}>
        {label}{sort.k === k ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}</button></th>);
  const job = data?.job;
  const running = job && ['queued', 'running'].includes(job.status);
  return (
    <>
      <div className="page-head">
        <div><h1>Commodity &amp; Energy Event Calendar</h1>
          <p>Next ~30 days{data?.fetched_at ? ` · refreshed ${fmtDateTime(data.fetched_at)}` : ''} · <i>verify each date via its linked source</i></p></div>
        <div className="row" style={{ gap: 8 }}>
          {job?.status === 'failed' && <span className="chip" title={job.error ?? ''}><span className="dot" style={{ background: 'var(--neg)' }} /> Last refresh failed</span>}
          {running && <span className="chip"><span className="spinner" /> Refreshing…</span>}
          {data?.can_refresh && data.claude_configured && (
            <button className="btn btn-sm" disabled={!!running || refresh.isPending} onClick={() => refresh.mutate()}>
              <Icon name="refresh" size={14} /> Refresh</button>)}
        </div>
      </div>
      <ErrorAlert error={error} />
      {isLoading && <div className="card skeleton" style={{ height: 420 }} />}
      {data && !data.events.length && (
        <div className="card"><Empty title="No events yet" icon="calendar">
          {!data.claude_configured ? 'Claude is not configured on this server, so the calendar cannot be compiled.'
            : running ? 'The first compilation is running (Claude searches the official release calendars)…'
              : 'The calendar is compiled every weekday morning (Claude + web search).'}</Empty></div>)}
      {data && data.events.length > 0 && (
        <>
          <div className="card controls">
            <div className="control"><span className="label">Category</span>
              <div className="years" role="group" aria-label="Categories">
                {data.categories.map((c) => (
                  <button key={c} type="button" className="year-chip" aria-pressed={cats.includes(c)}
                          onClick={() => setCats((cur) => (cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]))}>{c}</button>))}
              </div></div>
            <div className="control"><label className="label" htmlFor="cal-region">Region</label>
              <select id="cal-region" className="select" value={region} onChange={(e) => setRegion(e.target.value)}>
                <option value="">All</option>{data.regions.map((r) => <option key={r}>{r}</option>)}</select></div>
            <div className="control"><label className="label" htmlFor="cal-q">Search</label>
              <input id="cal-q" className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Event, agency, notes" style={{ width: 220 }} /></div>
          </div>
          <div className="card table-wrap">
            <table className="tbl compact calendar-table">
              <thead><tr>{th('event_date', 'Date')}<th>Day</th>{th('event', 'Event')}{th('region', 'Region')}{th('category', 'Category')}{th('agency', 'Source')}<th>Notes</th></tr></thead>
              <tbody>
                {rows.map((e, i) => {
                  const isToday = e.event_date === today;
                  const thisWeek = e.event_date >= today && e.event_date <= weekEnd;
                  return (
                    <tr key={i} className={isToday ? 'cal-today' : thisWeek ? 'cal-week' : ''}>
                      <td className="num">{e.event_date}</td><td>{e.day}</td><td style={{ fontWeight: 600 }}>{e.event}</td><td>{e.region}</td>
                      <td><span className="cal-cat" style={{ background: CAT_TINT[e.category] }}>{e.category}</span></td>
                      <td>{e.source_url ? <a href={e.source_url} target="_blank" rel="noopener noreferrer">{e.agency || 'source'} <Icon name="external" size={12} /></a> : e.agency}</td>
                      <td className="muted" style={{ fontSize: 12.5 }}>{e.notes}</td>
                    </tr>);
                })}
                {!rows.length && <tr><td colSpan={7} className="faint">No events match the filters.</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
