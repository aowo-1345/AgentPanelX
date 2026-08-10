import { Filter, Loader2, RefreshCw, Search } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, readableError } from '@/api/client';
import {
  FEATURE_STATUSES,
  FEATURE_STATUS_LABELS,
  type BoardFeature,
  type FeatureStatus,
  type Project,
} from '@/api/types';
import { KanbanColumn } from '@/components/board/KanbanColumn';
import { NewFeaturePanel } from '@/components/board/NewFeaturePanel';
import { useSilentPolling } from '@/hooks/useSilentPolling';

type LoadState = 'loading' | 'loaded' | 'refreshing' | 'error';

const ACTIVE_BOARD_POLL_MS = 1_000;
const IDLE_BOARD_POLL_MS = 5_000;

function sameFeatures(current: BoardFeature[], next: BoardFeature[]): boolean {
  return JSON.stringify(current) === JSON.stringify(next);
}

export function BoardPage() {
  const navigate = useNavigate();
  const [features, setFeatures] = useState<BoardFeature[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [projectFilter, setProjectFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState<FeatureStatus | 'all'>('all');

  const applyFeatures = useCallback((next: BoardFeature[]) => {
    setFeatures((current) => (sameFeatures(current, next) ? current : next));
    setLoadState((current) => (current === 'error' ? 'loaded' : current));
    setError('');
  }, []);

  const load = useCallback(async (refresh = false) => {
    setLoadState(refresh ? 'refreshing' : 'loading');
    setError('');
    try {
      const [nextProjects, nextFeatures] = await Promise.all([
        api.listProjects(),
        api.listFeatures(),
      ]);
      setProjects(nextProjects);
      applyFeatures(nextFeatures);
      setLoadState('loaded');
    } catch (caught) {
      setError(readableError(caught));
      setLoadState('error');
    }
  }, [applyFeatures]);

  useEffect(() => {
    void load();
  }, [load]);

  const boardBusy = features.some((feature) => feature.status === 'IN_PROGRESS');
  const pollFeatures = useCallback((signal: AbortSignal) => api.listFeatures(signal), []);

  useSilentPolling({
    enabled: loadState === 'loaded' || loadState === 'error',
    intervalMs: boardBusy ? ACTIVE_BOARD_POLL_MS : IDLE_BOARD_POLL_MS,
    query: pollFeatures,
    onData: applyFeatures,
  });

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return features.filter((feature) => {
      const searchable = [
        feature.name,
        feature.project_name,
        feature.branch,
        feature.pending_action,
        feature.current_milestone_key,
        feature.current_stage_key,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return (
        (!query || searchable.includes(query)) &&
        (projectFilter === 'all' || feature.project_id === projectFilter) &&
        (statusFilter === 'all' || feature.status === statusFilter)
      );
    });
  }, [features, projectFilter, search, statusFilter]);

  const isInitialLoading = loadState === 'loading' && features.length === 0;
  const isRefreshing = loadState === 'refreshing';

  return (
    <div className="relative flex h-full flex-col overflow-hidden bg-[#07080b]">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-64 bg-[radial-gradient(ellipse_at_50%_0%,rgba(59,130,246,0.055),transparent_64%)]" />
      <header className="relative z-20 flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-white/[0.07] bg-[#090b0f]/88 px-5 py-3 backdrop-blur-xl">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <label className="relative min-w-[240px] flex-1 sm:max-w-[360px]">
            <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-white/28" />
            <input
              className="field h-9 rounded-lg border-white/[0.075] bg-white/[0.035] pl-9 text-xs shadow-[inset_0_1px_0_rgba(255,255,255,0.025)]"
              placeholder="Search feature, project, branch…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label className="relative">
            <Filter className="pointer-events-none absolute left-3 top-1/2 h-3 w-3 -translate-y-1/2 text-white/30" />
            <select
              className="field h-9 w-44 rounded-lg border-white/[0.075] bg-white/[0.035] pl-8 text-xs"
              value={projectFilter}
              onChange={(event) => setProjectFilter(event.target.value)}
              aria-label="Filter by project"
            >
              <option value="all">All projects</option>
              {projects.map((project) => (
                <option key={project.project_id} value={project.project_id}>
                  {project.name}
                </option>
              ))}
            </select>
          </label>
          <select
            className="field h-9 w-40 rounded-lg border-white/[0.075] bg-white/[0.035] text-xs"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value as FeatureStatus | 'all')}
            aria-label="Filter by status"
          >
            <option value="all">All statuses</option>
            {FEATURE_STATUSES.map((status) => (
              <option key={status} value={status}>
                {FEATURE_STATUS_LABELS[status]}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2">
          <span className="rounded-full border border-white/[0.065] bg-white/[0.025] px-3 py-1.5 text-[10px] tabular-nums text-white/38">
            {features.length} feature{features.length === 1 ? '' : 's'}
          </span>
          <button
            className="btn btn-ghost h-9 w-9 rounded-lg border border-white/[0.055] bg-white/[0.018] p-0 hover:border-white/[0.1]"
            onClick={() => void load(true)}
            disabled={isInitialLoading || isRefreshing}
            aria-label="Refresh board"
          >
            {isRefreshing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
      </header>

      <div className="relative z-10 flex min-h-0 flex-1 overflow-hidden">
        <NewFeaturePanel projects={projects} onCreated={() => load(true)} />

        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {loadState === 'error' && features.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
              <p className="max-w-lg text-sm text-red-300">{error}</p>
              <button className="btn btn-secondary h-9" onClick={() => void load()}>
                <RefreshCw className="h-3.5 w-3.5" />
                Retry
              </button>
            </div>
          ) : (
            <>
              {loadState === 'error' && (
                <div className="border-b border-red-500/20 bg-red-500/10 px-4 py-2 text-xs text-red-300">
                  Refresh failed. Showing the last reliable board: {error}
                </div>
              )}
              {loadState === 'loaded' && filtered.length === 0 && features.length > 0 && (
                <div className="border-b border-border px-4 py-2 text-xs text-muted-foreground">
                  No features match these filters.{' '}
                  <button
                    className="text-primary hover:underline"
                    onClick={() => {
                      setSearch('');
                      setProjectFilter('all');
                      setStatusFilter('all');
                    }}
                  >
                    Clear filters
                  </button>
                </div>
              )}
              <div className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden">
                <div className="flex h-full min-w-max gap-3.5 p-4 2xl:min-w-full">
                  {FEATURE_STATUSES.map((status) => (
                    <KanbanColumn
                      key={status}
                      status={status}
                      label={FEATURE_STATUS_LABELS[status]}
                      features={filtered.filter((feature) => feature.status === status)}
                      loading={isInitialLoading}
                      onOpen={(feature) =>
                        navigate(
                          `/projects/${encodeURIComponent(feature.project_id)}/features/${encodeURIComponent(feature.triage_id)}`,
                        )
                      }
                    />
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
