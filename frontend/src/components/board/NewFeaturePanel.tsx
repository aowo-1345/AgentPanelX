import { AlertCircle, CheckCircle2, Loader2, PlusCircle, Settings } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, readableError } from '@/api/client';
import type { Project } from '@/api/types';

interface NewFeaturePanelProps {
  projects: Project[];
  onCreated: () => Promise<void>;
}

type SubmitState = 'idle' | 'submitting' | 'success' | 'error';

export function NewFeaturePanel({ projects, onCreated }: NewFeaturePanelProps) {
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [projectId, setProjectId] = useState('');
  const [submitState, setSubmitState] = useState<SubmitState>('idle');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!projects.some((project) => project.project_id === projectId)) {
      setProjectId(projects[0]?.project_id ?? '');
    }
  }, [projectId, projects]);

  const canSubmit = Boolean(name.trim() && projectId && submitState !== 'submitting');

  async function createFeature() {
    if (!canSubmit) return;
    setSubmitState('submitting');
    setError('');
    try {
      await api.createFeature(projectId, name.trim());
      setName('');
      setSubmitState('success');
      await onCreated();
    } catch (caught) {
      setError(readableError(caught));
      setSubmitState('error');
    }
  }

  return (
    <aside className="flex h-full w-[264px] shrink-0 flex-col border-r border-white/[0.075] bg-[#090b0f]/92 shadow-[18px_0_60px_rgba(0,0,0,0.16)]">
      <div className="border-b border-white/[0.07] px-5 py-5">
        <div className="flex items-center gap-3">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-blue-300/15 bg-blue-400/[0.07] text-blue-200">
            <PlusCircle className="h-4 w-4" />
          </span>
          <div>
            <p className="font-mono text-[8px] uppercase tracking-[0.18em] text-white/28">New delivery</p>
            <h2 className="mt-0.5 text-sm font-semibold text-white/90">Create feature</h2>
          </div>
        </div>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5">
        {projects.length === 0 ? (
          <div className="space-y-3 rounded-lg border border-dashed border-border p-3">
            <p className="text-xs leading-relaxed text-muted-foreground">
              Register a Git project before creating a feature.
            </p>
            <button className="btn btn-secondary h-8 w-full" onClick={() => navigate('/settings')}>
              <Settings className="h-3.5 w-3.5" />
              Project registry
            </button>
          </div>
        ) : (
          <>
            <label className="block space-y-2">
              <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-white/34">Feature name</span>
              <input
                className="field h-10 rounded-lg border-white/[0.075] bg-white/[0.035] text-xs shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]"
                value={name}
                onChange={(event) => {
                  setName(event.target.value);
                  if (submitState === 'success') setSubmitState('idle');
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void createFeature();
                }}
                placeholder="e.g. Add policy exports"
                disabled={submitState === 'submitting'}
              />
            </label>

            <label className="block space-y-2">
              <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-white/34">Project</span>
              <select
                className="field h-10 rounded-lg border-white/[0.075] bg-white/[0.035] text-xs"
                value={projectId}
                onChange={(event) => setProjectId(event.target.value)}
                disabled={submitState === 'submitting'}
              >
                {projects.map((project) => (
                  <option key={project.project_id} value={project.project_id}>
                    {project.name} · {project.main_branch}
                  </option>
                ))}
              </select>
            </label>

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.018] p-3.5">
              <p className="text-[10px] font-medium uppercase tracking-[0.12em] text-white/28">Isolated by default</p>
              <p className="mt-2 text-[11px] leading-5 text-white/38">
                Creates a dedicated Git worktree. Codex starts only when you begin the feature.
              </p>
            </div>

            {submitState === 'error' && (
              <div className="flex items-start gap-1.5 text-[11px] text-red-400">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>{error}</span>
              </div>
            )}
            {submitState === 'success' && (
              <div className="flex items-center gap-1.5 text-[11px] text-emerald-400">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Feature created from backend state.
              </div>
            )}
          </>
        )}
      </div>

      {projects.length > 0 && (
        <div className="border-t border-white/[0.06] p-5">
          <button className="btn h-10 w-full rounded-lg bg-white text-[#08090c] shadow-[0_10px_30px_rgba(255,255,255,0.08)] hover:bg-white/90" disabled={!canSubmit} onClick={createFeature}>
            {submitState === 'submitting' && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {submitState === 'submitting' ? 'Creating…' : 'Create feature'}
          </button>
        </div>
      )}
    </aside>
  );
}
