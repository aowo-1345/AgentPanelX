import type {
  ActivationReceipt,
  BoardFeature,
  CreateProjectInput,
  CreatedFeature,
  FeatureAction,
  Project,
  Workspace,
} from './types';

interface ErrorPayload {
  detail?: unknown;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function errorMessage(payload: ErrorPayload | null, status: number): string {
  if (typeof payload?.detail === 'string') {
    return payload.detail;
  }
  if (Array.isArray(payload?.detail)) {
    return payload.detail
      .map((item) => {
        if (typeof item === 'object' && item !== null && 'msg' in item) {
          return String(item.msg);
        }
        return String(item);
      })
      .join('; ');
  }
  return `Request failed with HTTP ${status}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('Accept', 'application/json');
  if (init?.body) {
    headers.set('Content-Type', 'application/json');
  }

  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers,
    });
  } catch (error) {
    throw new ApiError(
      error instanceof Error ? `Cannot reach AgentPanelX: ${error.message}` : 'Cannot reach AgentPanelX',
      0,
    );
  }

  const payload = (await response.json().catch(() => null)) as T | ErrorPayload | null;
  if (!response.ok) {
    throw new ApiError(errorMessage(payload as ErrorPayload | null, response.status), response.status);
  }
  return payload as T;
}

function featurePath(projectId: string, triageId?: string): string {
  const project = encodeURIComponent(projectId);
  const base = `/api/projects/${project}/features`;
  return triageId ? `${base}/${encodeURIComponent(triageId)}` : base;
}

export const api = {
  listProjects: () => request<Project[]>('/api/projects'),

  refreshProjects: () => request<Project[]>('/api/projects/refresh', { method: 'POST' }),

  createProject: (input: CreateProjectInput) =>
    request<Project>('/api/projects', {
      method: 'POST',
      body: JSON.stringify(input),
    }),

  listFeatures: (signal?: AbortSignal) => request<BoardFeature[]>('/api/features', { signal }),

  createFeature: (projectId: string, name: string) =>
    request<CreatedFeature>(featurePath(projectId), {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),

  deleteFeature: (projectId: string, triageId: string) =>
    request<void>(featurePath(projectId, triageId), {
      method: 'DELETE',
    }),

  getWorkspace: (projectId: string, triageId: string, signal?: AbortSignal) =>
    request<Workspace>(`${featurePath(projectId, triageId)}/workspace`, { signal }),

  sendMessage: (projectId: string, triageId: string, content: string) =>
    request<ActivationReceipt>(`${featurePath(projectId, triageId)}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),

  performAction: (
    projectId: string,
    triageId: string,
    action: FeatureAction,
    feedback?: string,
  ) =>
    request<Workspace>(`${featurePath(projectId, triageId)}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action, feedback: feedback || null }),
    }),
};

export function readableError(error: unknown): string {
  return error instanceof Error ? error.message : 'Unexpected error';
}
