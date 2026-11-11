import { lazy, Suspense, type ReactNode } from 'react';
import { Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom';
import { RequireAdmin, RequireAuth, RequireModule } from './auth/guards';
import { useAuth } from './auth/AuthContext';
import { AppShell } from './components/AppShell';
import { FullPageSpinner } from './components/Feedback';
import { ControlsProvider } from './lib/controls';
import { ChangePasswordPage } from './pages/ChangePasswordPage';
import { ForwardCurvesPage } from './pages/ForwardCurvesPage';
import { YtdPage } from './pages/YtdPage';
// Intelligence sections load on demand (map data, markdown renderer) to keep the main bundle small.
const AiPage = lazy(() => import('./pages/AiPage').then((m) => ({ default: m.AiPage })));
const AtlasPage = lazy(() => import('./pages/AtlasPage').then((m) => ({ default: m.AtlasPage })));
const CalendarPage = lazy(() => import('./pages/CalendarPage').then((m) => ({ default: m.CalendarPage })));
const CftcPage = lazy(() => import('./pages/CftcPage').then((m) => ({ default: m.CftcPage })));
const NewsPage = lazy(() => import('./pages/NewsPage').then((m) => ({ default: m.NewsPage })));
const SndPage = lazy(() => import('./pages/SndPage').then((m) => ({ default: m.SndPage })));
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
  return <AppShell title={title}><Suspense fallback={<div className="card skeleton" style={{ height: 320 }} />}>{children}</Suspense></AppShell>;
}

function ModuleShell() {
  const loc = useLocation();
  const { data } = useCatalog();
  const code = loc.pathname.slice(3);
  const name = data?.modules.find((m) => m.code === code)?.name ?? 'Module';
  return <AppShell title={name}><ModulePage /></AppShell>;
}

/** Forward Curve sub-view of Calendar Spreads / Outright Prices (outright curves) or Product Spreads. */
function ForwardShell() {
  const { code = '' } = useParams();
  const { data } = useCatalog();
  const hosts: Record<string, 'outright' | 'product_spread'> = {
    calendar_spreads: 'outright', outright_prices: 'outright', product_spreads: 'product_spread',
  };
  const kind = hosts[code];
  const name = data?.modules.find((m) => m.code === code)?.name ?? 'Module';
  if (!kind) return <Shell title="Not found"><NotFoundPage /></Shell>;
  return (
    <Shell title={`${name} · Forward Curve`}>
      <RequireModule module="forward_curves"><ForwardCurvesPage key={kind} kind={kind} /></RequireModule>
    </Shell>
  );
}

export function App() {
  const { me, loading } = useAuth();
  if (loading) return <FullPageSpinner />;
  const ready = !!me;
  return (
    <ControlsProvider enabled={ready}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/change-password" element={<RequireAuth><ChangePasswordPage /></RequireAuth>} />
        <Route path="/" element={<RequireAuth><Shell title="Market Overview"><RequireModule module="overview"><OverviewPage /></RequireModule></Shell></RequireAuth>} />
        <Route path="/m/:code" element={<RequireAuth><ModuleShell /></RequireAuth>} />
        <Route path="/m/:code/forward" element={<RequireAuth><ForwardShell /></RequireAuth>} />
        <Route path="/m/calendar_spreads/ytd" element={<RequireAuth><Shell title="Calendar Spreads · YTD"><RequireModule module="calendar_spreads"><YtdPage /></RequireModule></Shell></RequireAuth>} />
        <Route path="/forward-curves" element={<Navigate to="/m/outright_prices/forward" replace />} />
        <Route path="/snd" element={<RequireAuth><Shell title="SND · Graphs"><RequireModule module="snd"><SndPage key="g" view="graphs" /></RequireModule></Shell></RequireAuth>} />
        <Route path="/snd/exports" element={<RequireAuth><Shell title="SND · Exports"><RequireModule module="snd"><SndPage key="e" view="exports" /></RequireModule></Shell></RequireAuth>} />
        <Route path="/snd/tables" element={<RequireAuth><Shell title="SND · Tabular"><RequireModule module="snd"><SndPage key="t" view="tables" /></RequireModule></Shell></RequireAuth>} />
        <Route path="/cftc" element={<RequireAuth><Shell title="CFTC"><RequireModule module="cftc"><CftcPage /></RequireModule></Shell></RequireAuth>} />
        <Route path="/ai" element={<RequireAuth><Shell title="AI Analysis"><RequireModule module="ai_analysis"><AiPage /></RequireModule></Shell></RequireAuth>} />
        <Route path="/news" element={<RequireAuth><Shell title="News"><RequireModule module="news"><NewsPage /></RequireModule></Shell></RequireAuth>} />
        <Route path="/calendar" element={<RequireAuth><Shell title="Calendar"><RequireModule module="calendar"><CalendarPage /></RequireModule></Shell></RequireAuth>} />
        <Route path="/atlas" element={<RequireAuth><Shell title="HVO / SAF Atlas"><RequireModule module="hvo_atlas"><AtlasPage /></RequireModule></Shell></RequireAuth>} />
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

