import { AlertCircle, ArrowLeft, FolderGit2, Loader2, Plus, RefreshCw } from 'lucide-react';
import { type FormEvent, useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, readableError } from '@/api/client';
import type { CreateProjectInput, Project } from '@/api/types';

const EMPTY_FORM: CreateProjectInput = {
  name: '',
  repository_path: '',
  main_branch: 'main',
};

export function SettingsPage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [form, setForm] = useState<CreateProjectInput>(EMPTY_FORM);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const loadProjects = useCallback(async (refresh = false) => {
    setLoading(true);
    setError('');
    try {
      setProjects(await (refresh ? api.refreshProjects() : api.listProjects()));
      if (refresh) setSuccess('Configured project branches refreshed from local repositories.');
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError('');
    setSuccess('');
    try {
      const project = await api.createProject({
        name: form.name.trim(),
        repository_path: form.repository_path.trim(),
        main_branch: form.main_branch.trim(),
      });
      setProjects((current) => [...current, project]);
      setForm(EMPTY_FORM);
      setSuccess(`${project.name} is now available on the board.`);
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit = Boolean(
    form.name.trim() && form.repository_path.trim() && form.main_branch.trim() && !submitting,
  );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border px-4">
        <button className="btn btn-ghost h-8" onClick={() => navigate('/')}>
          <ArrowLeft className="h-3.5 w-3.5" />
          Board
        </button>
        <span className="text-muted-foreground/40">/</span>
        <span className="text-sm font-medium">Project registry</span>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        <div className="mx-auto grid max-w-5xl gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
          <section className="panel-surface min-h-[300px] overflow-hidden">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div>
                <h1 className="text-sm font-semibold">Registered projects</h1>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Local Git repositories managed by this AgentPlaneX workspace.
                </p>
              </div>
              <button
                className="btn btn-ghost h-8 w-8 p-0"
                onClick={() => void loadProjects(true)}
                disabled={loading}
                aria-label="Refresh projects"
              >
                {loading ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="h-3.5 w-3.5" />
                )}
              </button>
            </div>

            {loading && projects.length === 0 ? (
              <div className="flex h-52 items-center justify-center">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : projects.length === 0 ? (
              <div className="flex h-52 flex-col items-center justify-center gap-2 text-center">
                <FolderGit2 className="h-8 w-8 text-muted-foreground/40" />
                <p className="text-sm text-muted-foreground">No projects registered.</p>
                <p className="text-xs text-muted-foreground/60">
                  Add an existing Git repository with the form.
                </p>
              </div>
            ) : (
              <div className="divide-y divide-border">
                {projects.map((project) => (
                  <article key={project.project_id} className="flex items-start gap-3 p-4">
                    <div className="rounded-md bg-muted p-2 text-muted-foreground">
                      <FolderGit2 className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-3">
                        <h2 className="truncate text-sm font-medium">{project.name}</h2>
                        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                          {project.main_branch}
                        </span>
                      </div>
                      <p className="mt-1 truncate font-mono text-[11px] text-muted-foreground">
                        {project.repository_path}
                      </p>
                      <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground/50">
                        {project.project_id}
                      </p>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="panel-surface h-fit p-4">
            <div className="mb-4 flex items-center gap-2">
              <Plus className="h-4 w-4 text-primary" />
              <div>
                <h2 className="text-sm font-semibold">Register project</h2>
                <p className="text-[11px] text-muted-foreground">Use a path visible to the backend process.</p>
              </div>
            </div>

            <form className="space-y-4" onSubmit={submit}>
              <label className="block space-y-1.5">
                <span className="text-xs text-muted-foreground">Display name</span>
                <input
                  className="field h-9"
                  value={form.name}
                  onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                  placeholder="AgentPlaneX"
                  required
                />
              </label>
              <label className="block space-y-1.5">
                <span className="text-xs text-muted-foreground">Repository path</span>
                <input
                  className="field h-9 font-mono text-xs"
                  value={form.repository_path}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, repository_path: event.target.value }))
                  }
                  placeholder="/absolute/path/to/repository"
                  required
                />
              </label>
              <label className="block space-y-1.5">
                <span className="text-xs text-muted-foreground">Main branch</span>
                <input
                  className="field h-9 font-mono text-xs"
                  value={form.main_branch}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, main_branch: event.target.value }))
                  }
                  required
                />
              </label>

              {error && (
                <div className="flex items-start gap-2 rounded-md border border-red-500/20 bg-red-500/10 p-2.5 text-xs text-red-300">
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>{error}</span>
                </div>
              )}
              {success && (
                <div className="rounded-md border border-emerald-500/20 bg-emerald-500/10 p-2.5 text-xs text-emerald-300">
                  {success}
                </div>
              )}

              <button className="btn btn-primary h-9 w-full" disabled={!canSubmit} type="submit">
                {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {submitting ? 'Registering…' : 'Register project'}
              </button>
            </form>
          </section>
        </div>
      </div>
    </div>
  );
}
