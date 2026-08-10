import type {
  BoardFeature,
  ConversationMessage,
  FeatureStatus,
  Project,
  TimelineEvent,
  Workspace,
} from '@/api/types';

export type ShowcaseSkill = 'observe' | 'control' | 'attribution';
export type ShowcaseSkillState = 'waiting' | 'active' | 'complete';

export interface ShowcaseProposal {
  title: string;
  classification: string;
  evidence: string[];
  recommendation: string;
}

export interface ShowcaseFrame {
  id: string;
  label: string;
  eyebrow: string;
  title: string;
  summary: string;
  status: FeatureStatus;
  workspace: Workspace;
  skillStates: Record<ShowcaseSkill, ShowcaseSkillState>;
  proposal: ShowcaseProposal | null;
}

export interface ShowcaseBoardFeature extends BoardFeature {
  entryChapter: string;
}

export const showcaseProjects: Project[] = [
  {
    project_id: 'showcase-agentplanex',
    name: 'AgentPanelX',
    repository_path: 'local://agentplanex-showcase',
    main_branch: 'main',
    git_version: 'showcase-snapshot',
  },
  {
    project_id: 'showcase-research',
    name: 'Research Workspace',
    repository_path: 'local://research-workspace',
    main_branch: 'main',
    git_version: 'showcase-snapshot',
  },
];

export const showcaseBoardFeatures: ShowcaseBoardFeature[] = [
  {
    project_id: 'showcase-research',
    project_name: 'Research Workspace',
    triage_id: 'rolling-intent-capture',
    name: 'Rolling intent capture',
    status: 'TRIAGE',
    branch: null,
    pending_action: null,
    current_milestone_key: null,
    current_stage_key: null,
    entryChapter: 'intent',
  },
  {
    project_id: 'showcase-agentplanex',
    project_name: 'AgentPanelX',
    triage_id: 'operator-skill-evidence',
    name: 'Agent-native Skill evidence links',
    status: 'TODO',
    branch: 'showcase/operator-skill-evidence',
    pending_action: null,
    current_milestone_key: 'M2',
    current_stage_key: null,
    entryChapter: 'ultra',
  },
  {
    project_id: 'showcase-research',
    project_name: 'Research Workspace',
    triage_id: 'owner-plan-approval',
    name: 'Project Owner plan approval',
    status: 'READY',
    branch: 'showcase/owner-plan-approval',
    pending_action: 'WAITING_APPROVAL',
    current_milestone_key: 'M1',
    current_stage_key: null,
    entryChapter: 'plan',
  },
  {
    project_id: 'showcase-agentplanex',
    project_name: 'AgentPanelX',
    triage_id: 'tool-activity-projection',
    name: 'Realtime Tool activity projection',
    status: 'IN_PROGRESS',
    branch: 'showcase/tool-activity-projection',
    pending_action: null,
    current_milestone_key: 'M1',
    current_stage_key: 'S2-tool-stream',
    entryChapter: 'delivery',
  },
  {
    project_id: 'showcase-agentplanex',
    project_name: 'AgentPanelX',
    triage_id: 'ultra-mode-self-hosting',
    name: 'Ultra Mode · self-hosting',
    status: 'BLOCKED',
    branch: 'showcase/ultra-mode-self-hosting',
    pending_action: 'BROKEN_STAGE',
    current_milestone_key: 'M1',
    current_stage_key: 'S2-assistance-projection',
    entryChapter: 'blocked',
  },
  {
    project_id: 'showcase-research',
    project_name: 'Research Workspace',
    triage_id: 'delivery-evidence-recovery',
    name: 'Delivery evidence recovery',
    status: 'BLOCKED',
    branch: 'showcase/evidence-recovery',
    pending_action: 'ASSISTANCE_RUNNING',
    current_milestone_key: 'M3',
    current_stage_key: 'S1-restore-context',
    entryChapter: 'ultra',
  },
  {
    project_id: 'showcase-agentplanex',
    project_name: 'AgentPanelX',
    triage_id: 'harness-evolution-proposal',
    name: 'Harness Evolution proposal',
    status: 'DONE',
    branch: 'showcase/harness-evolution',
    pending_action: null,
    current_milestone_key: 'M1',
    current_stage_key: 'S3-evolution-proposal',
    entryChapter: 'evolution',
  },
  {
    project_id: 'showcase-agentplanex',
    project_name: 'AgentPanelX',
    triage_id: 'showcase-evidence-index',
    name: 'Public showcase evidence index',
    status: 'DONE',
    branch: 'showcase/public-evidence',
    pending_action: null,
    current_milestone_key: 'M1',
    current_stage_key: 'S4-review',
    entryChapter: 'done',
  },
];

const intentMessage: ConversationMessage = {
  message_id: 'showcase-intent',
  role: 'user',
  content:
    '使用 AgentPanelX 自己开发 Ultra Mode：当交付进入 BLOCKED 时，先让 Codex 恢复证据、尝试安全介入，再决定是否询问用户。',
  tool_activity: null,
};

const ownerPlanMessage: ConversationMessage = {
  message_id: 'showcase-owner-plan',
  role: 'assistant',
  content:
    '我会先完成一个可观察的垂直切片：固化 Block Incident，串联 Observe / Control / Attribution，并让 Web Console 展示 Assistance 的调查过程与最终 Proposal。',
  tool_activity: null,
};

function toolMessage(
  id: string,
  name: string,
  status: 'running' | 'completed' | 'failed',
  input: string,
  output: string | null,
): ConversationMessage {
  return {
    message_id: id,
    role: 'tool',
    content: '',
    tool_activity: {
      name,
      status,
      input_preview: input,
      output_preview: output,
    },
  };
}

const plannerTool = toolMessage(
  'showcase-planner',
  'talk_to_agent',
  'completed',
  '{"agent":"planner","contract":"ultra-mode-vertical-slice"}',
  'Planner returned a 3-stage plan with observable acceptance criteria.',
);

const planApprovalTool = toolMessage(
  'showcase-plan-approval',
  'request_plan_approval',
  'completed',
  '{"plan":"docs/plan.md","subject":"b7d9…4a21"}',
  'Plan approval recorded. Initial Milestone Snapshot may be published.',
);

const deliveryRunningTool = toolMessage(
  'showcase-delivery-running',
  'run_next_milestone',
  'running',
  '{"milestone":"M1","stage":"S2-assistance-projection"}',
  null,
);

const deliveryFailedTool = toolMessage(
  'showcase-delivery-failed',
  'run_next_milestone',
  'failed',
  '{"milestone":"M1","stage":"S2-assistance-projection"}',
  'Stage validation failed: the resumed worker could not resolve the fixed blocker snapshot.',
);

const observeTool = toolMessage(
  'showcase-observe',
  'agentplanex_project_observe',
  'completed',
  '{"triage":"ultra-mode-self-hosting","checkpoint":"blocked-transition"}',
  'Recovered Plan b7d9…4a21, Milestone M1, failed Stage S2 and 9 linked Timeline events.',
);

const attributionRunningTool = toolMessage(
  'showcase-attribution-running',
  'agentplanex_project_attribution',
  'running',
  '{"incident":"block-01","evidence":["plan","timeline","stage-run","git"]}',
  null,
);

const attributionCompletedTool = toolMessage(
  'showcase-attribution-completed',
  'agentplanex_project_attribution',
  'completed',
  '{"incident":"block-01","evidence":["plan","timeline","stage-run","git"]}',
  'Root cause classified as Harness / context handoff. A structured evolution proposal was persisted.',
);

const controlTool = toolMessage(
  'showcase-control',
  'agentplanex_project_control',
  'completed',
  '{"action":"resume-recommended","incident":"block-01"}',
  'Recovery recommendation accepted through the bounded Runtime control interface.',
);

const reviewTool = toolMessage(
  'showcase-review',
  'talk_to_agent',
  'completed',
  '{"agent":"reviewer","contract":"candidate-review"}',
  'Reviewer accepted the showcase candidate and its evidence index.',
);

const blockedOwnerMessage: ConversationMessage = {
  message_id: 'showcase-blocked-owner',
  role: 'assistant',
  content:
    'Stage S2 进入 BLOCKED。我不会让用户重新解释整个项目；Ultra Mode 将从固定的 Runtime、Git、Plan 与 Timeline 证据开始调查。',
  tool_activity: null,
};

const ultraOwnerMessage: ConversationMessage = {
  message_id: 'showcase-ultra-owner',
  role: 'assistant',
  content:
    'Ultra Mode 已恢复失败现场：批准的 Plan 与 Candidate 基线仍然有效，问题集中在 Assistance Worker 的上下文交接，而不是产品需求。',
  tool_activity: null,
};

const proposalMessage: ConversationMessage = {
  message_id: 'showcase-proposal-owner',
  role: 'assistant',
  content:
    'Harness Evolution Proposal 已生成：将 blocker snapshot 作为显式不可变输入传给 Worker，并在启动恢复时校验 incident/thread 绑定。该改进不需要用户重新规划 Feature。',
  tool_activity: null,
};

const doneMessage: ConversationMessage = {
  message_id: 'showcase-done-owner',
  role: 'assistant',
  content:
    'Ultra Mode 自举展示已完成。交付证据、BLOCKED 调查与 Harness Evolution Proposal 已进入同一条可追溯 Timeline。',
  tool_activity: null,
};

const planContent = `# Ultra Mode · 展示级垂直切片

## 目标

让一次进入 BLOCKED 的交付不再依赖用户重新解释上下文：Ultra Mode 从固定证据开始调查，并把结论投影到 Web Console。

## 交付路径

\`\`\`mermaid
flowchart LR
  A[BLOCKED] --> B[Observe evidence]
  B --> C[Attribution]
  C --> D[Recovery recommendation]
  C --> E[Harness Evolution Proposal]
\`\`\`

## 验收

- Project Owner、Plan、Tool、Delivery 和 Timeline 在同一 Workspace 可见。
- Observe、Control、Attribution 的输入、输出和权限边界清楚。
- BLOCKED 能形成引用真实 Artifact 的结构化 Proposal。
`;

const requirementsContent = `# Requirements

1. Project Owner 维护 Ultra Mode Feature 的目标与滚动计划。
2. BLOCKED 后优先恢复 Runtime、Git、Plan、Milestone 与 Stage evidence。
3. Control 只能经过真实 Runtime 接口，不直接修改 SQLite。
4. Attribution 只读分析历史执行链路并输出 Harness Evolution Proposal。
`;

const proposal: ShowcaseProposal = {
  title: 'Make blocker snapshots explicit Worker inputs',
  classification: 'Harness · context handoff',
  evidence: [
    'S2 failed after the Runtime had already persisted BLOCKED.',
    'Approved Plan b7d9…4a21 and Milestone M1 remained valid.',
    'The resumed Worker lacked the incident-bound snapshot reference.',
  ],
  recommendation:
    'Persist one immutable evidence index per Block Incident and validate the incident/thread binding before a Worker resumes.',
};

function event(
  eventId: number,
  eventType: string,
  minute: number,
  payload: Record<string, unknown>,
): TimelineEvent {
  return {
    event_id: eventId,
    event_type: eventType,
    created_at: `2026-08-09T20:${String(minute).padStart(2, '0')}:00+08:00`,
    payload,
  };
}

const timelineEvents = [
  event(1, 'FEATURE_CREATED', 1, { feature: 'Ultra Mode self-hosting' }),
  event(2, 'OWNER_ACTIVATION_COMPLETED', 5, { result: 'intent captured' }),
  event(3, 'PLAN_APPROVAL_RECORDED', 9, { plan: 'b7d9…4a21' }),
  event(4, 'MILESTONE_SNAPSHOT_PUBLISHED', 12, { milestone: 'M1' }),
  event(5, 'STAGE_RUN_STARTED', 18, { stage: 'S2-assistance-projection' }),
  event(6, 'STAGE_RUN_FAILED', 24, { stage: 'S2-assistance-projection' }),
  event(7, 'RUNTIME_CONTEXT_UPDATED', 24, { status: { from: 'IN_PROGRESS', to: 'BLOCKED' } }),
  event(8, 'ULTRA_ASSISTANCE_STARTED', 25, { incident: 'block-01' }),
  event(9, 'ATTRIBUTION_PROPOSAL_CREATED', 31, { classification: 'Harness' }),
  event(10, 'RECOVERY_RECOMMENDED', 34, { disposition: 'RESUME_RECOMMENDED' }),
  event(11, 'SHOWCASE_CANDIDATE_ACCEPTED', 39, { result: 'DONE' }),
];

const milestoneStates = ['PLANNED', 'PLANNED', 'RUNNING', 'BLOCKED', 'BLOCKED', 'BLOCKED', 'DONE'];
const statuses: FeatureStatus[] = [
  'TRIAGE',
  'READY',
  'IN_PROGRESS',
  'BLOCKED',
  'BLOCKED',
  'BLOCKED',
  'DONE',
];

const conversations: ConversationMessage[][] = [
  [intentMessage],
  [intentMessage, ownerPlanMessage, plannerTool, planApprovalTool],
  [ownerPlanMessage, plannerTool, planApprovalTool, deliveryRunningTool],
  [planApprovalTool, deliveryFailedTool, blockedOwnerMessage],
  [
    deliveryFailedTool,
    blockedOwnerMessage,
    observeTool,
    attributionRunningTool,
    ultraOwnerMessage,
  ],
  [
    observeTool,
    attributionCompletedTool,
    ultraOwnerMessage,
    proposalMessage,
  ],
  [
    proposalMessage,
    controlTool,
    reviewTool,
    doneMessage,
  ],
];

const eventCounts = [1, 4, 5, 7, 8, 9, 11];

function workspaceFor(index: number): Workspace {
  const status = statuses[index];
  const blocked = status === 'BLOCKED';
  const complete = status === 'DONE';

  return {
    project: {
      project_id: 'showcase-agentplanex',
      name: 'AgentPanelX',
      repository_path: 'local://agentplanex-showcase',
      main_branch: 'main',
      git_version: 'showcase-snapshot',
    },
    feature: {
      project_id: 'showcase-agentplanex',
      project_name: 'AgentPanelX',
      triage_id: 'ultra-mode-self-hosting',
      name: 'Ultra Mode · self-hosting',
      status,
      branch: 'showcase/ultra-mode-self-hosting',
      worktree_path: 'managed showcase worktree',
      pending_action: index === 1 ? 'PLAN_APPROVAL' : null,
      current_milestone_key: index >= 1 ? 'M1' : null,
      current_stage_key: index >= 2 && !complete ? 'S2-assistance-projection' : null,
    },
    available_actions: [],
    runtime: {
      data: {
        status,
        pending_action: index === 1 ? 'PLAN_APPROVAL' : null,
        activation_status: index === 0 || index === 4 ? 'RUNNING' : null,
        activation_has_reply: index !== 0,
        current_milestone_key: index >= 1 ? 'M1' : null,
        current_stage_key: index >= 2 && !complete ? 'S2-assistance-projection' : null,
        blocked_reason: blocked
          ? 'Stage validation failed. Ultra Mode is investigating the fixed blocker evidence before escalating to the user.'
          : null,
        blocked_capability: blocked ? 'ASSISTANCE_CONTEXT_HANDOFF' : null,
      },
      error: null,
    },
    conversation: { data: conversations[index], error: null },
    plan: {
      data: {
        documents: [
          { name: 'plan.md', content: planContent },
          { name: 'requirements.md', content: requirementsContent },
          { name: 'roadmap.md', content: '# Roadmap\n\n- M1 · Observable assistance\n- M2 · Recovery policy\n' },
        ],
        pending_subject_digest: index === 1 ? 'b7d9…4a21' : null,
        current_commit_sha: index >= 1 ? 'showcase-plan-b7d9' : null,
      },
      error: null,
    },
    milestones: {
      data: {
        snapshot_id: index >= 1 ? 'snapshot-ultra-m1' : null,
        milestones:
          index === 0
            ? []
            : [
                {
                  key: 'M1',
                  objective: 'Expose one complete Ultra Mode assistance journey.',
                  state: milestoneStates[index],
                  stages: [
                    { key: 'S1-runtime-evidence', objective: 'Project Runtime evidence projection' },
                    { key: 'S2-assistance-projection', objective: 'Ultra Mode assistance workspace' },
                    { key: 'S3-evolution-proposal', objective: 'Attribution proposal surface' },
                  ],
                },
              ],
      },
      error: null,
    },
    timeline: { data: timelineEvents.slice(0, eventCounts[index]), error: null },
    git: {
      data: {
        branch: 'showcase/ultra-mode-self-hosting',
        head: complete ? 'showcase-candidate' : `showcase-step-${index + 1}`,
      },
      error: null,
    },
  };
}

const frameMetadata = [
  {
    id: 'intent',
    label: 'Intent',
    eyebrow: '01 · SELF-HOSTING',
    title: 'AgentPanelX starts building Ultra Mode',
    summary: 'Project Owner receives the product goal and keeps the feature context alive.',
  },
  {
    id: 'plan',
    label: 'Plan',
    eyebrow: '02 · ROLLING PLAN',
    title: 'Planner returns an observable delivery contract',
    summary: 'The plan, documents and Milestone Snapshot become durable project evidence.',
  },
  {
    id: 'delivery',
    label: 'Delivery',
    eyebrow: '03 · EXECUTION',
    title: 'Coding agents execute inside the project Runtime',
    summary: 'Tool activity, Stage state, Git and Timeline remain visible while work is running.',
  },
  {
    id: 'blocked',
    label: 'Blocked',
    eyebrow: '04 · BLOCK INCIDENT',
    title: 'A failed Stage becomes a fixed investigation point',
    summary: 'The failure is persisted with its Plan, Stage and Timeline context instead of disappearing into chat.',
  },
  {
    id: 'ultra',
    label: 'Ultra',
    eyebrow: '05 · ULTRA MODE',
    title: 'Ultra Mode restores evidence before asking the user',
    summary: 'Observe reconstructs authoritative facts while Attribution investigates the handoff failure.',
  },
  {
    id: 'evolution',
    label: 'Evolution',
    eyebrow: '06 · HARNESS EVOLUTION',
    title: 'Failure evidence becomes a structured proposal',
    summary: 'The proposal names the root cause, cites evidence and defines the smallest Harness improvement.',
  },
  {
    id: 'done',
    label: 'Done',
    eyebrow: '07 · EVIDENCE COMPLETE',
    title: 'One delivery story, from intent to evolution',
    summary: 'The final view links the self-hosting work, Block Incident, operator Skills and delivery result.',
  },
];

const skillStates: Array<Record<ShowcaseSkill, ShowcaseSkillState>> = [
  { observe: 'waiting', control: 'waiting', attribution: 'waiting' },
  { observe: 'waiting', control: 'waiting', attribution: 'waiting' },
  { observe: 'waiting', control: 'waiting', attribution: 'waiting' },
  { observe: 'active', control: 'waiting', attribution: 'waiting' },
  { observe: 'complete', control: 'waiting', attribution: 'active' },
  { observe: 'complete', control: 'waiting', attribution: 'complete' },
  { observe: 'complete', control: 'complete', attribution: 'complete' },
];

export const showcaseFrames: ShowcaseFrame[] = frameMetadata.map((metadata, index) => ({
  ...metadata,
  status: statuses[index],
  workspace: workspaceFor(index),
  skillStates: skillStates[index],
  proposal: index >= 5 ? proposal : null,
}));
