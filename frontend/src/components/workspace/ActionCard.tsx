import { Loader2 } from 'lucide-react';
import { useState } from 'react';
import type { FeatureAction } from '@/api/types';

const ACTION_LABELS: Record<FeatureAction, string> = {
  begin: 'Begin feature',
  'approve-plan': 'Approve plan',
  'reject-plan': 'Request changes',
  'start-delivery': 'Start delivery',
};

interface ActionCardProps {
  actions: FeatureAction[];
  pendingAction: FeatureAction | null;
  onAction: (action: FeatureAction, feedback?: string) => Promise<void>;
}

export function ActionCard({ actions, pendingAction, onAction }: ActionCardProps) {
  const [feedback, setFeedback] = useState('');
  const needsFeedback = actions.includes('reject-plan');

  if (actions.length === 0) return null;

  return (
    <div className="space-y-3 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
      <div>
        <div className="text-xs font-medium text-foreground">Available decisions</div>
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          These actions come from the current backend workspace state.
        </p>
      </div>

      {needsFeedback && (
        <label className="block space-y-1.5">
          <span className="text-[11px] text-muted-foreground">
            Feedback required when requesting changes
          </span>
          <textarea
            className="field min-h-16 resize-y py-2 text-xs"
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder="Explain what should change in the plan…"
            disabled={pendingAction !== null}
          />
        </label>
      )}

      <div className="flex flex-wrap gap-2">
        {actions.map((action) => {
          const waiting = action === pendingAction;
          const rejectDisabled = action === 'reject-plan' && !feedback.trim();
          const variant = action === 'reject-plan' ? 'btn-danger' : 'btn-secondary';
          return (
            <button
              key={action}
              className={`btn h-8 ${variant}`}
              disabled={pendingAction !== null || rejectDisabled}
              onClick={() => void onAction(action, action === 'reject-plan' ? feedback.trim() : undefined)}
            >
              {waiting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {ACTION_LABELS[action]}
            </button>
          );
        })}
      </div>
    </div>
  );
}
