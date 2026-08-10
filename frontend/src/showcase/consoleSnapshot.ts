import type { BoardFeature, Workspace } from '@/api/types';
import type { BoardSnapshot } from '@/pages/BoardPage';
import snapshot from './consoleSnapshot.json';

interface ConsoleRuntimeSnapshot extends BoardSnapshot {
  workspaces: Record<string, Workspace>;
}

export const consoleSnapshot = snapshot as ConsoleRuntimeSnapshot;

export function consoleWorkspace(
  projectId: string,
  triageId: string,
): Workspace | null {
  return consoleSnapshot.workspaces[`${projectId}:${triageId}`] ?? null;
}

export function workspacePath(feature: BoardFeature): string {
  const params = new URLSearchParams({
    project: feature.project_id,
    feature: feature.triage_id,
  });
  return `/console?${params.toString()}`;
}
