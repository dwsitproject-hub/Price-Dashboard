import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api, ApiError, UNAUTHORIZED_EVENT } from '../api/client';
import type { AuthConfig, Me } from '../api/types';
import { hub } from '../lib/hubSdk';

interface AuthState {
  config: AuthConfig | null;
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<Me>;
  logout: () => Promise<void>;
  setMe: (me: Me) => void;
  can: (perm: string) => boolean;
  hasModule: (m: string) => boolean;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [me, setMeState] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const qc = useQueryClient();

  const setMe = useCallback((m: Me) => {
    setMeState(m);
    hub.identify(m.hub_sub, m.email); // SDK-02: on every load once the user is known
  }, []);

  useEffect(() => {
    let alive = true;
    Promise.all([
      api.get<AuthConfig>('/auth/config').catch(() => null),
      api.get<Me>('/auth/me').catch((e: ApiError) => (e.status === 401 ? null : Promise.reject(e))),
    ])
      .then(([cfg, m]) => {
        if (!alive) return;
        setConfig(cfg);
        if (m) setMe(m);
      })
      .catch(() => undefined)
      .finally(() => alive && setLoading(false));
    const onUnauthorized = () => {
      setMeState(null);
      qc.clear();
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => {
      alive = false;
      window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    };
  }, [qc, setMe]);

  const login = useCallback(
    async (email: string, password: string) => {
      const m = await api.post<Me>('/auth/login', { email, password });
      setMe(m);
      hub.track('login_succeeded', { method: 'local' });
      return m;
    },
    [setMe],
  );

  const logout = useCallback(async () => {
    try {
      await api.post('/auth/logout');
    } finally {
      hub.reset(); // SDK-03
      setMeState(null);
      qc.clear();
    }
  }, [qc]);

  const value = useMemo<AuthState>(
    () => ({
      config,
      me,
      loading,
      login,
      logout,
      setMe,
      can: (perm) => !!me && (me.permissions.includes('admin') || me.permissions.includes(perm)),
      hasModule: (m) => !!me && me.modules.includes(m),
    }),
    [config, me, loading, login, logout, setMe],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useAuth outside AuthProvider');
  return v;
}
