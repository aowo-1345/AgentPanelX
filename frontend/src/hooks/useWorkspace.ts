import { useCallback, useEffect, useState } from 'react';
import { api } from '@/api/client';
import type {
  ActivationReceipt,
  CreatedIssue,
  FeatureAction,
  InterruptReceipt,
  Workspace,
} from '@/api/types';
import { useSilentPolling } from '@/hooks/useSilentPolling';

export type WorkspaceLoadState = 'loading' | 'loaded' | 'refreshing' | 'error';

interface UseWorkspaceOptions {
  projectId?: string;
  triageId?: string;
  snapshot?: Workspace;
}

export interface SendMessageResult {
  receipt: ActivationReceipt;
  refreshError: unknown | null;
}

export interface InterruptOwnerResult {
  receipt: InterruptReceipt;
  refreshError: unknown | null;
}

interface UseWorkspaceResult {
  workspace: Workspace | null;
  loadState: WorkspaceLoadState;
  loadError: string;
  sending: boolean;
  interrupting: boolean;
  pendingAction: FeatureAction | null;
  deleting: boolean;
  load: (refresh?: boolean) => Promise<void>;
  sendMessage: (content: string) => Promise<SendMessageResult>;
  interruptOwner: () => Promise<InterruptOwnerResult>;
  performAction: (action: FeatureAction, feedback?: string) => Promise<boolean>;
  createProposalIssue: (runId: string) => Promise<CreatedIssue>;
  deleteFeature: () => Promise<void>;
}

const ACTIVE_WORKSPACE_POLL_MS = 500;
const IDLE_WORKSPACE_POLL_MS = 5_000;

function preserveEqual<T>(current: T, next: T): T {
  return JSON.stringify(current) === JSON.stringify(next) ? current : next;
}

function mergeWorkspace(current: Workspace | null, next: Workspace): Workspace {
  if (current === null || JSON.stringify(current) === JSON.stringify(next)) return current ?? next;
  return {
    ...next,
    project: preserveEqual(current.project, next.project),
    feature: preserveEqual(current.feature, next.feature),
    available_actions: preserveEqual(current.available_actions, next.available_actions),
    runtime: preserveEqual(current.runtime, next.runtime),
    active_execution: preserveEqual(current.active_execution, next.active_execution),
    conversation: preserveEqual(current.conversation, next.conversation),
    plan: preserveEqual(current.plan, next.plan),
    milestones: preserveEqual(current.milestones, next.milestones),
    timeline: preserveEqual(current.timeline, next.timeline),
    git: preserveEqual(current.git, next.git),
    attribution: preserveEqual(current.attribution, next.attribution),
  };
}

function readableCaught(error: unknown): string {
  return error instanceof Error ? error.message : 'Unexpected error';
}

export function useWorkspace({
  projectId,
  triageId,
  snapshot,
}: UseWorkspaceOptions = {}): UseWorkspaceResult {
  const [workspace, setWorkspace] = useState<Workspace | null>(snapshot ?? null);
  const [loadState, setLoadState] = useState<WorkspaceLoadState>(snapshot ? 'loaded' : 'loading');
  const [loadError, setLoadError] = useState('');
  const [sending, setSending] = useState(false);
  const [interrupting, setInterrupting] = useState(false);
  const [pendingAction, setPendingAction] = useState<FeatureAction | null>(null);
  const [deleting, setDeleting] = useState(false);

  const applyWorkspace = useCallback((next: Workspace) => {
    setWorkspace((current) => mergeWorkspace(current, next));
    setLoadState((current) => (current === 'error' ? 'loaded' : current));
    setLoadError('');
  }, []);

  const refreshFromServer = useCallback(async () => {
    if (!projectId || !triageId) {
      throw new Error('The workspace URL is missing its project or feature identity.');
    }
    applyWorkspace(await api.getWorkspace(projectId, triageId));
  }, [applyWorkspace, projectId, triageId]);

  const load = useCallback(
    async (refresh = false) => {
      if (snapshot) {
        setLoadState(refresh ? 'refreshing' : 'loading');
        setLoadError('');
        applyWorkspace(snapshot);
        setLoadState('loaded');
        return;
      }
      if (!projectId || !triageId) {
        setLoadError('The workspace URL is missing its project or feature identity.');
        setLoadState('error');
        return;
      }
      setLoadState(refresh ? 'refreshing' : 'loading');
      setLoadError('');
      try {
        await refreshFromServer();
        setLoadState('loaded');
      } catch (caught) {
        setLoadError(readableCaught(caught));
        setLoadState('error');
      }
    },
    [applyWorkspace, projectId, refreshFromServer, snapshot, triageId],
  );

  useEffect(() => {
    setWorkspace(snapshot ?? null);
    void load();
  }, [load, snapshot]);

  const pollWorkspace = useCallback(
    (signal: AbortSignal) => {
      if (snapshot) return Promise.resolve(snapshot);
      if (!projectId || !triageId) {
        return Promise.reject(new Error('Workspace identity is missing'));
      }
      return api.getWorkspace(projectId, triageId, signal);
    },
    [projectId, snapshot, triageId],
  );

  const activationStatus = workspace?.runtime.data?.activation_status ?? null;
  useEffect(() => {
    if (activationStatus !== 'RUNNING') {
      setInterrupting(false);
    }
  }, [activationStatus]);

  const workspaceBusy =
    activationStatus === 'PENDING' ||
    activationStatus === 'RUNNING' ||
    workspace?.feature.status === 'IN_PROGRESS' ||
    sending ||
    interrupting ||
    pendingAction !== null;

  useSilentPolling({
    enabled:
      !snapshot &&
      workspace !== null &&
      (loadState === 'loaded' || loadState === 'error'),
    intervalMs: workspaceBusy ? ACTIVE_WORKSPACE_POLL_MS : IDLE_WORKSPACE_POLL_MS,
    query: pollWorkspace,
    onData: applyWorkspace,
  });

  const sendMessage = useCallback(
    async (content: string): Promise<SendMessageResult> => {
      if (snapshot) throw new Error('This public Console is read-only.');
      if (!projectId || !triageId) throw new Error('Workspace identity is missing');
      if (sending) throw new Error('A message is already being sent');

      setSending(true);
      try {
        const receipt = await api.sendMessage(projectId, triageId, content);
        let refreshError: unknown | null = null;
        try {
          await refreshFromServer();
        } catch (caught) {
          refreshError = caught;
        }
        return { receipt, refreshError };
      } finally {
        setSending(false);
      }
    },
    [projectId, refreshFromServer, sending, snapshot, triageId],
  );

  const performAction = useCallback(
    async (action: FeatureAction, feedback?: string) => {
      if (snapshot) throw new Error('This public Console is read-only.');
      if (!projectId || !triageId) throw new Error('Workspace identity is missing');
      if (pendingAction) return false;

      setPendingAction(action);
      try {
        applyWorkspace(await api.performAction(projectId, triageId, action, feedback));
        return true;
      } finally {
        setPendingAction(null);
      }
    },
    [applyWorkspace, pendingAction, projectId, snapshot, triageId],
  );

  const interruptOwner = useCallback(async (): Promise<InterruptOwnerResult> => {
    if (snapshot) throw new Error('This public Console is read-only.');
    if (!projectId || !triageId) throw new Error('Workspace identity is missing');
    if (interrupting) throw new Error('Owner interruption is already being requested');

    setInterrupting(true);
    try {
      const receipt = await api.interruptOwner(projectId, triageId);
      let refreshError: unknown | null = null;
      try {
        await refreshFromServer();
      } catch (caught) {
        refreshError = caught;
      }
      if (!receipt.accepted) {
        setInterrupting(false);
      }
      return { receipt, refreshError };
    } catch (caught) {
      setInterrupting(false);
      throw caught;
    }
  }, [interrupting, projectId, refreshFromServer, snapshot, triageId]);

  const createProposalIssue = useCallback(
    async (runId: string): Promise<CreatedIssue> => {
      if (snapshot) throw new Error('This public Console is read-only.');
      if (!projectId || !triageId) throw new Error('Workspace identity is missing');
      const issue = await api.createProposalIssue(projectId, triageId, runId);
      setWorkspace((current) => {
        const panel = current?.attribution;
        const attribution = panel?.data;
        if (!current || !panel || !attribution) return current;
        return {
          ...current,
          attribution: {
            ...panel,
            data: {
              ...attribution,
              reports: attribution.reports.map((report) =>
                report.run_id === runId ? { ...report, created_issue: issue } : report,
              ),
            },
          },
        };
      });
      return issue;
    },
    [projectId, snapshot, triageId],
  );

  const deleteFeature = useCallback(async () => {
    if (snapshot) throw new Error('This public Console is read-only.');
    if (!projectId || !triageId) throw new Error('Workspace identity is missing');
    if (deleting) return;

    setDeleting(true);
    try {
      await api.deleteFeature(projectId, triageId);
    } finally {
      setDeleting(false);
    }
  }, [deleting, projectId, snapshot, triageId]);

  return {
    workspace,
    loadState,
    loadError,
    sending,
    interrupting,
    pendingAction,
    deleting,
    load,
    sendMessage,
    interruptOwner,
    performAction,
    createProposalIssue,
    deleteFeature,
  };
}
