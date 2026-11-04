import { useId } from 'react';

/** AASTAR mark: palm-green badge, gold oil droplet with a rising price line, four-point star. */
export function LogoMark({ size = 36 }: { size?: number }) {
  const id = useId().replace(/:/g, '');
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="AASTAR">
      <defs>
        <linearGradient id={`bg${id}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#14876F" />
          <stop offset="1" stopColor="#073A33" />
        </linearGradient>
        <linearGradient id={`dr${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#F7CB5C" />
          <stop offset="1" stopColor="#D88A0A" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="15" fill={`url(#bg${id})`} />
      <rect x="0.75" y="0.75" width="62.5" height="62.5" rx="14.3" fill="none" stroke="rgba(255,255,255,.12)" strokeWidth="1.5" />
      <path d="M30 11c0 0-13.5 15.6-13.5 26.2a13.5 13.5 0 0 0 27 0C43.5 26.6 30 11 30 11z" fill={`url(#dr${id})`} />
      <path d="M21.5 43.5l5.2-5.6 3.6 3.1 6.9-10.4 4.3 4.6" fill="none" stroke="#fff" strokeWidth="3.2"
            strokeLinecap="round" strokeLinejoin="round" />
      <path d="M50 7.5l1.7 5.6 5.6 1.7-5.6 1.7L50 22.1l-1.7-5.6-5.6-1.7 5.6-1.7z" fill="#F7CB5C" />
      <circle cx="41.6" cy="34.7" r="2.1" fill="#fff" />
    </svg>
  );
}

export function LogoLockup({ size = 36, tone = 'light' }: { size?: number; tone?: 'light' | 'onDark' }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      <LogoMark size={size} />
      <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.05 }}>
        <b style={{ fontSize: size * 0.53, letterSpacing: '.12em', fontWeight: 800,
                    color: tone === 'onDark' ? '#fff' : 'var(--brand-700)' }}>AASTAR</b>
        <span style={{ fontSize: Math.max(9, size * 0.26), letterSpacing: '.28em', fontWeight: 700, marginTop: 3,
                       color: tone === 'onDark' ? 'var(--accent-400)' : 'var(--accent-600)' }}>PRICE INTELLIGENCE</span>
      </div>
    </div>
  );
}
