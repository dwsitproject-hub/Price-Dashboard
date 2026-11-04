export function fmtNum(v: number | null | undefined, digits?: number): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '–';
  const d = digits ?? (Math.abs(v) < 200 ? 2 : 0);
  return v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}

export function fmtSigned(v: number | null | undefined, digits?: number): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '–';
  return (v > 0 ? '+' : '') + fmtNum(v, digits);
}

export function fmtInt(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '–';
  return Math.round(v).toLocaleString('en-US');
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '–';
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '–';
  return new Date(iso).toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

export function relTime(iso: string | null | undefined): string {
  if (!iso) return 'never';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]!.toUpperCase()).join('') || '?';
}
