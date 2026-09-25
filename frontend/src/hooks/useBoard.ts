import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/api/client';
import type { BoardFeature, Project } from '@/api/types';
import { useSilentPolling } from '@/hooks/useSilentPolling';

export type BoardLoadState = 'loading' | 'loaded' | 'refreshing' | 'error';

export interface BoardSnapshot {
  projects: Project[];
  features: BoardFeature[];
}

interface UseBoardOptions {
  snapshot?: BoardSnapshot;
}

interface UseBoardResult {
  projects: Project[];
  features: BoardFeature[];
  loadState: BoardLoadState;
  error: string;
  isInitialLoading: boolean;
  isRefreshing: boolean;
  load: (refresh?: boolean) => Promise<void>;
  createFeature: (projectId: string, name: string) => Promise<void>;
}

const ACTIVE_BOARD_POLL_MS = 1_000;
const IDLE_BOARD_POLL_MS = 5_000;

function sameFeatures(current: BoardFeature[], next: BoardFeature[]): boolean {
  return JSON.stringify(current) === JSON.stringify(next);
}

export function useBoard({ snapshot }: UseBoardOptions = {}): UseBoardResult {
  const [features, setFeatures] = useState<BoardFeature[]>(snapshot?.features ?? []);
  const [projects, setProjects] = useState<Project[]>(snapshot?.projects ?? []);
  const [loadState, setLoadState] = useState<BoardLoadState>(snapshot ? 'loaded' : 'loading');
  const [error, setError] = useState('');

  const applyFeatures = useCallback((next: BoardFeature[]) => {
    setFeatures((current) => (sameFeatures(current, next) ? current : next));
    setLoadState((current) => (current === 'error' ? 'loaded' : current));
    setError('');
  }, []);

  const load = useCallback(
    async (refresh = false) => {
      setLoadState(refresh ? 'refreshing' : 'loading');
      setError('');
      if (snapshot) {
        setProjects(snapshot.projects);
        applyFeatures(snapshot.features);
        setLoadState('loaded');
        return;
      }
      try {
        const [nextProjects, nextFeatures] = await Promise.all([
          api.listProjects(),
          api.listFeatures(),
        ]);
        setProjects(nextProjects);
        applyFeatures(nextFeatures);
        setLoadState('loaded');
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : 'Unexpected error');
        setLoadState('error');
      }
    },
    [applyFeatures, snapshot],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const boardBusy = useMemo(
    () => features.some((feature) => feature.status === 'IN_PROGRESS'),
    [features],
  );
  const pollFeatures = useCallback((signal: AbortSignal) => api.listFeatures(signal), []);

  useSilentPolling({
    enabled: !snapshot && (loadState === 'loaded' || loadState === 'error'),
    intervalMs: boardBusy ? ACTIVE_BOARD_POLL_MS : IDLE_BOARD_POLL_MS,
    query: pollFeatures,
    onData: applyFeatures,
  });

  const createFeature = useCallback(
    async (projectId: string, name: string) => {
      await api.createFeature(projectId, name);
      await load(true);
    },
    [load],
  );

  return {
    projects,
    features,
    loadState,
    error,
    isInitialLoading: loadState === 'loading' && features.length === 0,
    isRefreshing: loadState === 'refreshing',
    load,
    createFeature,
  };
}
