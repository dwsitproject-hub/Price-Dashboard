/** localStorage for per-viewer conveniences only (years, unit, theme). Every access is guarded. */
export function load<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(`aastar.${key}`);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function save(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(`aastar.${key}`, JSON.stringify(value));
  } catch {
    /* private mode / blocked storage */
  }
}
