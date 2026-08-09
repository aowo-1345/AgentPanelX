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
    <span className={`inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium ${CLASSES[status]}`}>
      {FEATURE_STATUS_LABELS[status]}
    </span>
  );
}
