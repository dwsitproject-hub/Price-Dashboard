import { useEffect, useRef, useState } from 'react';

/* eslint-disable @typescript-eslint/no-explicit-any */
type PlotlyLib = any;
let plotlyPromise: Promise<PlotlyLib> | null = null;
const loadPlotly = () => (plotlyPromise ??= import('plotly.js-basic-dist-min').then((m) => m.default ?? m));

/**
 * Minimal Plotly wrapper. The chart library is lazy-loaded (PERF-04) and bundled — never a CDN.
 * Each chart sits alone in its own fixed-height grid cell, so the legacy dcc.Graph flex-resize
 * feedback loop cannot happen here.
 */
export function Plot({ data, layout, height = 300 }: { data: any[]; layout: any; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [lib, setLib] = useState<PlotlyLib | null>(null);

  useEffect(() => {
    let alive = true;
    loadPlotly().then((p) => alive && setLib(p));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!lib || !ref.current) return;
    lib.react(ref.current, data, { ...layout, height, autosize: true }, {
      displayModeBar: false, responsive: true, displaylogo: false,
    });
  }, [lib, data, layout, height]);

  useEffect(() => {
    const el = ref.current;
    if (!lib || !el) return;
    const ro = new ResizeObserver(() => lib.Plots.resize(el));
    ro.observe(el);
    return () => {
      ro.disconnect();
      lib.purge(el);
    };
  }, [lib]);

  return (
    <div ref={ref} style={{ width: '100%', height }}>
      {!lib && <div className="skeleton" style={{ height: height - 16, margin: 8 }} />}
    </div>
  );
}
