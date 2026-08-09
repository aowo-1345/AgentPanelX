import { Settings } from 'lucide-react';
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router-dom';
import { ErrorBoundary } from '@/components/common/ErrorBoundary';
import { BoardPage } from '@/pages/BoardPage';
import { SettingsPage } from '@/pages/SettingsPage';
import { WorkspacePage } from '@/pages/WorkspacePage';

function TopNav() {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <nav className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-background px-4">
      <div className="flex items-center gap-3">
        <button
          className="text-sm font-semibold tracking-tight text-foreground transition-colors hover:text-primary"
          onClick={() => navigate('/')}
        >
          AgentPlaneX
        </button>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="h-1.5 w-1.5 rounded-full bg-slate-500" />
          <span>Codex · local runtime</span>
        </div>
      </div>
      {location.pathname !== '/settings' && (
        <button
          className="btn btn-ghost h-8 w-8 p-0"
          onClick={() => navigate('/settings')}
          aria-label="Open project settings"
        >
          <Settings className="h-4 w-4" />
        </button>
      )}
    </nav>
  );
}

function AppShell() {
  return (
    <div className="flex h-screen min-h-[600px] flex-col overflow-hidden bg-background text-foreground">
      <TopNav />
      <main className="min-h-0 flex-1 overflow-hidden">
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<BoardPage />} />
            <Route
              path="/projects/:projectId/features/:triageId"
              element={<WorkspacePage />}
            />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}
