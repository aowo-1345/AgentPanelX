import { ArrowLeft, Loader2, RefreshCw, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, readableError } from '@/api/client';
import type { ActivationReceipt, FeatureAction, Workspace } from '@/api/types';
import { SkeletonWorkspace } from '@/components/common/Skeletons';
import { StatusBadge } from '@/components/common/StatusBadge';
import { ChatArea, type CommandNotice } from '@/components/workspace/ChatArea';
import { SidePanels } from '@/components/workspace/SidePanels';
import { useSilentPolling } from '@/hooks/useSilentPolling';

type LoadState = 'loading' | 'loaded' | 'refreshing' | 'error';

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
    conversation: preserveEqual(current.conversation, next.conversation),
    plan: preserveEqual(current.plan, next.plan),
    milestones: preserveEqual(current.milestones, next.milestones),
    timeline: preserveEqual(current.timeline, next.timeline),
    git: preserveEqual(current.git, next.git),
  };
}

function receiptNotice(receipt: ActivationReceipt): CommandNotice {
  return {
    kind: 'success',
    text: `Message accepted by the backend (${receipt.status}). Waiting for Project Owner. Activation ${receipt.activation_id}.`,
    activationId: receipt.activation_id,
  };
}

export function WorkspacePage() {
  const navigate = useNavigate();
  const { projectId, triageId } = useParams<{ projectId: string; triageId: string }>();
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [loadError, setLoadError] = useState('');
  const [sending, setSending] = useState(false);
  const [pendingAction, setPendingAction] = useState<FeatureAction | null>(null);
  const [notice, setNotice] = useState<CommandNotice | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');

  const applyWorkspace = useCallback((next: Workspace) => {
    setWorkspace((current) => mergeWorkspace(current, next));
    setLoadState((current) => (current === 'error' ? 'loaded' : current));
    setLoadError('');
  }, []);

  const load = useCallback(
    async (refresh = false) => {
      if (!projectId || !triageId) {
        setLoadError('The workspace URL is missing its project or feature identity.');
        setLoadState('error');
        return;
      }
      setLoadState(refresh ? 'refreshing' : 'loading');
      setLoadError('');
      try {
        applyWorkspace(await api.getWorkspace(projectId, triageId));
        setLoadState('loaded');
      } catch (caught) {
        setLoadError(readableError(caught));
        setLoadState('error');
      }
    },
    [applyWorkspace, projectId, triageId],
  );

  useEffect(() => {
    setWorkspace(null);
    setNotice(null);
    void load();
  }, [load]);

  const pollWorkspace = useCallback(
    (signal: AbortSignal) => {
      if (!projectId || !triageId) {
        return Promise.reject(new Error('Workspace identity is missing'));
      }
      return api.getWorkspace(projectId, triageId, signal);
    },
    [projectId, triageId],
  );
  const activationStatus = workspace?.runtime.data?.activation_status ?? null;

  useEffect(() => {
    if (sending || activationStatus !== null) return;
    setNotice((current) => (current?.activationId ? null : current));
  }, [activationStatus, sending]);

  const workspaceBusy =
    activationStatus === 'PENDING' ||
    activationStatus === 'RUNNING' ||
    workspace?.feature.status === 'IN_PROGRESS' ||
    sending ||
    pendingAction !== null;

  useSilentPolling({
    enabled:
      workspace !== null &&
      (loadState === 'loaded' || loadState === 'error'),
    intervalMs: workspaceBusy ? ACTIVE_WORKSPACE_POLL_MS : IDLE_WORKSPACE_POLL_MS,
    query: pollWorkspace,
    onData: applyWorkspace,
  });

  async function sendMessage(content: string) {
    if (!projectId || !triageId || sending) return false;
    setSending(true);
    setNotice(null);
    try {
      const receipt = await api.sendMessage(projectId, triageId, content);
      setNotice(receiptNotice(receipt));
      try {
        applyWorkspace(await api.getWorkspace(projectId, triageId));
      } catch (caught) {
        setNotice({
          kind: 'warning',
          text: `Message accepted by the backend (${receipt.status}), but the immediate workspace refresh failed: ${readableError(caught)} Automatic refresh will retry. Activation ${receipt.activation_id}.`,
        });
      }
      return true;
    } catch (caught) {
      setNotice({ kind: 'error', text: readableError(caught) });
      return false;
    } finally {
      setSending(false);
    }
  }

  async function performAction(action: FeatureAction, feedback?: string) {
    if (!projectId || !triageId || pendingAction) return;
    setPendingAction(action);
    setNotice(null);
    try {
      applyWorkspace(await api.performAction(projectId, triageId, action, feedback));
      setNotice({ kind: 'success', text: `Action “${action}” was accepted by the backend.` });
    } catch (caught) {
      setNotice({ kind: 'error', text: readableError(caught) });
    } finally {
      setPendingAction(null);
    }
  }

  async function deleteFeature() {
    if (!projectId || !triageId || deleting) return;
    setDeleting(true);
    setDeleteError('');
    try {
      await api.deleteFeature(projectId, triageId);
      navigate('/', { replace: true });
    } catch (caught) {
      setDeleteError(readableError(caught));
    } finally {
      setDeleting(false);
    }
  }

  const isRefreshing = loadState === 'refreshing';
  const initialLoading = loadState === 'loading' && workspace === null;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-border px-4">
        <div className="flex min-w-0 items-center gap-3">
          <button className="btn btn-ghost h-8 shrink-0" onClick={() => navigate('/')}>
            <ArrowLeft className="h-3.5 w-3.5" />
            Board
          </button>
          <span className="text-muted-foreground/40">/</span>
          {workspace ? (
            <>
              <span className="shrink-0 text-sm text-muted-foreground">{workspace.project.name}</span>
              <span className="text-muted-foreground/40">/</span>
              <span className="min-w-0 truncate text-sm font-medium">{workspace.feature.name}</span>
              {workspace.feature.branch && (
                <span className="hidden max-w-48 truncate rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground md:block">
                  {workspace.feature.branch}
                </span>
              )}
              <StatusBadge status={workspace.feature.status} />
            </>
          ) : initialLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : null}
        </div>

        <div className="flex items-center gap-1">
          {workspace && (
            <button
              className="btn btn-ghost h-8 w-8 shrink-0 p-0 text-muted-foreground hover:text-red-300"
              onClick={() => {
                setDeleteError('');
                setDeleteOpen(true);
              }}
              aria-label="Delete feature"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
          <button
            className="btn btn-ghost h-8 w-8 shrink-0 p-0"
            onClick={() => void load(true)}
            disabled={initialLoading || isRefreshing}
            aria-label="Refresh workspace"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </header>

      {initialLoading ? (
        <SkeletonWorkspace />
      ) : loadState === 'error' && workspace === null ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
          <p className="max-w-lg text-sm text-red-300">{loadError}</p>
          <button className="btn btn-secondary h-9" onClick={() => void load()}>
            <RefreshCw className="h-3.5 w-3.5" />
            Retry
          </button>
        </div>
      ) : workspace ? (
        <div className="flex min-h-0 flex-1 overflow-x-auto overflow-y-hidden">
          <div className="flex min-w-[420px] flex-1 flex-col border-r border-border">
            {loadState === 'error' && (
              <div className="border-b border-red-500/20 bg-red-500/10 px-4 py-2 text-xs text-red-300">
                Refresh failed. Showing the last reliable workspace: {loadError}
              </div>
            )}
            <div className="min-h-0 flex-1">
              <ChatArea
                conversation={workspace.conversation}
                actions={workspace.available_actions}
                activationStatus={activationStatus}
                activationHasReply={workspace.runtime.data?.activation_has_reply ?? false}
                pendingAction={pendingAction}
                sending={sending}
                notice={notice}
                onSend={sendMessage}
                onAction={performAction}
              />
            </div>
          </div>
          <SidePanels
            runtime={workspace.runtime}
            plan={workspace.plan}
            milestones={workspace.milestones}
            git={workspace.git}
            timeline={workspace.timeline}
          />
        </div>
      ) : null}

      {deleteOpen && workspace && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="delete-feature-title"
        >
          <div className="w-full max-w-lg rounded-xl border border-border bg-card p-5 shadow-2xl">
            <div className="flex items-start gap-3">
              <div className="rounded-lg bg-red-500/10 p-2 text-red-300">
                <Trash2 className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <h2 id="delete-feature-title" className="text-sm font-semibold">
                  Delete “{workspace.feature.name}”?
                </h2>
                <p className="mt-2 text-xs leading-5 text-muted-foreground">
                  AgentPlaneX will remove this Feature from the Board and delete its managed
                  worktree and local Runtime history. Its Git branch and commits will be preserved.
                  Dirty or active worktrees are refused.
                </p>
                <div className="mt-3 space-y-1 rounded-lg border border-border bg-background/60 p-3 font-mono text-[10px] text-muted-foreground">
                  <div className="break-all">{workspace.feature.worktree_path}</div>
                  {workspace.feature.branch && <div className="break-all">{workspace.feature.branch}</div>}
                </div>
                {deleteError && (
                  <p className="mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                    {deleteError}
                  </p>
                )}
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="btn btn-secondary h-9"
                onClick={() => setDeleteOpen(false)}
                disabled={deleting}
              >
                Cancel
              </button>
              <button
                className="btn h-9 border border-red-500/30 bg-red-500/15 text-red-200 hover:bg-red-500/25"
                onClick={() => void deleteFeature()}
                disabled={deleting}
              >
                {deleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                {deleting ? 'Deleting…' : 'Delete feature'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
