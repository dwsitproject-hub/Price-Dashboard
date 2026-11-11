import { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api, qs } from '../api/client';
import type { ArbPayload, Curve, TicketsCalc, YtdProduct } from '../api/types';
import { ForwardCurveChart, YtdChart } from '../components/charts';
import { Segmented, SingleYearSelect, YearPicker } from '../components/Controls';
import { Empty, ErrorAlert } from '../components/Feedback';
import { useControls } from '../lib/controls';
import { fmtNum } from '../lib/format';

/** Sub-view names shown in page titles (legacy Argus sub-tabs; Basis → Tradable Prem). */
export const SECTION_NAMES: Record<string, Record<string, string>> = {
  waste_bio: {
    calendar: 'Calendar Spreads', product: 'Product Spreads', outright: 'Outright Prices', premium: 'Biofuel Premium',
    bunker: 'Bunker Market', margin: 'European Margin', byproduct: 'Byproduct', freight: 'Freight',
    tickets: 'Tickets', arb: 'Arb', features: 'Features', us: 'US Compliance',
  },
  basis_premiums: { tradable: 'Tradable Prem' },
};

type CurveSet = 'calendar' | 'product' | 'fwd_diff';
const SET_TEXT: Record<CurveSet, [string, string]> = {
  calendar: ['Calendar Spreads — Forward Curve', 'Argus swap curve M1–M3 per product; one line per year, sampled on the same day-month.'],
  product: ['Product Spreads — Forward Curve', 'Each cross-product spread by maturity; one line per year, sampled on the same day-month.'],
  fwd_diff: ['Biofuel Premium — Fwd Diff', 'Product M1/M2/M3 minus ICE Gasoil at the same maturity (LGO c1–c3); one line per year.'],
};

/** Waste/Bio forward-curve views (legacy ar-cal-forward, ar-prod-forward, prem-fwd Graph | Tabular). */
export function ArgusCurvesPage({ set }: { set: CurveSet }) {
  const { years } = useControls();
  const [snap, setSnap] = useState(new Date().toISOString().slice(0, 10));
  const [view, setView] = useState<'graph' | 'table'>('graph');
  const md = snap.slice(5);
  const { data, error, isFetching } = useQuery({
    queryKey: ['argus-curves', set, years, md],
    queryFn: ({ signal }) => api.get<{ curves: Curve[] }>(`/sheets/waste-bio/curves/${set}${qs({ years, snapshot: md })}`,
                                                           signal),
    enabled: years.length > 0,
    placeholderData: keepPreviousData,
  });
  const [title, sub] = SET_TEXT[set];
  const hasData = data?.curves.some((c) => c.traces.length);
  return (
    <>
      <div className="page-head">
        <div><h1>Waste / Bio — {title}</h1><p>{sub}</p></div>
        {isFetching && <span className="spinner" />}
      </div>
      <div className="card controls">
        <div className="control">
          <label className="label" htmlFor="snap">Snapshot</label>
          <input id="snap" className="input" type="date" value={snap} onChange={(e) => e.target.value && setSnap(e.target.value)}
                 style={{ height: 32, width: 160 }} />
        </div>
        <YearPicker />
        {set === 'fwd_diff' && (
          <div className="control"><span className="label">View</span>
            <Segmented label="View" value={view} onChange={setView}
                       options={[{ value: 'graph', label: 'Graph' }, { value: 'table', label: 'Tabular' }]} /></div>
        )}
      </div>
      <ErrorAlert error={error} />
      {data && !hasData && <div className="card"><Empty title="No Argus data yet">The Argus sheet has not been loaded — see Administration → Sheet sources.</Empty></div>}
      {view === 'table' && set === 'fwd_diff' ? (
        <div className="chart-grid">
          {data?.curves.map((c) => <CurveTable key={c.key} curve={c} />)}
        </div>
      ) : (
        <div className="chart-grid">
          {!data && Array.from({ length: 6 }, (_, i) => <div key={i} className="card skeleton" style={{ height: 350 }} />)}
          {hasData && data?.curves.map((c) => (
            <article key={c.key} className="card chart-card">
              <header className="card-head"><div><div className="card-title">{c.title}</div><div className="card-sub">{c.unit}</div></div></header>
              <div className="chart-body">
                {c.traces.length ? <ForwardCurveChart curve={c} /> : <Empty title="No data">No curve for the selected years.</Empty>}
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}

/** legacy make_argus_fwd_diff_table: one row per year (latest first) with the as-of date and M1–M3. */
function CurveTable({ curve }: { curve: Curve }) {
  const rows = [...curve.traces].sort((a, b) => b.year - a.year);
  const cols = ['M1', 'M2', 'M3'];
  return (
    <article className="card">
      <header className="card-head"><div className="card-title">{curve.title}</div></header>
      {rows.length === 0 ? <Empty title="No data" /> : (
        <div className="table-wrap">
          <table className="tbl compact">
            <thead><tr><th>Year</th><th>As of</th>{cols.map((c) => <th key={c} className="r">{c} ({curve.unit})</th>)}</tr></thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.year}>
                  <td style={{ fontWeight: 600 }}>{t.year}</td><td>{t.as_of}</td>
                  {cols.map((c) => {
                    const i = t.x.indexOf(c);
                    const v = i >= 0 ? t.y[i] : null;
                    return <td key={c} className="r num">{v === null || v === undefined ? '—' : v.toFixed(2)}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </article>
  );
}

/** Waste/Bio → Calendar Spreads → YTD (legacy make_argus_ytd_chart). */
export function ArgusYtdPage() {
  const { available } = useControls();
  const [year, setYear] = useState<number | null>(null);
  const y = year ?? available[available.length - 1] ?? new Date().getFullYear();
  const { data, error, isFetching } = useQuery({
    queryKey: ['argus-ytd', y],
    queryFn: ({ signal }) => api.get<{ products: YtdProduct[] }>(`/sheets/waste-bio/ytd${qs({ year: y })}`, signal),
    placeholderData: keepPreviousData,
  });
  return (
    <>
      <div className="page-head">
        <div><h1>Waste / Bio — Calendar Spreads YTD</h1>
          <p>M1 minus M2 and M3 on the year's own dates; the dotted line is the M1 price.</p></div>
        {isFetching && <span className="spinner" />}
      </div>
      <div className="card controls"><SingleYearSelect id="argus-ytd-year" label="Year" value={y} onChange={setYear} /></div>
      <ErrorAlert error={error} />
      <div className="chart-grid">
        {!data && Array.from({ length: 6 }, (_, i) => <div key={i} className="card skeleton" style={{ height: 370 }} />)}
        {data?.products.map((p) => (
          <article key={p.key} className="card chart-card">
            <header className="card-head"><div><div className="card-title">{p.title} — {p.year}</div><div className="card-sub">{p.unit}</div></div></header>
            <div className="chart-body">{p.legs.length ? <YtdChart item={p} /> : <Empty title="No data">No curve for {p.year}.</Empty>}</div>
          </article>
        ))}
      </div>
    </>
  );
}

const eur = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `€${fmtNum(v, 1)}`);
const usd = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `$${fmtNum(v, 1)}`);

function Status({ s }: { s: 'open' | 'closed' | null }) {
  if (!s) return <span className="muted">no data</span>;
  return <span className={`badge ${s === 'open' ? 'badge-pos' : 'badge-neg'}`} style={{ fontWeight: 700 }}>ARB {s.toUpperCase()}</span>;
}

function Row({ item, detail, amount, bold }: { item: string; detail?: string; amount: string; bold?: boolean }) {
  return <tr style={bold ? { fontWeight: 700 } : undefined}><td>{item}</td><td className="muted">{detail}</td><td className="r num">{amount}</td></tr>;
}

/** Waste/Bio → Arb (legacy _argus_arb_tile + _argus_china_ucome_arb_tile); the historical charts follow below. */
export function ArbPanel() {
  const { data, error } = useQuery({ queryKey: ['argus-arb'], queryFn: () => api.get<ArbPayload>('/sheets/waste-bio/arb') });
  if (error) return <ErrorAlert error={error} />;
  if (!data) return <div className="card skeleton" style={{ height: 300, marginBottom: 16 }} />;
  const k = data.constants;
  const cur = data.current;
  const s = data.sme;
  const c = data.china_ucome;
  return (
    <div className="stack" style={{ gap: 16, marginBottom: 16 }}>
      <article className="card" style={{ padding: 16 }}>
        <h3 style={{ margin: '0 0 4px' }}>MIP (minimum import price) — SME landed into ARA, by quarter</h3>
        <p className="muted" style={{ fontSize: 12, margin: '0 0 10px', maxWidth: 900 }}>
          MIP = (avg SBO Argentina over the quarter's fixing window ÷ EURUSD averaged over the same window + €{k.conversion_eur.toFixed(0)} conversion
          + ${k.freight_usd.toFixed(0)} freight ÷ EURUSD) × {(1 + k.import_duty).toFixed(3)} duty. Fixing window = the 3 calendar months ending 2 months
          before the quarter starts (Q4 Oct–Dec is priced off Jun–Aug). One MIP applies flat across the quarter's 3 months.
        </p>
        {data.quarters.length === 0 ? <Empty title="MIP unavailable">Needs LSEG BO-ARGUPR-C1 and EUR= history.</Empty> : (
          <div className="table-wrap">
            <table className="tbl compact">
              <thead><tr><th>Quarter</th><th>Fixing window</th><th>EURUSD ref</th><th className="r">Avg SBO Argentina (USD/t)</th><th className="r">MIP (EUR/t)</th></tr></thead>
              <tbody>{data.quarters.map((q) => {
                const isCur = cur?.label === q.label;
                return (
                  <tr key={q.label} style={isCur ? { background: 'var(--accent-soft, rgba(227,162,26,.12))' } : undefined}>
                    <td style={{ fontWeight: isCur ? 700 : 400 }}>{q.label}{isCur ? ' (current)' : ''}</td>
                    <td>{q.window_start} → {q.window_end}</td><td>{q.eurusd.toFixed(4)} ({q.fx_date})</td>
                    <td className="r num">{usd(q.avg_sbo_usd)}</td><td className="r num" style={{ fontWeight: 700 }}>{eur(q.mip_eur)}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        )}
        {cur && s ? (
          <>
            <h4 style={{ margin: '16px 0 4px' }}>Confirmed quarter detail — {cur.label} (fixed {cur.window_start} → {cur.window_end}, EURUSD {cur.eurusd.toFixed(4)})</h4>
            <p className="muted" style={{ fontSize: 12, margin: '0 0 6px' }}>The first six rows use the quarter's fixing; the RME premium row uses the latest data.</p>
            <table className="tbl compact" style={{ marginBottom: 12 }}>
              <thead><tr><th>Item</th><th>Detail</th><th className="r">Amount</th></tr></thead>
              <tbody>
                <Row item="Avg SBO Argentina (fixing window)" detail={`${usd(cur.avg_sbo_usd)}/t ÷ EURUSD ${cur.eurusd.toFixed(4)}`} amount={`${eur(s.eur_oil)}/t`} />
                <Row item="+ SME conversion cost" detail="soyoil → SME, flat assumption" amount={`${eur(s.eur_conversion)}/t`} />
                <Row item="+ Freight (Argentina → ARA)" detail={`$${k.freight_usd.toFixed(0)}/t ÷ EURUSD ${cur.eurusd.toFixed(4)}`} amount={`${eur(s.eur_freight)}/t`} />
                <Row item="= Subtotal (pre-duty)" amount={`${eur(s.subtotal)}/t`} bold />
                <Row item={`+ EU import duty (${(k.import_duty * 100).toFixed(1)}%)`} detail="applied to the whole landed value" amount={`${eur(s.duty)}/t`} />
                <Row item="= Landed cost (post-duty)" amount={`${eur(s.landed)}/t`} bold />
                <Row item={`+ RME premium (${k.rme_fraction} × RME−FAME 0, latest M1)`}
                     detail={s.rme_premium === null ? 'no data' : `${k.rme_fraction} × (${usd(s.rme_m1)} − ${usd(s.fame0_m1)}) ÷ EURUSD ${s.live_eurusd.toFixed(4)}`}
                     amount={`${eur(s.rme_premium ?? 0)}/t`} />
                <Row item="= SME landed value (ARA)" amount={`${eur(s.value)}/t`} bold />
              </tbody>
            </table>
            <p className="muted" style={{ fontSize: 12, margin: '0 0 6px' }}>
              FAME 0 is the latest Argus swap curve (M1/M2/M3 = this quarter's months), converted at the latest EURUSD ({s.live_eurusd.toFixed(4)}).
            </p>
            <table className="tbl compact">
              <thead><tr><th>Month</th><th>Maturity</th><th className="r">SME landed (EUR/t)</th><th className="r">FAME 0 (USD/t)</th><th className="r">FAME 0 (EUR/t)</th><th className="r">Diff (EUR/t)</th><th>Status</th></tr></thead>
              <tbody>{s.months.map((m) => (
                <tr key={m.maturity}>
                  <td>{m.month}</td><td>{m.maturity}</td><td className="r num">{eur(m.sme_landed_eur)}</td>
                  <td className="r num">{usd(m.fame0_usd)}</td><td className="r num">{eur(m.fame0_eur)}</td>
                  <td className="r num" style={{ fontWeight: 700 }}>{m.diff_eur === null ? '—' : `${m.diff_eur >= 0 ? '+' : ''}${eur(m.diff_eur)}`}</td>
                  <td><Status s={m.status} /></td>
                </tr>
              ))}</tbody>
            </table>
          </>
        ) : data.quarters.length > 0 && (
          <p className="muted" style={{ marginTop: 10 }}>The current quarter's MIP is not computable yet (fixing window not fully observed).</p>
        )}
      </article>
      <article className="card" style={{ padding: 16 }}>
        <h3 style={{ margin: '0 0 4px' }}>China UCOME CIF Malaysia</h3>
        <p className="muted" style={{ fontSize: 12, margin: '0 0 10px' }}>
          Blended CIF Malaysia cost — 60% a China-UCOME import route, 40% a POME-fob-Malaysia route — vs. the pure-POME reference. All USD/t.
          Diff &gt; 0 = arb closed, &lt; 0 = arb open.
        </p>
        {!c ? <Empty title="Unavailable">Missing Argus UCOME China FOB or POME fob Malaysia.</Empty> : (
          <>
            <table className="tbl compact" style={{ marginBottom: 10 }}>
              <thead><tr><th>Item</th><th>Detail</th><th className="r">Amount</th></tr></thead>
              <tbody>
                <Row item="China UCOME FOB (latest)" amount={`${usd(c.china_fob)}/t`} />
                <Row item="+ Freight (China → Malaysia)" amount={`${usd(c.freight)}/t`} />
                <Row item="+ Trucking" amount={`${usd(c.trucking)}/t`} />
                <Row item="+ Fobbing" amount={`${usd(c.fobbing)}/t`} />
                <Row item="= China route landed cost" amount={`${usd(c.china_landed)}/t`} bold />
                <Row item={`× ${(c.china_weight * 100).toFixed(0)}% weight`} amount={`${usd(c.china_weighted)}/t`} />
                <Row item="POME FOB Malaysia (latest)" amount={`${usd(c.pome_fob)}/t`} />
                <Row item="+ Net costs" amount={`${usd(c.net_costs)}/t`} />
                <Row item="= POME reference cost" amount={`${usd(c.pome_reference)}/t`} bold />
                <Row item={`× ${(c.pome_weight * 100).toFixed(0)}% weight`} amount={`${usd(c.pome_weighted)}/t`} />
                <Row item="= China UCOME CIF Malaysia Blended" detail="60% China route + 40% POME route" amount={`${usd(c.blended)}/t`} bold />
                <Row item="− POME reference cost" detail="pure-POME alternative (unweighted)" amount={`${usd(c.pome_reference)}/t`} />
                <Row item="= Diff" amount={`${c.diff >= 0 ? '+' : ''}${usd(c.diff)}/t`} bold />
              </tbody>
            </table>
            <Status s={c.status} />
          </>
        )}
      </article>
    </div>
  );
}

/** Waste/Bio → Tickets: how the blend margin is calculated (legacy _ticket_calc_block). */
export function TicketsCalcBox() {
  const { data } = useQuery({ queryKey: ['argus-tickets'], queryFn: () => api.get<TicketsCalc>('/sheets/waste-bio/tickets-calc') });
  if (!data) return null;
  return (
    <article className="card" style={{ padding: '12px 16px', marginBottom: 16 }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 4 }}>
        How the blend margin is calculated — UK: pence per litre of fuel; Germany / Netherlands: USD per tonne of biofuel
      </div>
      <p className="muted" style={{ fontSize: 12, margin: '0 0 8px' }}>{data.assumptions}</p>
      <div className="table-wrap">
        <table className="tbl compact" style={{ fontSize: 12 }}>
          <thead><tr><th>Market</th><th>Ticket used</th><th>Formula</th><th>Worked example — UCOME, latest</th></tr></thead>
          <tbody>{data.rows.map((r) => (
            <tr key={r.market} style={{ verticalAlign: 'top' }}>
              <td style={{ fontWeight: 700 }}>{r.market}</td><td style={{ whiteSpace: 'pre-line' }}>{r.ticket}</td>
              <td>{r.formula}</td><td>{r.example}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </article>
  );
}
