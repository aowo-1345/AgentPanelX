import { CircleAlert, GitBranch, Milestone } from 'lucide-react';
import type { BoardFeature } from '@/api/types';
import { StatusBadge } from '@/components/common/StatusBadge';

interface FeatureCardProps {
  feature: BoardFeature;
  onOpen: (feature: BoardFeature) => void;
}

export function FeatureCard({ feature, onOpen }: FeatureCardProps) {
  return (
    <button
      className="group w-full space-y-2.5 rounded-lg border border-border bg-card p-3 text-left transition-colors hover:border-primary/40 hover:bg-muted/30 focus:outline-none focus:ring-1 focus:ring-primary/60"
      onClick={() => onOpen(feature)}
      aria-label={`Open ${feature.name} in ${feature.project_name}`}
    >
      <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {feature.project_name}
      </div>
      <div className="text-sm font-medium leading-snug text-foreground transition-colors group-hover:text-primary">
        {feature.name}
      </div>

      {feature.branch && (
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <GitBranch className="h-3 w-3 shrink-0" />
          <span className="truncate font-mono">{feature.branch}</span>
        </div>
      )}

      {(feature.current_milestone_key || feature.current_stage_key) && (
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Milestone className="h-3 w-3 shrink-0" />
          <span className="truncate">
            {[feature.current_milestone_key, feature.current_stage_key].filter(Boolean).join(' · ')}
          </span>
        </div>
      )}

      {feature.pending_action && (
        <div className="flex items-center gap-1.5 text-[11px] text-amber-300">
          <CircleAlert className="h-3 w-3 shrink-0" />
          <span className="truncate">Waiting: {feature.pending_action}</span>
        </div>
      )}

      <StatusBadge status={feature.status} />
    </button>
  );
}
