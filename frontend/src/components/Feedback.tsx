import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import { Icon } from './Icon';

export function FullPageSpinner() {
  return (
    <div style={{ display: 'grid', placeItems: 'center', height: '100%' }}>
      <div className="spinner" aria-label="Loading" />
    </div>
  );
}

export function Empty({ title, children, icon = 'inbox' }: { title: string; children?: ReactNode; icon?: string }) {
  return (
    <div className="empty">
      <Icon name={icon} size={26} />
      <b>{title}</b>
      {children && <div style={{ fontSize: 12.5 }}>{children}</div>}
    </div>
  );
}

/** Shown instead of chart placeholders while the database holds no published market data. */
export function NoMarketData({ canRunPipeline }: { canRunPipeline: boolean }) {
  return (
    <div className="card" style={{ gridColumn: '1 / -1' }}>
      <Empty title="No market data yet" icon="database">
        The data pipeline has not published any prices yet. The first LSEG backfill (2015 to today) can take an
        hour or more; charts appear here as soon as it finishes.
        {canRunPipeline && <> Follow its progress under <Link to="/admin/pipeline">Data pipeline</Link>.</>}
      </Empty>
    </div>
  );
}

export function ErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as ApiError;
  return (
    <div className="alert alert-error" role="alert">
      <Icon name="alert" size={16} />
      <div>
        {e.message || 'Something went wrong.'}
        {e.requestId && <div className="mono faint" style={{ fontSize: 11 }}>ref {e.requestId}</div>}
      </div>
    </div>
  );
}

const ToastCtx = createContext<(msg: string) => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<{ id: number; msg: string }[]>([]);
  const push = useCallback((msg: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, msg }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast-stack" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className="toast">{t.msg}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);
