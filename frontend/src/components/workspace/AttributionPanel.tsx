import { AlertCircle, FileWarning, Loader2 } from 'lucide-react';
import type { AttributionData, Panel } from '@/api/types';
import { MarkdownDocument } from '@/components/workspace/MarkdownDocument';

interface AttributionPanelProps {
  panel?: Panel<AttributionData>;
}

export function AttributionPanel({ panel }: AttributionPanelProps) {
  if (panel?.error) {
    return (
      <div className="flex items-start gap-2 p-4 text-xs text-red-300">
        <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>Proposals unavailable: {panel.error}</span>
      </div>
    );
  }

  const attribution = panel?.data ?? { state: 'idle' as const, reports: [] };
  if (attribution.reports.length === 0) {
    return (
      <div className="flex items-center gap-2 p-4 text-xs text-muted-foreground">
        {attribution.state === 'running' ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-300" />
        ) : (
          <FileWarning className="h-3.5 w-3.5" />
        )}
        <span>
          {attribution.state === 'running'
            ? '归因中，完成后 Proposal 会出现在这里。'
            : attribution.state === 'failed'
              ? '最近一次归因未产生 Proposal。'
              : '还没有归因 Proposal。'}
        </span>
      </div>
    );
  }

  return (
    <div className="space-y-2 p-3">
      {attribution.state === 'failed' && (
        <div className="flex items-start gap-2 rounded-md border border-red-500/20 bg-red-500/10 p-3 text-xs text-red-300">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>最近一次归因失败；以下仍可查看此前生成的 Proposal。</span>
        </div>
      )}
      {attribution.reports.map((report) => (
        <details
          key={report.run_id}
          className="group rounded-lg border border-border bg-background/40"
        >
          <summary className="cursor-pointer list-none px-3 py-2.5 marker:hidden">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-xs font-medium">Proposal</div>
                <div className="mt-0.5 truncate font-mono text-[9px] text-muted-foreground">
                  {report.run_id}
                </div>
              </div>
              <time className="shrink-0 text-[10px] text-muted-foreground">
                {new Date(report.completed_at ?? report.created_at).toLocaleString()}
              </time>
            </div>
          </summary>
          <div className="border-t border-border px-3 py-4">
            {report.status === 'available' && report.content_markdown ? (
              <MarkdownDocument content={report.content_markdown} />
            ) : (
              <p className="text-xs text-amber-300">这份 Proposal 的内容当前不可用。</p>
            )}
          </div>
        </details>
      ))}
    </div>
  );
}
