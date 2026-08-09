import type { BoardFeature, FeatureStatus } from '@/api/types';
import { SkeletonCard } from '@/components/common/Skeletons';
import { FeatureCard } from './FeatureCard';

const ACCENTS: Record<FeatureStatus, string> = {
  TRIAGE: 'border-t-amber-500/60',
  TODO: 'border-t-slate-500/60',
  READY: 'border-t-blue-500/60',
  IN_PROGRESS: 'border-t-emerald-500/60',
  BLOCKED: 'border-t-red-500/60',
  DONE: 'border-t-emerald-400/60',
};

interface KanbanColumnProps {
  status: FeatureStatus;
  label: string;
  features: BoardFeature[];
  loading: boolean;
  onOpen: (feature: BoardFeature) => void;
}

export function KanbanColumn({
  status,
  label,
  features,
  loading,
  onOpen,
}: KanbanColumnProps) {
  return (
    <section
      className={`flex h-full w-[252px] shrink-0 flex-col rounded-lg border border-border border-t-2 bg-card/40 ${ACCENTS[status]}`}
    >
      <header className="flex items-center justify-between border-b border-border px-3 py-2.5">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </h2>
        <span className="rounded bg-muted px-1.5 py-0.5 text-xs tabular-nums text-muted-foreground">
          {loading ? '…' : features.length}
        </span>
      </header>
      <div className="min-h-[120px] flex-1 space-y-2 overflow-y-auto p-2">
        {loading ? (
          <>
            <SkeletonCard />
            <SkeletonCard />
          </>
        ) : features.length === 0 ? (
          <div className="flex h-24 items-center justify-center text-xs text-muted-foreground/50">
            No features
          </div>
        ) : (
          features.map((feature) => (
            <FeatureCard
              key={`${feature.project_id}:${feature.triage_id}`}
              feature={feature}
              onOpen={onOpen}
            />
          ))
        )}
      </div>
    </section>
  );
}
