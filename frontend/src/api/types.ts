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
  'approve-blocked-run',
  'reject-blocked-run',
] as const;

export type FeatureAction = (typeof FEATURE_ACTIONS)[number];

export interface Project {
  project_id: string;
  name: string;
  repository_path: string;
  main_branch: string;
  git_version: string | null;
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

export interface CreatedIssue {
  number: number;
  url: string;
}

export interface Panel<T> {
  data: T | null;
  error: string | null;
}

export interface RuntimeData {
  status: FeatureStatus;
  pending_action: string | null;
  activation_status: string | null;
  activation_has_reply: boolean;
  current_milestone_key: string | null;
  current_stage_key: string | null;
  blocked_reason: string | null;
  blocked_capability: string | null;
}

export type ToolActivityStatus = 'running' | 'completed' | 'failed';

export interface ToolActivity {
  name: string;
  status: ToolActivityStatus;
  input_preview: string;
  output_preview: string | null;
}

export interface ConversationMessage {
  message_id: string;
  role: 'user' | 'assistant' | 'status' | 'tool';
  content: string;
  tool_activity: ToolActivity | null;
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

export interface AttributionReport {
  run_id: string;
  trigger_event_id?: number;
  created_at: string;
  completed_at: string | null;
  status: 'available' | 'unavailable';
  content_markdown: string | null;
}

export interface AttributionData {
  state: 'idle' | 'running' | 'completed' | 'failed';
  reports: AttributionReport[];
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
  attribution?: Panel<AttributionData>;
}

export interface CreateProjectInput {
  name: string;
  repository_path: string;
  main_branch: string;
}
