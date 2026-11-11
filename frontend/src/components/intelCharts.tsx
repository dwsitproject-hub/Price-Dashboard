import { useMemo } from 'react';
import type { CftcProduct, SndTrace } from '../api/types';
import { useResolvedTheme } from '../lib/controls';
import { baseLayout, palette, yearStyle } from './charts';
import { Plot } from './Plot';

/* eslint-disable @typescript-eslint/no-explicit-any */

// Legacy colours: CFTC trader categories (cftc_data._HEADLINE_CATEGORIES), OI signals (ai_analysis).
export const CFTC_COLORS: Record<string, string> = { mm: '#2a78d6', swap: '#eb6834', pm: '#1baf7a' };
export const NEG = '#e34948';
export const SIGNAL_COLORS: Record<string, string> = {
  'Long Buildup': '#2ca02c', 'Short Buildup': '#d62728', 'Short Covering': '#f0c030', 'Long Unwinding': '#ff7f0e',
  Neutral: '#9e9e9e',
};

function useLayout(unit: string, extra: (l: any, p: ReturnType<typeof palette>) => void, deps: unknown[]) {
  const theme = useResolvedTheme();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => {
    const l: any = baseLayout(theme, unit);
    extra(l, palette(theme));
    return { layout: l, theme };
  }, [theme, unit, ...deps]);
}

/** SND Graphs: one line per year over Jan–Dec (legacy _snd_seasonal_chart). */
export function SndChart({ traces, unit, height = 300 }: { traces: SndTrace[]; unit: string; height?: number }) {
  const { layout, theme } = useLayout(unit, (l) => {
    l.xaxis = { ...l.xaxis, type: 'category' };
  }, []);
  const data = useMemo(() => {
    const years = traces.map((t) => t.year);
    return traces.map((t) => {
      const st = yearStyle(t.year, years, theme);
      return { type: 'scatter', mode: 'lines+markers', x: t.x, y: t.y, name: String(t.year), connectgaps: false,
               line: { color: st.color, width: st.width }, marker: { size: 4, color: st.color }, opacity: st.opacity,
               hovertemplate: `<b>${t.year}</b> %{x}: %{y:,.0f}<extra></extra>` };
    });
  }, [traces, theme]);
  return <Plot data={data} layout={layout} height={height} />;
}

/** CFTC: horizontal bars per product for one trader category (legacy make_*_chart). */
export function CftcBars({ items, color, unit, pct = false }: {
  items: { label: string; value: number }[]; color: string; unit: string; pct?: boolean;
}) {
  const sorted = useMemo(() => [...items].sort((a, b) => a.value - b.value), [items]);
  const { layout } = useLayout(unit, (l, p) => {
    l.yaxis = { ...l.yaxis, title: undefined, automargin: true, tickfont: { size: 11, color: p.ink } };
    l.xaxis = { ...l.xaxis, title: { text: unit, font: { size: 10.5, color: p.muted } }, zeroline: true,
                zerolinecolor: p.zero };
    l.hovermode = 'closest';
    l.showlegend = false;
    l.margin = { l: 10, r: 16, t: 6, b: 40 };
  }, []);
  const data = useMemo(() => [{
    type: 'bar', orientation: 'h', y: sorted.map((i) => i.label), x: sorted.map((i) => i.value),
    marker: { color: sorted.map((i) => (i.value >= 0 ? color : NEG)) },
    hovertemplate: pct ? '%{y}: %{x:+.1f}%<extra></extra>' : '%{y}: %{x:+,.0f}<extra></extra>',
  }], [sorted, color, pct]);
  return <Plot data={data} layout={layout} height={90 + 32 * Math.max(items.length, 1)} />;
}

/** CFTC By Product: MM / Swap / P/M bars for one product. */
export function CategoryBars({ values, unit, pct = false }: { values: Record<string, number | null>; unit: string; pct?: boolean }) {
  const { layout } = useLayout(unit, (l, p) => {
    l.xaxis = { ...l.xaxis, type: 'category' };
    l.yaxis = { ...l.yaxis, zeroline: true, zerolinecolor: p.zero };
    l.showlegend = false;
    l.hovermode = 'closest';
  }, []);
  const keys = ['mm', 'swap', 'pm'];
  const data = useMemo(() => [{
    type: 'bar', x: ['MM', 'Swap', 'P/M'], y: keys.map((k) => values[k] ?? null),
    marker: { color: keys.map((k) => ((values[k] ?? 0) >= 0 ? CFTC_COLORS[k] : NEG)) },
    hovertemplate: pct ? '%{x}: %{y:+.1f}%<extra></extra>' : '%{x}: %{y:+,.0f}<extra></extra>',
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }], [values, pct]);
  return <Plot data={data} layout={layout} height={260} />;
}

export function CftcHistory({ p }: { p: CftcProduct }) {
  const { layout } = useLayout('Net contracts', (l, pal) => {
    l.xaxis = { ...l.xaxis, type: 'date' };
    l.yaxis = { ...l.yaxis, zeroline: true, zerolinecolor: pal.zero };
  }, []);
  const data = useMemo(() => ([['mm', 'Managed Money'], ['swap', 'Swap Dealers'], ['pm', 'Producer/Merchant']] as const)
    .map(([k, name]) => ({ type: 'scatter', mode: 'lines', x: p.history.dates, y: p.history[`${k}_net`], name,
                           line: { color: CFTC_COLORS[k], width: 2 } })), [p]);
  return <Plot data={data} layout={layout} height={280} />;
}

/** Generic time-series lines (News price trend, AI price panels). */
export function Lines({ series, unit, height = 300, shapes, y2 }: {
  series: { name: string; x: string[]; y: (number | null)[]; color?: string; dash?: string; width?: number;
            fill?: string; fillcolor?: string; yaxis?: string }[];
  unit: string; height?: number; shapes?: any[]; y2?: string;
}) {
  const { layout } = useLayout(unit, (l, p) => {
    l.xaxis = { ...l.xaxis, type: 'date' };
    if (shapes) l.shapes = shapes;
    if (y2) l.yaxis2 = { title: { text: y2, font: { size: 10.5, color: p.muted } }, overlaying: 'y', side: 'right',
                         showgrid: false, tickfont: { color: p.muted } };
  }, [shapes, y2]);
  const data = useMemo(() => series.map((s) => ({
    type: 'scatter', mode: 'lines', x: s.x, y: s.y, name: s.name, yaxis: s.yaxis,
    line: { color: s.color, width: s.width ?? 1.6, dash: s.dash }, fill: s.fill, fillcolor: s.fillcolor,
  })), [series]);
  return <Plot data={data} layout={layout} height={height} />;
}

export function Bars({ x, y, colors, unit, height = 300, extra, barmode }: {
  x: (string | number)[]; y: (number | null)[]; colors?: string | string[]; unit: string; height?: number; extra?: any[];
  barmode?: 'stack' | 'group';
}) {
  const { layout } = useLayout(unit, (l) => {
    l.xaxis = { ...l.xaxis, type: 'category' };
    l.hovermode = 'closest';
    if (barmode) l.barmode = barmode;
  }, [barmode]);
  const data = useMemo(() => [{ type: 'bar', x, y, marker: { color: colors }, name: unit, showlegend: false },
                               ...(extra ?? [])], [x, y, colors, unit, extra]);
  return <Plot data={data} layout={layout} height={height} />;
}

/** Category-axis lines (AI year resemblance by day of year). */
export function XYLines({ series, xTitle, unit, height = 300 }: {
  series: { name: string; x: number[]; y: (number | null)[]; color?: string; width?: number }[];
  xTitle: string; unit: string; height?: number;
}) {
  const { layout } = useLayout(unit, (l, p) => {
    l.xaxis = { ...l.xaxis, title: { text: xTitle, font: { size: 10.5, color: p.muted } } };
  }, [xTitle]);
  const data = useMemo(() => series.map((s) => ({ type: 'scatter', mode: 'lines', x: s.x, y: s.y, name: s.name,
                                                  line: { color: s.color, width: s.width ?? 1.6 } })), [series]);
  return <Plot data={data} layout={layout} height={height} />;
}
