import type { ReactNode } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { RequireAdmin, RequireAuth, RequireModule } from './auth/guards';
import { useAuth } from './auth/AuthContext';
import { AppShell } from './components/AppShell';
import { FullPageSpinner } from './components/Feedback';
import { ControlsProvider } from './lib/controls';
import { ChangePasswordPage } from './pages/ChangePasswordPage';
import { ForwardCurvesPage } from './pages/ForwardCurvesPage';
import { LoginPage } from './pages/LoginPage';
import { ModulePage } from './pages/ModulePage';
import { OiVolumePage } from './pages/OiVolumePage';
import { OverviewPage } from './pages/OverviewPage';
import { NotFoundPage } from './pages/StatusPages';
import { TechnicalAnalysisPage } from './pages/TechnicalAnalysisPage';
import { AuditPage } from './pages/admin/AuditPage';
import { PipelinePage } from './pages/admin/PipelinePage';
import { RolesPage } from './pages/admin/RolesPage';
import { SettingsPage } from './pages/admin/SettingsPage';
import { UsersPage } from './pages/admin/UsersPage';
import { useCatalog } from './components/AppShell';

function Shell({ title, children }: { title: string; children: ReactNode }) {
  return <AppShell title={title}>{children}</AppShell>;
}

function ModuleShell() {
  const loc = useLocation();
  const { data } = useCatalog();
  const code = loc.pathname.slice(3);
  const name = data?.modules.find((m) => m.code === code)?.name ?? 'Module';
  return <AppShell title={name}><ModulePage /></AppShell>;
}

export function App() {
  const { me, loading } = useAuth();
  if (loading) return <FullPageSpinner />;
  const ready = !!me && !me.must_change_password;
  return (
    <ControlsProvider enabled={ready}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/change-password" element={<RequireAuth><ChangePasswordPage /></RequireAuth>} />
        <Route path="/" element={<RequireAuth><Shell title="Market Overview"><RequireModule module="overview"><OverviewPage /></RequireModule></Shell></RequireAuth>} />
        <Route path="/m/:code" element={<RequireAuth><ModuleShell /></RequireAuth>} />
        <Route path="/forward-curves" element={<RequireAuth><Shell title="Forward Curves"><RequireModule module="forward_curves"><ForwardCurvesPage /></RequireModule></Shell></RequireAuth>} />
        <Route path="/oi-volume" element={<RequireAuth><Shell title="OI / Volume"><RequireModule module="oi_volume"><OiVolumePage /></RequireModule></Shell></RequireAuth>} />
        <Route path="/technical-analysis" element={<RequireAuth><Shell title="Technical Analysis"><RequireModule module="technical_analysis"><TechnicalAnalysisPage /></RequireModule></Shell></RequireAuth>} />
        <Route path="/admin/users" element={<RequireAuth><Shell title="Users & access"><RequireAdmin><UsersPage /></RequireAdmin></Shell></RequireAuth>} />
        <Route path="/admin/roles" element={<RequireAuth><Shell title="Roles & modules"><RequireAdmin><RolesPage /></RequireAdmin></Shell></RequireAuth>} />
        <Route path="/admin/audit" element={<RequireAuth><Shell title="Audit log"><RequireAdmin><AuditPage /></RequireAdmin></Shell></RequireAuth>} />
        <Route path="/admin/pipeline" element={<RequireAuth><Shell title="Data pipeline"><RequireAdmin perm="pipeline.run"><PipelinePage /></RequireAdmin></Shell></RequireAuth>} />
        <Route path="/admin/settings" element={<RequireAuth><Shell title="Settings"><RequireAdmin><SettingsPage /></RequireAdmin></Shell></RequireAuth>} />
        <Route path="/index.html" element={<Navigate to="/" replace />} />
        <Route path="*" element={<RequireAuth><Shell title="Not found"><NotFoundPage /></Shell></RequireAuth>} />
      </Routes>
    </ControlsProvider>
  );
}

