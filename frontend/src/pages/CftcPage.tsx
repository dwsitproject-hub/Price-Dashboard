import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { CftcPayload, CftcProduct } from '../api/types';
import { Empty, ErrorAlert, useToast } from '../components/Feedback';
import { Icon } from '../components/Icon';
import { CategoryBars, CFTC_COLORS, CftcBars, CftcHistory } from '../components/intelCharts';
import { fmtDate, fmtDateTime } from '../lib/format';

type Cat = 'mm' | 'swap' | 'pm';
const CATS: [Cat, string][] = [['mm', 'Managed Money'], ['swap', 'Swap Dealers'], ['pm', 'Producer/Merchant']];
const signed = (v: number | null, pct = false) =>
  v === null ? 'n/a' : pct ? `${v >= 0 ? '+' : ''}${v.toFixed(1)}%` : `${v >= 0 ? '+' : ''}${Math.round(v).toLocaleString()}`;

function JobLine({ data, onRefresh, busy }: { data: CftcPayload; onRefresh: () => void; busy: boolean }) {
  const j = data.job;
  return (
    <div className="row" style={{ gap: 8 }}>
      {j && <span className="chip"><span className="dot" style={{ background: j.status === 'failed' ? 'var(--neg)' : undefined }} />
        {j.status === 'queued' || j.status === 'running' ? 'Refreshing…' : `Last refresh ${j.status} · ${fmtDateTime(j.finished_at ?? j.queued_at)}`}</span>}
      {data.can_refresh && (
        <button className="btn btn-sm" onClick={onRefresh} disabled={busy || j?.status === 'queued' || j?.status === 'running'}>
          <Icon name="refresh" size={14} /> Refresh from CFTC</button>)}
    </div>
  );
}

function ByTraderType({ products }: { products: CftcProduct[] }) {
  const groups: [string, (p: CftcProduct, c: Cat) => number | null, string, boolean][] = [
    ['Weekly Position Change (contracts)', (p, c) => p[`wow_${c}_net`], 'Δ Net Long Contracts (Long − Short)', false],
    ['Current Net Position (contracts)', (p, c) => p[`${c}_net`], 'Net Long Contracts (Long − Short)', false],
    ['Net Position vs. Open Interest (current)', (p, c) => p[`${c}_net_pct_oi`], 'Net Long % of Open Interest', true],
  ];
  return (
    <>
      {groups.map(([title, get, unit, pct]) => (
        <section key={title} style={{ marginBottom: 20 }}>
          <h4 style={{ margin: '6px 0 10px' }}>{title}</h4>
          <div className="chart-grid">
            {CATS.map(([c, name]) => {
              const items = products.filter((p) => get(p, c) !== null).map((p) => ({ label: p.label, value: get(p, c) as number }));
              return (
                <article key={c} className="card chart-card">
                  <header className="card-head"><div className="card-title" style={{ color: CFTC_COLORS[c] }}>{name}</div></header>
                  <div className="chart-body auto">{items.length ? <CftcBars items={items} color={CFTC_COLORS[c]} unit={unit} pct={pct} />
                    : <Empty title="Needs a second weekly report" />}</div>
                </article>
              );
            })}
          </div>
        </section>
      ))}
    </>
  );
}

function ByProduct({ products }: { products: CftcProduct[] }) {
  const grids: [string, (p: CftcProduct) => Record<string, number | null>, string, boolean][] = [
    ['Weekly Position Change, by Product (contracts)', (p) => ({ mm: p.wow_mm_net, swap: p.wow_swap_net, pm: p.wow_pm_net }), 'Δ Net Contracts (WoW)', false],
    ['Current Net Position, by Product (contracts)', (p) => ({ mm: p.mm_net, swap: p.swap_net, pm: p.pm_net }), 'Net Contracts (Long − Short)', false],
    ['Net Position vs. Open Interest, by Product (current)', (p) => ({ mm: p.mm_net_pct_oi, swap: p.swap_net_pct_oi, pm: p.pm_net_pct_oi }), 'Net % of Open Interest', true],
  ];
  return (
    <>
      {grids.map(([title, get, unit, pct]) => (
        <section key={title} style={{ marginBottom: 20 }}>
          <h4 style={{ margin: '6px 0 10px' }}>{title}</h4>
          <div className="chart-grid">
            {products.map((p) => (
              <article key={p.code} className="card chart-card">
                <header className="card-head"><div className="card-title">{p.label}</div></header>
                <div className="chart-body"><CategoryBars values={get(p)} unit={unit} pct={pct} /></div>
              </article>
            ))}
          </div>
        </section>
      ))}
      <section>
        <h4 style={{ margin: '6px 0 10px' }}>Net Position History, by Product</h4>
        <div className="chart-grid">
          {products.map((p) => (
            <article key={p.code} className="card chart-card">
              <header className="card-head"><div className="card-title">{p.label}</div></header>
              <div className="chart-body"><CftcHistory p={p} /></div>
            </article>
          ))}
        </div>
      </section>
    </>
  );
}

function Snapshot({ products }: { products: CftcProduct[] }) {
  const cell = (key: string, v: number | null) => (
    <td key={key} className="r num" style={{ color: v === null ? 'var(--faint)' : v >= 0 ? 'var(--info)' : 'var(--neg)', fontWeight: 600 }}>{signed(v)}</td>);
  return (
    <div className="card table-wrap" style={{ marginTop: 20 }}>
      <table className="tbl compact">
        <thead><tr><th>Product</th><th>Report date</th><th className="r">Open interest</th>
          {CATS.map(([c, n]) => [<th key={`${c}n`} className="r">{n} net</th>, <th key={`${c}p`} className="r">%OI</th>,
            <th key={`${c}w`} className="r">WoW Δ</th>])}</tr></thead>
        <tbody>
          {products.map((p) => (
            <tr key={p.code}>
              <td style={{ fontWeight: 600 }}>{p.label}</td><td>{p.report_date}</td>
              <td className="r num">{p.open_interest?.toLocaleString() ?? 'n/a'}</td>
              {CATS.map(([c]) => [<td key={`${c}n`} className="r num" style={{ fontWeight: 600 }}>{signed(p[`${c}_net`])}</td>,
                <td key={`${c}p`} className="r num">{signed(p[`${c}_net_pct_oi`], true)}</td>,
                cell(`${c}w`, p[`wow_${c}_net`])])}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CftcPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState<'type' | 'product'>('type');
  const { data, error, isLoading } = useQuery({
    queryKey: ['cftc'], queryFn: () => api.get<CftcPayload>('/intel/cftc'),
    refetchInterval: (q) => (q.state.data?.job && ['queued', 'running'].includes(q.state.data.job.status) ? 4000 : false),
  });
  const refresh = useMutation({
    mutationFn: () => api.post('/intel/cftc/refresh'),
    onSuccess: () => { toast('CFTC refresh queued.'); qc.invalidateQueries({ queryKey: ['cftc'] }); },
    onError: (e) => toast((e as Error).message),
  });
  return (
    <>
      <div className="page-head">
        <div><h1>CFTC Commitments of Traders — Spec Positioning</h1>
          <p>Disaggregated, Futures + Options Combined{data?.as_of ? ` · latest report ${fmtDate(data.as_of)} · ${data.weeks} week(s) on file` : ''}.</p></div>
        {data && <JobLine data={data} onRefresh={() => refresh.mutate()} busy={refresh.isPending} />}
      </div>
      <p className="muted" style={{ fontSize: 12.5, fontStyle: 'italic', marginTop: -8 }}>
        Managed Money = speculative funds/CTAs · Swap Dealers = banks hedging OTC swaps · Producer/Merchant = commercial hedgers.
      </p>
      <ErrorAlert error={error} />
      {isLoading && <div className="card skeleton" style={{ height: 400 }} />}
      {data && !data.products.length && (
        <div className="card"><Empty title="No CFTC data yet" icon="users">
          {data.job && ['queued', 'running'].includes(data.job.status) ? 'The first download is running…'
            : data.job?.status === 'failed' ? `The last download failed: ${data.job.error}` : 'Use “Refresh from CFTC” to load it.'}
        </Empty></div>
      )}
      {data && data.products.length > 0 && (
        <>
          <div className="tabs" role="tablist">
            <button className="tab" role="tab" aria-selected={tab === 'type'} onClick={() => setTab('type')}>By Trader Type</button>
            <button className="tab" role="tab" aria-selected={tab === 'product'} onClick={() => setTab('product')}>By Product</button>
          </div>
          {tab === 'type' ? <ByTraderType products={data.products} /> : <ByProduct products={data.products} />}
          <h4 style={{ margin: '24px 0 0' }}>Snapshot</h4>
          <Snapshot products={data.products} />
          <p className="faint" style={{ fontSize: 11.5, marginTop: 10 }}>
            Source: CFTC Commitments of Traders, Disaggregated Futures-and-Options-Combined history files (cftc.gov),
            refreshed weekly. Week-on-week change is against the previous report on file.
          </p>
        </>
      )}
    </>
  );
}
