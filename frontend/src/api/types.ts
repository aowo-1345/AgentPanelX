export const FEATURE_STATUSES = [
  'TRIAGE',
  'TODO',
  'READY',
  'IN_PROGRESS',
  'BLOCKED',
  'DONE',
] as const;

export type FeatureStatus = (typeof FEATURE_STATUSES)[number];

export const FEATURE_STATUS_LABELS: Record<FeatureStatus, string> = {
  TRIAGE: 'Triage',
  TODO: 'Todo',
  READY: 'Ready',
  IN_PROGRESS: 'In progress',
  BLOCKED: 'Blocked',
  DONE: 'Done',
};

export const FEATURE_ACTIONS = [
  'begin',
  'approve-plan',
  'reject-plan',
  'start-delivery',
] as const;

export type FeatureAction = (typeof FEATURE_ACTIONS)[number];

export interface Project {
  project_id: string;
  name: string;
  repository_path: string;
  main_branch: string;
}

export interface CreatedFeature {
  triage_id: string;
  project_id: string;
  name: string;
  branch: string;
  worktree_path: string;
}

export interface BoardFeature {
  project_id: string;
  project_name: string;
  triage_id: string;
  name: string;
  status: FeatureStatus;
  branch: string | null;
  pending_action: string | null;
  current_milestone_key: string | null;
  current_stage_key: string | null;
}

export interface WorkspaceFeature extends BoardFeature {
  worktree_path: string;
}

export interface ActivationReceipt {
  activation_id: string;
  status: string;
  created_at: string;
}

export interface Panel<T> {
  data: T | null;
  error: string | null;
}

export interface RuntimeData {
  status: FeatureStatus;
  pending_action: string | null;
  activation_status: string | null;
  current_milestone_key: string | null;
  current_stage_key: string | null;
  blocked_reason: string | null;
  blocked_capability: string | null;
}

export interface ConversationMessage {
  message_id: string;
  role: 'user' | 'assistant' | 'status';
  content: string;
}

export interface PlanDocument {
  name: string;
  content: string | null;
}

export interface PlanData {
  documents: PlanDocument[];
  pending_subject_digest: string | null;
  current_commit_sha: string | null;
}

export interface StageData {
  key: string;
  objective: string;
}

export interface MilestoneData {
  key: string;
  objective: string;
  state: string;
  stages: StageData[];
}

export interface MilestonesData {
  snapshot_id: string | null;
  milestones: MilestoneData[];
}

export interface TimelineEvent {
  event_id: number | null;
  event_type: string;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface GitData {
  branch: string;
  head: string;
}

export interface Workspace {
  project: Project;
  feature: WorkspaceFeature;
  available_actions: FeatureAction[];
  runtime: Panel<RuntimeData>;
  conversation: Panel<ConversationMessage[]>;
  plan: Panel<PlanData>;
  milestones: Panel<MilestonesData>;
  timeline: Panel<TimelineEvent[]>;
  git: Panel<GitData>;
}

export interface CreateProjectInput {
  name: string;
  repository_path: string;
  main_branch: string;
}
