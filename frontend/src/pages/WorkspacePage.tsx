import { ArrowLeft, Loader2, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, readableError } from '@/api/client';
import type { ActivationReceipt, FeatureAction, Workspace } from '@/api/types';
import { SkeletonWorkspace } from '@/components/common/Skeletons';
import { StatusBadge } from '@/components/common/StatusBadge';
import { ChatArea, type CommandNotice } from '@/components/workspace/ChatArea';
import { SidePanels } from '@/components/workspace/SidePanels';

type LoadState = 'loading' | 'loaded' | 'refreshing' | 'error';

function receiptNotice(receipt: ActivationReceipt): CommandNotice {
  return {
    kind: 'success',
    text: `Message accepted by the backend (${receipt.status}). Refresh the workspace to retrieve later Project Owner updates. Activation ${receipt.activation_id}.`,
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
        setWorkspace(await api.getWorkspace(projectId, triageId));
        setLoadState('loaded');
      } catch (caught) {
        setLoadError(readableError(caught));
        setLoadState('error');
      }
    },
    [projectId, triageId],
  );

  useEffect(() => {
    setWorkspace(null);
    setNotice(null);
    void load();
  }, [load]);

  async function sendMessage(content: string) {
    if (!projectId || !triageId || sending) return false;
    setSending(true);
    setNotice(null);
    try {
      const receipt = await api.sendMessage(projectId, triageId, content);
      setNotice(receiptNotice(receipt));
      try {
        setWorkspace(await api.getWorkspace(projectId, triageId));
      } catch (caught) {
        setNotice({
          kind: 'warning',
          text: `${receiptNotice(receipt).text} The immediate workspace refresh failed: ${readableError(caught)}`,
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
      setWorkspace(await api.performAction(projectId, triageId, action, feedback));
      setNotice({ kind: 'success', text: `Action “${action}” was accepted by the backend.` });
    } catch (caught) {
      setNotice({ kind: 'error', text: readableError(caught) });
    } finally {
      setPendingAction(null);
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

        <button
          className="btn btn-ghost h-8 w-8 shrink-0 p-0"
          onClick={() => void load(true)}
          disabled={initialLoading || isRefreshing}
          aria-label="Refresh workspace"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
        </button>
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
                activationStatus={workspace.runtime.data?.activation_status ?? null}
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
    </div>
  );
}
