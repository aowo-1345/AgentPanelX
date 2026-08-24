import { useState } from 'react';
import { AlertCircle, FileText, FileWarning, Loader2 } from 'lucide-react';
import type { AttributionData, AttributionReport, Panel } from '@/api/types';
import { DocumentDialog } from '@/components/workspace/DocumentDialog';
import { MarkdownDocument } from '@/components/workspace/MarkdownDocument';

interface AttributionDialogProps {
  panel?: Panel<AttributionData>;
  onClose: () => void;
}

function reportTime(report: AttributionReport) {
  return new Date(report.completed_at ?? report.created_at).toLocaleString();
}

export function AttributionDialog({ panel, onClose }: AttributionDialogProps) {
  const reports = panel?.data?.reports ?? [];
  const [selectedRunId, setSelectedRunId] = useState<string | null>(
    reports[0]?.run_id ?? null,
  );
  const selected = reports.find((report) => report.run_id === selectedRunId) ?? reports[0];
  const state = panel?.data?.state ?? 'idle';
  const subtitle = selected ? (
    <span className="font-mono">
      {selected.trigger_event_id !== undefined && `Timeline event #${selected.trigger_event_id} · `}
      Run {selected.run_id}
    </span>
  ) : (
    'Current Feature · no generated Proposal'
  );

  return (
    <DocumentDialog
      titleId="attribution-dialog-title"
      title="Attribution Proposals"
      subtitle={subtitle}
      icon={<FileText className="h-4 w-4" />}
      closeLabel="Close Attribution Proposals"
      bodyClassName="p-0"
      onClose={onClose}
    >
      {panel?.error ? (
        <div className="flex items-start gap-2 p-6 text-sm text-red-300">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>Proposals unavailable: {panel.error}</span>
        </div>
      ) : reports.length === 0 ? (
        <div className="flex h-full min-h-64 flex-col items-center justify-center gap-3 p-8 text-center text-muted-foreground">
          {state === 'running' ? (
            <Loader2 className="h-6 w-6 animate-spin text-amber-300" />
          ) : (
            <FileWarning className="h-6 w-6" />
          )}
          <p className="text-sm">
            {state === 'running'
              ? '归因正在运行，完成后 Proposal 会出现在这里。'
              : state === 'failed'
                ? '最近一次归因失败，没有生成可展示的 Proposal。'
                : '当前 Feature 还没有归因 Proposal。'}
          </p>
        </div>
      ) : (
        <div className="grid min-h-full md:grid-cols-[15rem_minmax(0,1fr)]">
          <aside className="border-b border-border bg-background/35 p-3 md:border-b-0 md:border-r">
            <div className="mb-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              History · newest first
            </div>
            <div className="space-y-1.5">
              {reports.map((report, index) => {
                const active = report.run_id === selected?.run_id;
                return (
                  <button
                    key={report.run_id}
                    type="button"
                    className={`w-full rounded-lg border px-3 py-2.5 text-left transition-colors ${
                      active
                        ? 'border-primary/35 bg-primary/10'
                        : 'border-transparent hover:border-border hover:bg-muted/60'
                    }`}
                    onClick={() => setSelectedRunId(report.run_id)}
                    aria-pressed={active}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium">
                        {index === 0 ? 'Latest Proposal' : 'Previous Proposal'}
                      </span>
                      {report.status === 'unavailable' && (
                        <span className="text-[9px] uppercase tracking-wide text-amber-300">
                          Unavailable
                        </span>
                      )}
                    </div>
                    <div className="mt-1 text-[10px] text-muted-foreground">
                      {reportTime(report)}
                    </div>
                    {report.trigger_event_id !== undefined && (
                      <div className="mt-1 font-mono text-[9px] text-muted-foreground/70">
                        Timeline event #{report.trigger_event_id}
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          </aside>

          <section className="min-w-0 px-5 py-6 sm:px-8 lg:px-12">
            {state === 'running' && (
              <div className="mb-6 flex items-center gap-2 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                新一轮归因正在运行；当前展示最近一次已完成的 Proposal。
              </div>
            )}
            {state === 'failed' && (
              <div className="mb-6 flex items-center gap-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                <AlertCircle className="h-3.5 w-3.5" />
                最近一次归因失败；当前展示此前已生成的 Proposal。
              </div>
            )}
            <div className="mx-auto max-w-4xl">
              {selected?.status === 'available' && selected.content_markdown ? (
                <MarkdownDocument content={selected.content_markdown} />
              ) : (
                <div className="flex items-center gap-2 text-sm text-amber-300">
                  <FileWarning className="h-4 w-4" />
                  这份 Proposal 的 Artifact 当前不可用。
                </div>
              )}
            </div>
          </section>
        </div>
      )}
    </DocumentDialog>
  );
}
