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
      className="group w-full space-y-3 rounded-xl border border-white/[0.085] bg-[#11151b] p-3.5 text-left shadow-[0_10px_28px_rgba(0,0,0,0.16),inset_0_1px_0_rgba(255,255,255,0.025)] transition duration-200 hover:-translate-y-px hover:border-blue-300/25 hover:bg-[#141921] focus:outline-none focus:ring-1 focus:ring-blue-300/50"
      onClick={() => onOpen(feature)}
      aria-label={`Open ${feature.name} in ${feature.project_name}`}
    >
      <div className="flex items-center gap-2 text-[9px] font-semibold uppercase tracking-[0.12em] text-white/38">
        <span className="h-1 w-1 rounded-full bg-blue-300/65" />
        {feature.project_name}
      </div>
      <div className="text-[14px] font-medium leading-snug text-white/90 transition-colors group-hover:text-blue-100">
        {feature.name}
      </div>

      {feature.branch && (
        <div className="flex items-center gap-1.5 text-[10px] text-white/34">
          <GitBranch className="h-3 w-3 shrink-0" />
          <span className="truncate font-mono">{feature.branch}</span>
        </div>
      )}

      {(feature.current_milestone_key || feature.current_stage_key) && (
        <div className="flex items-center gap-1.5 text-[10px] text-white/38">
          <Milestone className="h-3 w-3 shrink-0" />
          <span className="truncate">
            {[feature.current_milestone_key, feature.current_stage_key].filter(Boolean).join(' · ')}
          </span>
        </div>
      )}

      {feature.pending_action && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-300/10 bg-amber-400/[0.045] px-2.5 py-2 text-[10px] text-amber-200/85">
          <CircleAlert className="h-3 w-3 shrink-0" />
          <span className="truncate">Waiting: {feature.pending_action}</span>
        </div>
      )}

      <StatusBadge status={feature.status} />
    </button>
  );
}
