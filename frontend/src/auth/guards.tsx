import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './AuthContext';
import { FullPageSpinner } from '../components/Feedback';
import { ForbiddenPage } from '../pages/StatusPages';

export function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <FullPageSpinner />;
  if (!me) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  if (me.must_change_password && loc.pathname !== '/change-password') {
    return <Navigate to="/change-password" replace />;
  }
  return <>{children}</>;
}

export function RequireModule({ module, children }: { module: string; children: ReactNode }) {
  const { hasModule } = useAuth();
  return hasModule(module) ? <>{children}</> : <ForbiddenPage />;
}

export function RequireAdmin({ children, perm = 'admin' }: { children: ReactNode; perm?: string }) {
  const { can } = useAuth();
  return can(perm) ? <>{children}</> : <ForbiddenPage />;
}
