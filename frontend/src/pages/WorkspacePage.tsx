import { ArrowLeft, Loader2, RefreshCw, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { readableError } from '@/api/client';
import type { ActivationReceipt, FeatureAction, Workspace } from '@/api/types';
import { SkeletonWorkspace } from '@/components/common/Skeletons';
import { StatusBadge } from '@/components/common/StatusBadge';
import { ChatArea, type CommandNotice } from '@/components/workspace/ChatArea';
import { SidePanels } from '@/components/workspace/SidePanels';
import { useWorkspace } from '@/hooks/useWorkspace';

function receiptNotice(receipt: ActivationReceipt): CommandNotice {
  return {
    kind: 'success',
    text: `Message accepted by the backend (${receipt.status}). Waiting for Project Owner. Activation ${receipt.activation_id}.`,
    activationId: receipt.activation_id,
  };
}

interface WorkspacePageProps {
  snapshot?: Workspace;
}

const READ_ONLY_NOTICE: CommandNotice = {
  kind: 'warning',
  text: 'This public Console is a read-only snapshot of the local Project Runtime.',
};

export function WorkspacePage({ snapshot }: WorkspacePageProps = {}) {
  const navigate = useNavigate();
  const { projectId, triageId } = useParams<{ projectId: string; triageId: string }>();
  const {
    workspace,
    loadState,
    loadError,
    sending,
    interrupting,
    pendingAction,
    deleting,
    load,
    sendMessage: sendWorkspaceMessage,
    interruptOwner: interruptWorkspaceOwner,
    performAction: performWorkspaceAction,
    createProposalIssue,
    deleteFeature: deleteWorkspaceFeature,
  } = useWorkspace({ projectId, triageId, snapshot });
  const [notice, setNotice] = useState<CommandNotice | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteError, setDeleteError] = useState('');

  useEffect(() => {
    setNotice(null);
  }, [projectId, triageId, snapshot]);

  const activationStatus = workspace?.runtime.data?.activation_status ?? null;
  useEffect(() => {
    if (sending || activationStatus !== null) return;
    setNotice((current) => (current?.activationId ? null : current));
  }, [activationStatus, sending]);

  async function sendMessage(content: string) {
    if (snapshot) {
      setNotice(READ_ONLY_NOTICE);
      return false;
    }
    setNotice(null);
    try {
      const { receipt, refreshError } = await sendWorkspaceMessage(content);
      if (refreshError) {
        setNotice({
          kind: 'warning',
          text: `Message accepted by the backend (${receipt.status}), but the immediate workspace refresh failed: ${readableError(refreshError)} Automatic refresh will retry. Activation ${receipt.activation_id}.`,
        });
      } else {
        setNotice(receiptNotice(receipt));
      }
      return true;
    } catch (caught) {
      setNotice({ kind: 'error', text: readableError(caught) });
      return false;
    }
  }

  async function performAction(action: FeatureAction, feedback?: string) {
    if (snapshot) {
      setNotice(READ_ONLY_NOTICE);
      return;
    }
    setNotice(null);
    try {
      const accepted = await performWorkspaceAction(action, feedback);
      if (accepted) {
        setNotice({ kind: 'success', text: `Action “${action}” was accepted by the backend.` });
      }
    } catch (caught) {
      setNotice({ kind: 'error', text: readableError(caught) });
    }
  }

  async function interruptOwner() {
    if (snapshot) {
      setNotice(READ_ONLY_NOTICE);
      return;
    }
    setNotice(null);
    try {
      const { receipt, refreshError } = await interruptWorkspaceOwner();
      if (!receipt.accepted) {
        setNotice({
          kind: 'warning',
          text: receipt.status
            ? `Owner is already ${receipt.status.toLowerCase()}. Refreshing workspace.`
            : 'No running Owner activation was found. Refreshing workspace.',
        });
      } else if (refreshError) {
        setNotice({
          kind: 'warning',
          text: `Owner interruption requested, but the immediate workspace refresh failed: ${readableError(refreshError)}`,
        });
      }
    } catch (caught) {
      setNotice({ kind: 'error', text: readableError(caught) });
    }
  }

  async function deleteFeature() {
    if (snapshot) {
      setDeleteError(READ_ONLY_NOTICE.text);
      return;
    }
    setDeleteError('');
    try {
      await deleteWorkspaceFeature();
      navigate('/console', { replace: true });
    } catch (caught) {
      setDeleteError(readableError(caught));
    }
  }

  const isRefreshing = loadState === 'refreshing';
  const initialLoading = loadState === 'loading' && workspace === null;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-border px-4">
        <div className="flex min-w-0 items-center gap-3">
          <button className="btn btn-ghost h-8 shrink-0" onClick={() => navigate('/console')}>
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
                attribution={workspace.attribution}
                actions={workspace.available_actions}
                activationStatus={activationStatus}
                canInterrupt={workspace.runtime.data?.can_interrupt_owner ?? false}
                interrupting={interrupting}
                activationHasReply={workspace.runtime.data?.activation_has_reply ?? false}
                pendingAction={pendingAction}
                sending={sending}
                notice={notice}
                onSend={sendMessage}
                onInterrupt={interruptOwner}
                onAction={performAction}
                onCreateProposalIssue={snapshot ? undefined : createProposalIssue}
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
                  AgentPanelX will remove this Feature from the Board and delete its managed
                  worktree and local Runtime history. Its Git branch and commits will be preserved.
                  Uncommitted changes will be discarded. Active worktrees are refused.
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
