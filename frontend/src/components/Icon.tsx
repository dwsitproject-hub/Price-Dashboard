/** Small inline stroke-icon set (bundled — no icon CDN, SEC-08). 24×24 grid, 1.8 stroke. */
const PATHS: Record<string, string> = {
  gauge: 'M12 14l3.5-3.5M4.9 19a9 9 0 1 1 14.2 0M12 4v2M4 12H6m12 0h2M6.3 6.3l1.4 1.4m8.6-1.4-1.4 1.4',
  line: 'M3 3v18h18M7 14l3-3 3 3 5-6',
  curve: 'M3 20c4-1 5-6 8-9s6-4 10-5M3 3v18h18',
  calendar: 'M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zm3 10h2m4 0h2m-8 4h2',
  swap: 'M7 4 3 8l4 4M3 8h14m0 12 4-4-4-4m4 4H7',
  layers: 'm12 2 9 5-9 5-9-5 9-5zm-9 10 9 5 9-5M3 17l9 5 9-5',
  scale: 'M12 3v18M7 21h10M5 7h14M5 7l-3 7a3 3 0 0 0 6 0L5 7zm14 0-3 7a3 3 0 0 0 6 0l-3-7z',
  factory: 'M2 20h20M4 20V9l5 3V9l5 3V5h4l1 15M8 16h1m3 0h1m3 0h1',
  signal: 'M2 20h.01M7 20v-4m5 4v-8m5 8V8m5 12V4',
  bars: 'M3 3v18h18M8 17V9m4 8V5m4 12v-6',
  target: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zm0-4a6 6 0 1 0 0-12 6 6 0 0 0 0 12zm0-4a2 2 0 1 0 0-4 2 2 0 0 0 0 4z',
  users: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m7-10a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm13 10v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zm-3-10 2 2 4-4',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  database: 'M12 8c4.4 0 8-1.3 8-3s-3.6-3-8-3-8 1.3-8 3 3.6 3 8 3zM4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm7.4-3a7.4 7.4 0 0 0-.1-1.3l2-1.6-2-3.4-2.4 1a7.6 7.6 0 0 0-2.2-1.3L14.4 3h-4l-.4 2.4a7.6 7.6 0 0 0-2.2 1.3l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.6l-2 1.6 2 3.4 2.4-1a7.6 7.6 0 0 0 2.2 1.3l.4 2.4h4l.4-2.4a7.6 7.6 0 0 0 2.2-1.3l2.4 1 2-3.4-2-1.6c.1-.4.1-.9.1-1.3z',
  logout: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4m7 14 5-5-5-5m5 5H9',
  key: 'M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.8 7.8 5.5 5.5 0 0 1 7.8-7.8zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3m-3.5 3.5L19 4',
  menu: 'M3 6h18M3 12h18M3 18h18',
  sun: 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zm0-15v2m0 16v2M4.2 4.2l1.4 1.4m12.8 12.8 1.4 1.4M2 12h2m16 0h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4',
  moon: 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z',
  monitor: 'M4 4h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zm4 16h8m-4-4v4',
  download: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4m4-5 5 5 5-5m-5 5V3',
  alert: 'M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
  check: 'M20 6 9 17l-5-5',
  x: 'M18 6 6 18M6 6l12 12',
  inbox: 'M22 12h-6l-2 3h-4l-2-3H2m3.5-7h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6l3.5-7z',
  refresh: 'M21 12a9 9 0 0 1-15.5 6.2L3 16m0 5v-5h5M3 12a9 9 0 0 1 15.5-6.2L21 8m0-5v5h-5',
  play: 'm6 4 14 8-14 8V4z',
  lock: 'M5 11h14v10H5zm3 0V7a4 4 0 0 1 8 0v4',
  unlock: 'M5 11h14v10H5zm3 0V7a4 4 0 0 1 7.9-1',
  copy: 'M9 9h11v11H9zM5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1',
  chevron: 'm6 9 6 6 6-6',
  external: 'M15 3h6v6m-11 5L21 3m-3 10v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6',
  bug: 'M8 2l1.9 1.9M16 2l-1.9 1.9M9 7.1V6a3 3 0 0 1 6 0v1.1M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6zm0 0v-9M6.5 13H3m18 0h-3.5M6 9.5 3.5 8m14.5 1.5L20.5 8M6 17l-2.5 1.5M18 17l2.5 1.5',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zm10 2-4.3-4.3',
  plus: 'M12 5v14M5 12h14',
  link: 'M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7',
  clock: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zm0-14v4l3 3',
};

export function Icon({ name, size = 18, className }: { name: string; size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>
      <path d={PATHS[name] ?? PATHS.inbox} />
    </svg>
  );
}
