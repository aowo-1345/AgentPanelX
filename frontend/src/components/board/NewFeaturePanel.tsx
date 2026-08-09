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
    <aside className="flex h-full w-[240px] shrink-0 flex-col border-r border-border bg-card/25">
      <div className="border-b border-border px-4 py-4">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <PlusCircle className="h-4 w-4 text-primary" />
          New feature
        </div>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
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
            <label className="block space-y-1.5">
              <span className="text-xs text-muted-foreground">Feature name</span>
              <input
                className="field h-9 text-xs"
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

            <label className="block space-y-1.5">
              <span className="text-xs text-muted-foreground">Project</span>
              <select
                className="field h-9 text-xs"
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

            <p className="text-[11px] leading-relaxed text-muted-foreground">
              Creates an isolated worktree. Codex will not run until you begin the feature.
            </p>

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
        <div className="p-4">
          <button className="btn btn-primary h-9 w-full" disabled={!canSubmit} onClick={createFeature}>
            {submitState === 'submitting' && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {submitState === 'submitting' ? 'Creating…' : 'Create feature'}
          </button>
        </div>
      )}
    </aside>
  );
}
