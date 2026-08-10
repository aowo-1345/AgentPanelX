import type { BoardFeature, FeatureStatus } from '@/api/types';
import { SkeletonCard } from '@/components/common/Skeletons';
import { FeatureCard } from './FeatureCard';

const ACCENTS: Record<FeatureStatus, string> = {
  TRIAGE: 'before:bg-amber-400/75',
  TODO: 'before:bg-slate-400/55',
  READY: 'before:bg-blue-400/75',
  IN_PROGRESS: 'before:bg-emerald-400/75',
  BLOCKED: 'before:bg-red-400/80',
  DONE: 'before:bg-teal-300/70',
};

interface KanbanColumnProps {
  status: FeatureStatus;
  label: string;
  features: BoardFeature[];
  loading: boolean;
  onOpen: (feature: BoardFeature) => void;
  compact?: boolean;
}

export function KanbanColumn({
  status,
  label,
  features,
  loading,
  onOpen,
  compact = false,
}: KanbanColumnProps) {
  return (
    <section
      className={`relative flex h-full shrink-0 flex-col overflow-hidden rounded-xl border border-white/[0.075] bg-[#0a0d12]/90 shadow-[0_20px_60px_rgba(0,0,0,0.13)] before:absolute before:inset-x-0 before:top-0 before:h-px ${compact ? 'w-[236px]' : 'w-[280px] 2xl:min-w-[270px] 2xl:flex-1'} ${ACCENTS[status]}`}
    >
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-white/[0.065] px-4">
        <h2 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-white/50">
          {label}
        </h2>
        <span className="min-w-6 rounded-md border border-white/[0.055] bg-white/[0.035] px-1.5 py-0.5 text-center text-[10px] tabular-nums text-white/35">
          {loading ? '…' : features.length}
        </span>
      </header>
      <div className="min-h-[120px] flex-1 space-y-2.5 overflow-y-auto p-2.5">
        {loading ? (
          <>
            <SkeletonCard />
            <SkeletonCard />
          </>
        ) : features.length === 0 ? (
          <div aria-hidden="true" />
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
