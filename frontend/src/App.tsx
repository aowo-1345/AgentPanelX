import { LayoutDashboard, Settings, Sparkles } from 'lucide-react';
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router-dom';
import { ErrorBoundary } from '@/components/common/ErrorBoundary';
import { BrandMark } from '@/components/common/BrandMark';
import { BoardPage } from '@/pages/BoardPage';
import { LandingPage } from '@/pages/LandingPage';
import { SettingsPage } from '@/pages/SettingsPage';
import { ShowcasePage } from '@/pages/ShowcasePage';
import { WorkspacePage } from '@/pages/WorkspacePage';

const staticSite = import.meta.env.VITE_PUBLIC_STATIC_SITE === 'true';

function TopNav() {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <nav className="relative z-30 flex h-14 shrink-0 items-center justify-between border-b border-white/[0.07] bg-[#07080b]/95 px-5 shadow-[0_1px_0_rgba(255,255,255,0.015)] backdrop-blur-xl">
      <div className="flex items-center gap-3">
        <button
          className="flex items-center gap-2 text-sm font-semibold tracking-tight text-foreground transition-colors hover:text-primary"
          onClick={() => navigate('/')}
        >
          <BrandMark className="h-7 w-7 rounded-[9px]" />
          AgentPanelX
        </button>
        <div className="flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.025] px-2.5 py-1 text-[10px] text-white/38">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400/70 shadow-[0_0_8px_rgba(52,211,153,0.35)]" />
          <span>Codex · local runtime</span>
        </div>
      </div>
      <div className="flex items-center gap-1">
        {location.pathname === '/showcase' ? (
          <>
            <button className="btn btn-ghost h-8" onClick={() => navigate('/')}>
              Homepage
            </button>
            {!staticSite && (
              <button className="btn btn-ghost h-8" onClick={() => navigate('/console')}>
                <LayoutDashboard className="h-3.5 w-3.5" />
                Live console
              </button>
            )}
          </>
        ) : (
          <button className="btn btn-ghost h-8" onClick={() => navigate('/showcase')}>
            <Sparkles className="h-3.5 w-3.5" />
            Showcase
          </button>
        )}
        {location.pathname !== '/settings' && location.pathname !== '/showcase' && (
          <button
            className="btn btn-ghost h-8 w-8 p-0"
            onClick={() => navigate('/settings')}
            aria-label="Open project settings"
          >
            <Settings className="h-4 w-4" />
          </button>
        )}
      </div>
    </nav>
  );
}

function AppShell() {
  const location = useLocation();
  const landing = location.pathname === '/';

  return (
    <div
      className={
        landing
          ? 'min-h-screen bg-background text-foreground'
          : "flex h-screen min-h-[600px] flex-col overflow-hidden bg-[#07080b] font-['Manrope_Variable',ui-sans-serif,system-ui,sans-serif] text-foreground"
      }
    >
      {!landing && <TopNav />}
      <main className={landing ? 'min-h-screen' : 'min-h-0 flex-1 overflow-hidden'}>
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<LandingPage />} />
            <Route path="/console" element={<BoardPage />} />
            <Route
              path="/projects/:projectId/features/:triageId"
              element={<WorkspacePage />}
            />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/showcase" element={<ShowcasePage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  );
}

export default function App() {
  const basename = import.meta.env.BASE_URL.replace(/\/$/, '') || '/';

  return (
    <BrowserRouter basename={basename}>
      <AppShell />
    </BrowserRouter>
  );
}
