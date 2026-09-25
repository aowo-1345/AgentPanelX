import { TerminalSquare } from 'lucide-react';
import '@xterm/xterm/css/xterm.css';
import { useStageTerminal } from '@/hooks/useStageTerminal';

interface StageTerminalProps {
  projectId?: string;
  triageId?: string;
  stageRunId?: string;
}

export function StageTerminal({ projectId, triageId, stageRunId }: StageTerminalProps) {
  const { containerRef, status, error } = useStageTerminal(projectId, triageId, stageRunId);

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-[#07080b] text-foreground">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-white/[0.08] px-4">
        <TerminalSquare className="h-4 w-4 text-emerald-300" />
        <span className="text-xs font-medium">Active Stage terminal</span>
        <span className="ml-auto text-[10px] uppercase tracking-wider text-muted-foreground">
          {status}
        </span>
      </div>
      {error && (
        <div className="border-b border-red-500/20 bg-red-500/10 px-4 py-2 text-xs text-red-300">
          {error}
        </div>
      )}
      <div ref={containerRef} className="min-h-0 flex-1 p-3" />
    </div>
  );
}
