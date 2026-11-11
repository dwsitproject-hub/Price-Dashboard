import { useMemo } from 'react';
import type { Curve, SeriesPayload, TaPayload, Trace, YtdProduct } from '../api/types';
import { useResolvedTheme } from '../lib/controls';
import { Plot } from './Plot';

/* eslint-disable @typescript-eslint/no-explicit-any */

export function palette(theme: 'light' | 'dark') {
  return theme === 'dark'
    ? { ink: '#c8d4d7', muted: '#93a4aa', grid: '#1a272c', zero: '#2a3b41', bg: 'rgba(0,0,0,0)', accent: '#f2b84b',
        old: [120, 146, 190], mid: [43, 179, 156], hover: '#142126' }
    : { ink: '#2b3a40', muted: '#5a6b71', grid: '#edf1f0', zero: '#cfd9d6', bg: 'rgba(0,0,0,0)', accent: '#e3a21a',
        old: [104, 130, 178], mid: [14, 128, 110], hover: '#ffffff' };
}

/**
 * Legacy seasonal encoding with the brand palette: oldest → newest slate-blue → palm-green,
 * the most recent year in gold and thickest, previous year medium, older years thinner + fading.
 */
export function yearStyle(year: number, years: number[], theme: 'light' | 'dark') {
  const p = palette(theme);
  const sorted = [...years].sort();
  const latest = sorted[sorted.length - 1];
  if (year === latest) return { color: p.accent, width: 2.75, opacity: 1 };
  const older = sorted.slice(0, -1);
  const t = older.length <= 1 ? 1 : older.indexOf(year) / (older.length - 1);
  const c = p.old.map((o, i) => Math.round(o + (p.mid[i] - o) * t));
  const age = latest - year;
  return { color: `rgb(${c.join(',')})`, width: year === latest - 1 ? 1.6 : 1.1, opacity: Math.max(0.3, 1 - age * 0.07) };
}

export function baseLayout(theme: 'light' | 'dark', unit: string) {
  const p = palette(theme);
  return {
    paper_bgcolor: p.bg, plot_bgcolor: p.bg,
    font: { family: 'Inter, Segoe UI, system-ui, sans-serif', size: 11, color: p.ink },
    margin: { l: 52, r: 12, t: 8, b: 32 },
    hovermode: 'x unified',
    hoverlabel: { font: { size: 11 }, bgcolor: p.hover, bordercolor: p.zero },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10.5, color: p.muted }, itemclick: 'toggle' },
    yaxis: { title: { text: unit, font: { size: 10.5, color: p.muted } }, gridcolor: p.grid, zerolinecolor: p.zero,
             tickfont: { color: p.muted }, automargin: true },
    xaxis: { gridcolor: p.grid, tickfont: { color: p.muted }, zeroline: false },
  };
}

export function SeasonalChart({ payload, height = 290, band }: { payload: SeriesPayload; height?: number; band?: boolean }) {
  const theme = useResolvedTheme();
  const { data, layout } = useMemo(() => {
    const years = payload.traces.map((t) => t.year);
    const data: any[] = payload.traces.map((t: Trace) => {
      const st = yearStyle(t.year, years, theme);
      return {
        type: 'scatter', mode: 'lines', x: t.x, y: t.y, name: String(t.year),
        line: { color: st.color, width: st.width }, opacity: st.opacity,
        hovertemplate: `<b>${t.year}</b>: %{y:,.2f}<extra></extra>`,
      };
    });
    const layout: any = baseLayout(theme, payload.unit);
    layout.xaxis = { ...layout.xaxis, type: 'date', tickformat: '%b', dtick: 'M1', hoverformat: '%d %b',
                     range: ['2000-01-01', '2000-12-31'] };
    if (band && payload.traces.length) {
      const all = payload.traces.flatMap((t) => t.y).filter((v): v is number => v !== null);
      const mean = all.reduce((a, b) => a + b, 0) / all.length;
      const sd = Math.sqrt(all.reduce((a, b) => a + (b - mean) ** 2, 0) / all.length);
      const p = palette(theme);
      layout.shapes = [
        { type: 'rect', xref: 'paper', x0: 0, x1: 1, y0: mean - 2 * sd, y1: mean + 2 * sd, layer: 'below',
          fillcolor: theme === 'dark' ? 'rgba(43,179,156,0.07)' : 'rgba(14,128,110,0.06)', line: { width: 0 } },
        { type: 'line', xref: 'paper', x0: 0, x1: 1, y0: mean, y1: mean, line: { color: p.zero, width: 1, dash: 'dot' } },
      ];
    }
    return { data, layout };
  }, [payload, theme, band]);
  return <Plot data={data} layout={layout} height={height} />;
}

export function ForwardCurveChart({ curve, height = 290 }: { curve: Curve; height?: number }) {
  const theme = useResolvedTheme();
  const { data, layout } = useMemo(() => {
    const years = curve.traces.map((t) => t.year);
    const data = curve.traces.map((t) => {
      const st = yearStyle(t.year, years, theme);
      return {
        type: 'scatter', mode: 'lines+markers', x: t.x, y: t.y, name: `${t.year} (${t.as_of})`,
        line: { color: st.color, width: st.width }, marker: { size: 6, color: st.color }, opacity: st.opacity,
        hovertemplate: `<b>${t.year}</b> as of ${t.as_of}<br>%{x}: %{y:,.2f}<extra></extra>`,
      };
    });
    const layout: any = baseLayout(theme, curve.unit);
    layout.xaxis = { ...layout.xaxis, type: 'category' };
    layout.hovermode = 'closest';
    return { data, layout };
  }, [curve, theme]);
  return <Plot data={data} layout={layout} height={height} />;
}

// Legacy colour sequences (dashboard._YTD_LEG_COLORS / _COMPARE_COLORS), kept so the views read the same.
const YTD_LEG_COLORS = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b'];
const COMPARE_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948',
                        '#17becf', '#bcbd22', '#8c564b', '#7f7f7f'];

/** Calendar Spreads → YTD: C1−CN legs on actual trading dates, C1 price dotted on the right-hand axis. */
export function YtdChart({ item, height = 320 }: { item: YtdProduct; height?: number }) {
  const theme = useResolvedTheme();
  const { data, layout } = useMemo(() => {
    const p = palette(theme);
    const data: any[] = item.legs.map((l, i) => ({
      type: 'scatter', mode: 'lines', x: l.x, y: l.y, name: l.label,
      line: { color: YTD_LEG_COLORS[i % YTD_LEG_COLORS.length], width: 1.8 },
      hovertemplate: `<b>${l.label}:</b> %{y:,.2f}<extra></extra>`,
    }));
    if (item.front) {
      data.push({
        type: 'scatter', mode: 'lines', x: item.front.x, y: item.front.y, name: 'C1 Price', yaxis: 'y2',
        line: { color: theme === 'dark' ? '#9aa6aa' : '#888888', width: 1.5, dash: 'dot' },
        hovertemplate: '<b>C1 Price:</b> %{y:,.2f}<extra></extra>',
      });
    }
    const layout: any = baseLayout(theme, item.unit);
    layout.margin = { ...layout.margin, r: 58 };
    layout.xaxis = { ...layout.xaxis, type: 'date', tickformat: '%b', dtick: 'M1', hoverformat: '%d %b %Y' };
    layout.yaxis = { ...layout.yaxis, zeroline: true };
    if (item.front) {
      layout.yaxis2 = { title: { text: `C1 (${item.unit})`, font: { size: 10.5, color: p.muted } }, overlaying: 'y',
                        side: 'right', showgrid: false, zeroline: false, tickfont: { color: p.muted }, automargin: true };
    }
    return { data, layout };
  }, [item, theme]);
  return <Plot data={data} layout={layout} height={height} />;
}

/** Forward Curve → comparison: every instrument's curve for one year on a shared tenor axis (always USD/MT). */
export function ForwardCompareChart({ curves, year, height = 380 }: { curves: Curve[]; year: number; height?: number }) {
  const theme = useResolvedTheme();
  const { data, layout } = useMemo(() => {
    const data: any[] = [];
    curves.forEach((c, i) => {
      const t = c.traces.find((tr) => tr.year === year);
      if (!t) return;
      const name = c.title.replace(/ Forward Curve$/, '');
      data.push({
        type: 'scatter', mode: 'lines+markers', x: t.x, y: t.y, name,
        line: { color: COMPARE_COLORS[i % COMPARE_COLORS.length], width: 2 }, marker: { size: 6 },
        hovertemplate: `<b>${name}</b> as of ${t.as_of}<br>%{x}: %{y:,.2f}<extra></extra>`,
      });
    });
    const layout: any = baseLayout(theme, 'USD/MT');
    layout.xaxis = { ...layout.xaxis, type: 'category' };
    layout.legend = { ...layout.legend, orientation: 'v', x: 1.01, y: 1 };
    layout.margin = { ...layout.margin, r: 150 };
    return { data, layout };
  }, [curves, year, theme]);
  return <Plot data={data} layout={layout} height={height} />;
}

export function TaChart({ ta, height = 460 }: { ta: TaPayload; height?: number }) {
  const theme = useResolvedTheme();
  const { data, layout } = useMemo(() => {
    const p = palette(theme);
    const data: any[] = [
      { type: 'scatter', mode: 'lines', x: ta.dates, y: ta.price, name: 'Price',
        line: { color: theme === 'dark' ? '#48c9b1' : '#0e6b5c', width: 1.8 } },
      { type: 'scatter', mode: 'lines', x: ta.dates, y: ta.sma50, name: '50D SMA', line: { color: '#e3a21a', width: 1.4 } },
      { type: 'scatter', mode: 'lines', x: ta.dates, y: ta.sma100, name: '100D SMA', line: { color: '#c0392b', width: 1.2 } },
      { type: 'scatter', mode: 'lines', x: ta.dates, y: ta.sma200, name: '200D SMA', line: { color: '#5b7db1', width: 1.2 } },
    ];
    const layout: any = baseLayout(theme, ta.unit ?? '');
    layout.xaxis = { ...layout.xaxis, type: 'date' };
    layout.shapes = ta.levels.map((lvl) => ({
      type: 'line', xref: 'paper', x0: 0, x1: 1, y0: lvl, y1: lvl,
      line: { color: theme === 'dark' ? '#b48ce6' : '#7b4bb7', width: 1.3, dash: 'dash' },
    }));
    layout.annotations = ta.levels.map((lvl) => ({
      xref: 'paper', x: 1, y: lvl, xanchor: 'right', yanchor: 'bottom', showarrow: false,
      text: lvl.toLocaleString('en-US', { maximumFractionDigits: 2 }), font: { size: 10, color: p.muted },
    }));
    return { data, layout };
  }, [ta, theme]);
  return <Plot data={data} layout={layout} height={height} />;
}

export function Sparkline({ values, up }: { values: number[]; up: boolean }) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 100},${30 - ((v - min) / span) * 28}`).join(' ');
  const color = up ? 'var(--pos)' : 'var(--neg)';
  return (
    <svg className="spark" viewBox="0 0 100 32" preserveAspectRatio="none" aria-hidden="true">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
