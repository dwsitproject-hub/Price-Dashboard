import { useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from 'react';
import { geoMercator, geoPath } from 'd3-geo';
import { feature } from 'topojson-client';
import world from 'world-atlas/countries-110m.json';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { AtlasPlant } from '../api/types';
import { ErrorAlert, useToast } from '../components/Feedback';
import { Icon } from '../components/Icon';

/* eslint-disable @typescript-eslint/no-explicit-any */
// Legacy atlas colours and labels.
const STATUS: Record<AtlasPlant['status'], { label: string; color: string }> = {
  op: { label: 'Operating', color: '#f2a93b' }, con: { label: 'Under construction', color: '#4fa3e3' },
  ann: { label: 'Announced', color: '#7f8ea6' }, can: { label: 'Cancelled', color: '#d15757' },
};
const fmtKt = (kt: number) => (kt >= 1000 ? `${(kt / 1000).toFixed(kt >= 10000 ? 0 : 1)} Mt` : `${Math.round(kt)} kt`);
const radius = (kt: number) => Math.max(3.2, Math.sqrt(kt) * 0.3);
const COUNTRIES = (feature(world as any, (world as any).objects.countries) as any).features
  .filter((f: any) => f.id !== '010');                                   // drop Antarctica
const SPHERE = { type: 'FeatureCollection', features: COUNTRIES };

function WorldMap({ plants, selected, onSelect }: { plants: AtlasPlant[]; selected: AtlasPlant | null; onSelect: (p: AtlasPlant) => void }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(900);
  const [view, setView] = useState({ k: 1, x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth || 900));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const h = Math.round(Math.min(560, Math.max(320, w * 0.52)));
  const { path, project } = useMemo(() => {
    const proj = geoMercator().fitExtent([[8, 8], [w - 8, h - 8]], SPHERE as any);
    return { path: geoPath(proj), project: (p: AtlasPlant) => proj([p.lon, p.lat]) };
  }, [w, h]);
  const zoom = (f: number) => setView((v) => {
    const k = Math.min(10, Math.max(1, v.k * f));
    return { k, x: w / 2 - ((w / 2 - v.x) * k) / v.k, y: h / 2 - ((h / 2 - v.y) * k) / v.k };
  });
  const down = (e: RPointerEvent) => { drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y }; (e.target as Element).setPointerCapture?.(e.pointerId); };
  const move = (e: RPointerEvent) => { const d = drag.current; if (d) setView((v) => ({ ...v, x: d.vx + e.clientX - d.x, y: d.vy + e.clientY - d.y })); };
  return (
    <div ref={wrap} className="atlas-map">
      <svg width={w} height={h} role="img" aria-label="Map of HVO and SAF plants" onPointerDown={down} onPointerMove={move}
           onPointerUp={() => (drag.current = null)} onPointerLeave={() => (drag.current = null)}>
        <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
          {COUNTRIES.map((f: any) => <path key={f.id ?? f.properties?.name} d={path(f) ?? ''} className="atlas-land" strokeWidth={0.5 / view.k} />)}
          {[...plants].sort((a, b) => b.kt - a.kt).map((p) => {
            const xy = project(p);
            if (!xy) return null;
            const r = radius(p.kt) / view.k;
            const c = STATUS[p.status].color;
            const isSel = selected?.name === p.name;
            return (
              <circle key={p.name} cx={xy[0]} cy={xy[1]} r={r} fill={p.type === 'copro' ? 'none' : c} fillOpacity={0.78}
                      stroke={isSel ? 'var(--ink)' : c} strokeWidth={(isSel ? 2.4 : p.type === 'copro' ? 1.8 : 0.8) / view.k}
                      style={{ cursor: 'pointer' }} onClick={() => onSelect(p)}>
                <title>{`${p.name} — ${p.company}\n${fmtKt(p.kt)}/yr · ${STATUS[p.status].label}`}</title>
              </circle>);
          })}
        </g>
      </svg>
      <div className="atlas-zoom">
        <button className="btn btn-sm" aria-label="Zoom in" onClick={() => zoom(1.6)}>+</button>
        <button className="btn btn-sm" aria-label="Zoom out" onClick={() => zoom(1 / 1.6)}>−</button>
        <button className="btn btn-sm" aria-label="Reset view" onClick={() => setView({ k: 1, x: 0, y: 0 })}><Icon name="refresh" size={13} /></button>
      </div>
    </div>
  );
}

type SortK = 'name' | 'country' | 'status' | 'kt' | 'safkt' | 'startup';

export function AtlasPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data, error } = useQuery({ queryKey: ['atlas'], queryFn: () => api.get<{ snapshot: string | null; plants: AtlasPlant[]; can_edit: boolean }>('/intel/atlas') });
  const [statuses, setStatuses] = useState<AtlasPlant['status'][]>(['op', 'con', 'ann']);
  const [types, setTypes] = useState<AtlasPlant['type'][]>(['standalone', 'copro']);
  const [safOnly, setSafOnly] = useState(false);
  const [q, setQ] = useState('');
  const [country, setCountry] = useState<string | null>(null);
  const [selected, setSelected] = useState<AtlasPlant | null>(null);
  const [sort, setSort] = useState<{ k: SortK; dir: 1 | -1 }>({ k: 'kt', dir: -1 });
  const all = data?.plants ?? [];
  const shown = useMemo(() => {
    const n = q.trim().toLowerCase();
    return all.filter((p) => statuses.includes(p.status) && types.includes(p.type) && (!safOnly || (p.safkt ?? 0) > 0)
      && (!country || p.country === country) && (!n || `${p.name} ${p.company} ${p.country} ${p.region}`.toLowerCase().includes(n)));
  }, [all, statuses, types, safOnly, country, q]);
  const sum = (st: string, f: (p: AtlasPlant) => number) => all.filter((p) => p.status === st).reduce((a, p) => a + f(p), 0);
  const ranking = useMemo(() => {
    const m = new Map<string, number>();
    shown.forEach((p) => m.set(p.country, (m.get(p.country) ?? 0) + p.kt));
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 15);
  }, [shown]);
  const sorted = useMemo(() => [...shown].sort((a, b) => {
    const av = a[sort.k] ?? -Infinity, bv = b[sort.k] ?? -Infinity;      // nulls last either way
    return (av < bv ? -1 : av > bv ? 1 : 0) * sort.dir;
  }), [shown, sort]);
  const toggle = <T,>(arr: T[], v: T, set: (x: T[]) => void) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const replace = useMutation({
    mutationFn: async (file: File) => api.put<{ plants: number }>('/intel/atlas', JSON.parse(await file.text())),
    onSuccess: (r) => { toast(`Atlas replaced — ${r.plants} plants.`); qc.invalidateQueries({ queryKey: ['atlas'] }); },
    onError: (e) => toast(e instanceof SyntaxError ? 'That file is not valid JSON.' : (e as Error).message),
  });
  const download = () => {
    const blob = new Blob([JSON.stringify({ snapshot: data?.snapshot, plants: all }, null, 1)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'hvo_saf_plants.json';
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const th = (k: SortK, label: string, r = false) => (
    <th className={r ? 'r' : ''} aria-sort={sort.k === k ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
      <button className="th-sort" onClick={() => setSort((s) => ({ k, dir: s.k === k ? (s.dir === 1 ? -1 : 1) : -1 }))}>
        {label}{sort.k === k ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}</button></th>);
  return (
    <>
      <div className="page-head">
        <div><h1>Global HVO / Renewable Diesel &amp; SAF Plant Atlas</h1>
          <p>Capacity in kt/yr{data?.snapshot ? ` · snapshot compiled ${data.snapshot}` : ''} · 2.95 kt ≈ 1 million US gal · 45 kt ≈ 1,000 b/d</p></div>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn btn-sm" onClick={download}><Icon name="download" size={14} /> JSON</button>
          {data?.can_edit && (
            <label className="btn btn-sm" style={{ cursor: 'pointer' }}><Icon name="upload" size={14} /> Replace dataset
              <input type="file" accept=".json,application/json" hidden onChange={(e) => {
                const f = e.target.files?.[0];
                if (f && window.confirm(`Replace all ${all.length} plants with ${f.name}?`)) replace.mutate(f);
                e.target.value = '';
              }} /></label>)}
        </div>
      </div>
      <ErrorAlert error={error} />
      <div className="kpis">
        {[['Operating', fmtKt(sum('op', (p) => p.kt))], ['Under construction', fmtKt(sum('con', (p) => p.kt))],
          ['Announced', fmtKt(sum('ann', (p) => p.kt))], ['Max SAF (operating)', fmtKt(sum('op', (p) => p.safkt ?? 0))],
          ['Plants shown', String(shown.length)]].map(([l, v]) => (
          <div key={l} className="card kpi"><div className="kpi-v num">{v}</div><div className="kpi-l">{l}</div></div>))}
      </div>
      <div className="card controls">
        <div className="years" role="group" aria-label="Status">
          {(Object.keys(STATUS) as AtlasPlant['status'][]).map((s) => (
            <button key={s} type="button" className="year-chip" aria-pressed={statuses.includes(s)} onClick={() => toggle(statuses, s, setStatuses)}>
              <span className="legend-dot" style={{ background: STATUS[s].color }} /> {STATUS[s].label}</button>))}
        </div>
        <div className="years" role="group" aria-label="Plant type">
          <button type="button" className="year-chip" aria-pressed={types.includes('standalone')} onClick={() => toggle(types, 'standalone', setTypes)}>Standalone</button>
          <button type="button" className="year-chip" aria-pressed={types.includes('copro')} onClick={() => toggle(types, 'copro', setTypes)}>○ Co-processing</button>
          <button type="button" className="year-chip" aria-pressed={safOnly} onClick={() => setSafOnly((v) => !v)}>SAF-capable only</button>
        </div>
        <input className="input" placeholder="Search plant, company, country" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} aria-label="Search plants" />
        {country && <button className="chip" onClick={() => setCountry(null)}>{country} <Icon name="x" size={12} /></button>}
      </div>
      <div className="atlas-layout">
        <div className="card" style={{ padding: 8 }}><WorldMap plants={shown} selected={selected} onSelect={setSelected} /></div>
        <div className="stack" style={{ gap: 12 }}>
          {selected && (
            <div className="card card-pad atlas-detail">
              <div className="row" style={{ justifyContent: 'space-between' }}><b>{selected.name}</b>
                <button className="btn btn-ghost btn-sm icon-btn" aria-label="Close" onClick={() => setSelected(null)}><Icon name="x" size={14} /></button></div>
              <div className="muted">{selected.company} · {selected.region ? `${selected.region}, ` : ''}{selected.country}</div>
              <div className="row" style={{ gap: 6, margin: '8px 0' }}>
                <span className="badge" style={{ background: STATUS[selected.status].color, color: '#111' }}>{STATUS[selected.status].label}</span>
                <span className="badge">{selected.type === 'copro' ? 'Co-processing' : 'Standalone'}</span>
                {selected.est && <span className="badge" title="Capacity converted or estimated from the quoted figure">est.</span>}</div>
              <div className="num">{fmtKt(selected.kt)}/yr{selected.safkt ? ` · SAF up to ${fmtKt(selected.safkt)}` : ''}</div>
              <div className="faint" style={{ fontSize: 12 }}>Quoted: {selected.quoted || '—'} · Start-up: {selected.startup || '—'}</div>
              {selected.note && <p style={{ fontSize: 12.5, marginBottom: 0 }}>{selected.note}</p>}
            </div>)}
          <div className="card card-pad">
            <div className="card-title" style={{ marginBottom: 6 }}>Capacity by country</div>
            {ranking.map(([c, kt]) => (
              <button key={c} className="atlas-rank" onClick={() => setCountry(c === country ? null : c)} aria-pressed={c === country}>
                <span>{c}</span><span className="bar" style={{ width: `${(kt / (ranking[0]?.[1] || 1)) * 100}%` }} /><span className="num">{fmtKt(kt)}</span>
              </button>))}
          </div>
        </div>
      </div>
      <div className="card table-wrap" style={{ marginTop: 16 }}>
        <table className="tbl compact">
          <thead><tr>{th('name', 'Plant')}<th>Company</th>{th('country', 'Country')}{th('status', 'Status')}<th>Type</th>
            {th('kt', 'kt/yr', true)}{th('safkt', 'SAF kt/yr', true)}{th('startup', 'Start-up')}</tr></thead>
          <tbody>
            {sorted.map((p) => (
              <tr key={p.name} className="row-click" onClick={() => setSelected(p)} aria-current={selected?.name === p.name}>
                <td style={{ fontWeight: 600 }}>{p.name}{p.est ? <span className="faint"> *</span> : null}</td><td>{p.company}</td><td>{p.country}</td>
                <td><span className="legend-dot" style={{ background: STATUS[p.status].color }} /> {STATUS[p.status].label}</td>
                <td>{p.type === 'copro' ? 'Co-processing' : 'Standalone'}</td>
                <td className="r num">{Math.round(p.kt).toLocaleString()}</td><td className="r num">{p.safkt ? Math.round(p.safkt).toLocaleString() : '—'}</td>
                <td className="faint">{p.startup}</td>
              </tr>))}
          </tbody>
        </table>
      </div>
      <p className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>* capacity converted or estimated from the quoted figure.</p>
    </>
  );
}
