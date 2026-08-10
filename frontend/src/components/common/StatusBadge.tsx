import { FEATURE_STATUS_LABELS, type FeatureStatus } from '@/api/types';

const CLASSES: Record<FeatureStatus, string> = {
  TRIAGE: 'status-badge-triage',
  TODO: 'status-badge-todo',
  READY: 'status-badge-ready',
  IN_PROGRESS: 'status-badge-in-progress',
  BLOCKED: 'status-badge-blocked',
  DONE: 'status-badge-done',
};

export function StatusBadge({ status }: { status: FeatureStatus }) {
  return (
    <span className={`inline-flex rounded-md px-2 py-1 text-[9px] font-semibold uppercase tracking-[0.08em] ${CLASSES[status]}`}>
      {FEATURE_STATUS_LABELS[status]}
    </span>
  );
}
