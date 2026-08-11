import { Check, Copy, ExternalLink, FileText, GitBranch, GitCommit } from 'lucide-react';
import { type ReactNode, useState } from 'react';
import type {
  GitData,
  MilestonesData,
  Panel,
  PlanData,
  RuntimeData,
  TimelineEvent,
} from '@/api/types';
import { ErrorBoundary } from '@/components/common/ErrorBoundary';
import { StatusBadge } from '@/components/common/StatusBadge';
import { PlanDocumentDialog } from '@/components/workspace/PlanDocumentDialog';

function PanelShell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="panel-surface space-y-2.5 p-3">
      <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h2>
      {children}
    </section>
  );
}

function PanelState<T>({ panel, children }: { panel: Panel<T>; children: (data: T) => ReactNode }) {
  if (panel.error) {
    return <p className="text-xs leading-relaxed text-red-300">{panel.error}</p>;
  }
  if (panel.data === null) {
    return <p className="text-xs italic text-muted-foreground/60">Information unavailable</p>;
  }
  return children(panel.data);
}

function KeyValue({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex items-start justify-between gap-3 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="min-w-0 break-all text-right font-mono text-[11px] text-foreground">
        {value || '—'}
      </span>
    </div>
  );
}

function RuntimePanel({ panel }: { panel: Panel<RuntimeData> }) {
  return (
    <PanelState panel={panel}>
      {(runtime) => {
        const assistanceInProgress = runtime.blocked_capability?.startsWith('ASSISTANCE_');
        return (
          <div className="space-y-2">
            <StatusBadge status={runtime.status} />
            <KeyValue label="Activation" value={runtime.activation_status} />
            <KeyValue label="Pending" value={runtime.pending_action} />
            <KeyValue label="Milestone" value={runtime.current_milestone_key} />
            <KeyValue label="Stage" value={runtime.current_stage_key} />
            {runtime.blocked_reason && (
              <div
                className={`rounded-md border p-2.5 text-xs ${
                  assistanceInProgress
                    ? 'border-violet-400/30 bg-violet-400/10'
                    : 'border-red-500/30 bg-red-500/10'
                }`}
              >
                <div
                  className={
                    assistanceInProgress
                      ? 'font-medium text-violet-300'
                      : 'font-medium text-red-300'
                  }
                >
                  {assistanceInProgress ? 'Ultra Mode investigating' : 'User action required'}
                </div>
                {runtime.blocked_capability && (
                  <div
                    className={`mt-1 font-mono text-[10px] ${
                      assistanceInProgress ? 'text-violet-200/70' : 'text-red-200/70'
                    }`}
                  >
                    {runtime.blocked_capability}
                  </div>
                )}
                <p
                  className={`mt-1.5 leading-relaxed ${
                    assistanceInProgress ? 'text-violet-100/80' : 'text-red-100/80'
                  }`}
                >
                  {runtime.blocked_reason}
                </p>
              </div>
            )}
          </div>
        );
      }}
    </PanelState>
  );
}

function PlanPanel({ panel }: { panel: Panel<PlanData> }) {
  const [selectedDocumentName, setSelectedDocumentName] = useState<string | null>(null);

  return (
    <PanelState panel={panel}>
      {(plan) => {
        const selectedDocument = plan.documents.find(
          (document) => document.name === selectedDocumentName,
        );

        return (
          <div className="space-y-2.5">
            {plan.documents.length === 0 ? (
              <p className="text-xs italic text-muted-foreground/60">No plan documents yet</p>
            ) : (
              <div className="space-y-1.5">
                {plan.documents.map((document) => {
                  const available = Boolean(document.content);
                  return (
                    <button
                      key={document.name}
                      type="button"
                      className="group flex w-full items-center gap-2 rounded-md border border-border bg-background/40 p-2 text-left text-xs transition-colors enabled:hover:border-primary/30 enabled:hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                      onClick={() => setSelectedDocumentName(document.name)}
                      disabled={!available}
                      aria-label={available ? `Open ${document.name}` : `${document.name} unavailable`}
                    >
                      <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground group-enabled:group-hover:text-primary" />
                      <span className="min-w-0 flex-1 truncate">{document.name}</span>
                      {available ? (
                        <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground/60 group-hover:text-primary" />
                      ) : (
                        <span className="text-[9px] uppercase tracking-wide text-muted-foreground">
                          Missing
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
            <KeyValue label="Plan commit" value={plan.current_commit_sha} />
            <KeyValue label="Pending digest" value={plan.pending_subject_digest} />

            {selectedDocument && (
              <PlanDocumentDialog
                document={selectedDocument}
                commitSha={plan.current_commit_sha}
                onClose={() => setSelectedDocumentName(null)}
              />
            )}
          </div>
        );
      }}
    </PanelState>
  );
}

function milestoneDisplayState(milestoneKey: string, state: string, runtime: RuntimeData | null) {
  if (state !== 'pending' || runtime?.current_milestone_key !== milestoneKey) return state;
  if (runtime.status === 'IN_PROGRESS') return 'in progress';
  if (runtime.status === 'BLOCKED') return 'blocked';
  return state;
}

function milestoneStateTone(state: string) {
  if (state === 'completed') return 'bg-emerald-500/15 text-emerald-300';
  if (state === 'in progress') return 'bg-blue-500/15 text-blue-300';
  if (state === 'blocked') return 'bg-red-500/15 text-red-300';
  return 'bg-muted text-muted-foreground';
}

function activeStageState(
  milestoneKey: string,
  stageKey: string,
  runtime: RuntimeData | null,
) {
  if (
    runtime?.current_milestone_key !== milestoneKey ||
    runtime.current_stage_key !== stageKey
  ) {
    return null;
  }
  if (runtime.status === 'IN_PROGRESS') return 'running';
  if (runtime.status === 'BLOCKED') return 'blocked';
  return null;
}

function MilestonesPanel({
  panel,
  runtime,
}: {
  panel: Panel<MilestonesData>;
  runtime: RuntimeData | null;
}) {
  return (
    <PanelState panel={panel}>
      {(data) => (
        <div className="space-y-3">
          {data.milestones.length === 0 ? (
            <p className="text-xs italic text-muted-foreground/60">No milestones published</p>
          ) : (
            data.milestones.map((milestone) => {
              const displayState = milestoneDisplayState(
                milestone.key,
                milestone.state,
                runtime,
              );
              return (
                <div key={milestone.key} className="space-y-1.5 border-l border-border pl-2.5">
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span className="font-mono text-primary">{milestone.key}</span>
                    <span
                      className={`rounded px-1.5 py-0.5 text-[9px] uppercase ${milestoneStateTone(displayState)}`}
                    >
                      {displayState}
                    </span>
                  </div>
                  <p className="text-xs leading-relaxed">{milestone.objective}</p>
                  {milestone.stages.map((stage) => {
                    const stageState = activeStageState(
                      milestone.key,
                      stage.key,
                      runtime,
                    );
                    return (
                      <div
                        key={stage.key}
                        className={`flex items-start gap-1.5 rounded px-1.5 py-1 text-[11px] ${
                          stageState === 'blocked'
                            ? 'bg-red-500/10 text-red-200'
                            : stageState === 'running'
                              ? 'bg-blue-500/10 text-blue-200'
                              : 'text-muted-foreground'
                        }`}
                      >
                        <span className="font-mono">{stage.key}</span>
                        <span className="min-w-0 flex-1">· {stage.objective}</span>
                        {stageState && (
                          <span
                            className={`shrink-0 text-[9px] uppercase ${
                              stageState === 'blocked' ? 'text-red-300' : 'text-blue-300'
                            }`}
                          >
                            {stageState}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              );
            })
          )}
          <KeyValue label="Snapshot" value={data.snapshot_id} />
        </div>
      )}
    </PanelState>
  );
}

function GitPanel({ panel }: { panel: Panel<GitData> }) {
  const [copied, setCopied] = useState(false);

  async function copy(head: string) {
    await navigator.clipboard.writeText(head);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <PanelState panel={panel}>
      {(git) => (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-xs">
            <GitBranch className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="min-w-0 truncate font-mono">{git.branch}</span>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <GitCommit className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate font-mono">{git.head}</span>
            <button
              className="text-muted-foreground hover:text-foreground"
              onClick={() => void copy(git.head)}
              aria-label="Copy commit hash"
            >
              {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
            </button>
          </div>
        </div>
      )}
    </PanelState>
  );
}

function TimelinePanel({ panel }: { panel: Panel<TimelineEvent[]> }) {
  return (
    <PanelState panel={panel}>
      {(events) =>
        events.length === 0 ? (
          <p className="text-xs italic text-muted-foreground/60">No events yet</p>
        ) : (
          <div className="space-y-3">
            {events.map((event, index) => (
              <div key={`${event.event_id ?? 'event'}:${index}`} className="flex gap-2.5">
                <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full border border-border bg-muted" />
                <div className="min-w-0">
                  <div className="text-[10px] text-muted-foreground">
                    {new Date(event.created_at).toLocaleString()}
                  </div>
                  <div className="break-words text-xs">{event.event_type}</div>
                  {Object.keys(event.payload).length > 0 && (
                    <details>
                      <summary className="cursor-pointer text-[10px] text-muted-foreground">Payload</summary>
                      <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-background/50 p-2 text-[9px] text-muted-foreground">
                        {JSON.stringify(event.payload, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
              </div>
            ))}
          </div>
        )
      }
    </PanelState>
  );
}

interface SidePanelsProps {
  runtime: Panel<RuntimeData>;
  plan: Panel<PlanData>;
  milestones: Panel<MilestonesData>;
  git: Panel<GitData>;
  timeline: Panel<TimelineEvent[]>;
}

export function SidePanels({ runtime, plan, milestones, git, timeline }: SidePanelsProps) {
  const panels: Array<[string, ReactNode]> = [
    ['Runtime', <RuntimePanel key="runtime" panel={runtime} />],
    ['Current plan', <PlanPanel key="plan" panel={plan} />],
    [
      'Milestones',
      <MilestonesPanel key="milestones" panel={milestones} runtime={runtime.data} />,
    ],
    ['Git', <GitPanel key="git" panel={git} />],
    ['Timeline', <TimelinePanel key="timeline" panel={timeline} />],
  ];

  return (
    <aside className="w-[340px] shrink-0 space-y-3 overflow-y-auto p-4">
      {panels.map(([title, content]) => (
        <ErrorBoundary key={title} compact>
          <PanelShell title={title}>{content}</PanelShell>
        </ErrorBoundary>
      ))}
    </aside>
  );
}
