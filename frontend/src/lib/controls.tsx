import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { YearsInfo } from '../api/types';
import { load, save } from './storage';
import { hub } from './hubSdk';

export type Unit = 'usd' | 'local';
export type Theme = 'system' | 'light' | 'dark';

interface Controls {
  available: number[];
  years: number[];
  setYears: (y: number[]) => void;
  unit: Unit;
  setUnit: (u: Unit) => void;
  theme: Theme;
  setTheme: (t: Theme) => void;
  asOf: string | null;
  dataVersion: number;
  currentYear: number;
}

const Ctx = createContext<Controls | null>(null);

export function ControlsProvider({ children, enabled }: { children: ReactNode; enabled: boolean }) {
  const { data } = useQuery({
    queryKey: ['years'],
    queryFn: ({ signal }) => api.get<YearsInfo>('/market/years', signal),
    enabled,
    refetchInterval: 60_000,
  });
  const [stored, setStored] = useState<number[] | null>(() => load<number[] | null>('years', null));
  const [unit, setUnitState] = useState<Unit>(() => load<Unit>('unit', 'usd'));
  const [theme, setThemeState] = useState<Theme>(() => load<Theme>('theme', 'system'));

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', theme);
    window.dispatchEvent(new Event('aastar:theme'));
  }, [theme]);

  const available = data?.years ?? [];
  const years = useMemo(() => {
    const valid = (stored ?? []).filter((y) => available.includes(y));
    return valid.length ? valid : (data?.default ?? []);
  }, [stored, available, data?.default]);

  const setYears = useCallback((y: number[]) => {
    const sorted = [...new Set(y)].sort();
    setStored(sorted);
    save('years', sorted);
    hub.track('years_changed', { count: sorted.length });
  }, []);
  const setUnit = useCallback((u: Unit) => {
    setUnitState(u);
    save('unit', u);
    hub.track('unit_toggled', { unit: u });
  }, []);
  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    save('theme', t);
  }, []);

  const value: Controls = {
    available, years, setYears, unit, setUnit, theme, setTheme,
    asOf: data?.as_of ?? null, dataVersion: data?.data_version ?? 0,
    currentYear: available.length ? available[available.length - 1] : new Date().getFullYear(),
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useControls(): Controls {
  const v = useContext(Ctx);
  if (!v) throw new Error('useControls outside ControlsProvider');
  return v;
}

/** Resolved light/dark for chart colours, tracking OS changes and the manual toggle. */
export function useResolvedTheme(): 'light' | 'dark' {
  const read = () => {
    const attr = document.documentElement.getAttribute('data-theme');
    if (attr === 'light' || attr === 'dark') return attr;
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  };
  const [t, setT] = useState<'light' | 'dark'>(read);
  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)');
    const on = () => setT(read());
    mq?.addEventListener('change', on);
    window.addEventListener('aastar:theme', on);
    return () => {
      mq?.removeEventListener('change', on);
      window.removeEventListener('aastar:theme', on);
    };
  }, []);
  return t;
}
