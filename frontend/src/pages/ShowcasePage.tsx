import {
  ArrowLeft,
  ArrowRight,
  Check,
  Circle,
  Eye,
  Filter,
  GitPullRequestArrow,
  History,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
  Wrench,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  FEATURE_STATUSES,
  FEATURE_STATUS_LABELS,
  type FeatureAction,
  type FeatureStatus,
} from '@/api/types';
import { KanbanColumn } from '@/components/board/KanbanColumn';
import { StatusBadge } from '@/components/common/StatusBadge';
import { ChatArea } from '@/components/workspace/ChatArea';
import { SidePanels } from '@/components/workspace/SidePanels';
import {
  showcaseBoardFeatures,
  showcaseFrames,
  showcaseProjects,
  type ShowcaseBoardFeature,
  type ShowcaseFrame,
  type ShowcaseSkill,
  type ShowcaseSkillState,
} from '@/showcase/data';

const SKILLS: Array<{
  id: ShowcaseSkill;
  label: string;
  action: string;
  icon: typeof Eye;
}> = [
  { id: 'observe', label: 'Observe', action: '恢复权威事实', icon: Eye },
  { id: 'control', label: 'Control', action: '有界推进 Runtime', icon: Wrench },
  { id: 'attribution', label: 'Attribution', action: '形成归因 Proposal', icon: History },
];

const SKILL_STATE_LABELS: Record<ShowcaseSkillState, string> = {
  waiting: 'Ready',
  active: 'Active',
  complete: 'Complete',
};

function frameIndex(value: string | null): number {
  const index = showcaseFrames.findIndex((frame) => frame.id === value);
  return index >= 0 ? index : 0;
}

function SkillCard({
  label,
  action,
  icon: Icon,
  state,
}: (typeof SKILLS)[number] & { state: ShowcaseSkillState }) {
  const active = state === 'active';
  const complete = state === 'complete';
  return (
    <div
      className={`rounded-lg border p-3 transition-colors ${
        active
          ? 'border-amber-400/35 bg-amber-400/10'
          : complete
            ? 'border-emerald-400/25 bg-emerald-400/5'
            : 'border-border bg-background/40'
      }`}
    >
      <div className="flex items-center gap-2">
        <span
          className={`flex h-7 w-7 items-center justify-center rounded-md ${
            active
              ? 'bg-amber-400/15 text-amber-300'
              : complete
                ? 'bg-emerald-400/15 text-emerald-300'
                : 'bg-muted text-muted-foreground'
          }`}
        >
          <Icon className="h-3.5 w-3.5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-xs font-semibold text-foreground">{label}</div>
          <div className="truncate text-[10px] text-muted-foreground">{action}</div>
        </div>
        <span
          className={`text-[9px] font-semibold uppercase tracking-wider ${
            active ? 'text-amber-300' : complete ? 'text-emerald-300' : 'text-muted-foreground/60'
          }`}
        >
          {SKILL_STATE_LABELS[state]}
        </span>
      </div>
    </div>
  );
}

function EvidenceRail({ frame }: { frame: ShowcaseFrame }) {
  return (
    <aside className="hidden w-[268px] shrink-0 space-y-3 overflow-y-auto border-r border-border bg-card/30 p-4 xl:block">
      <section className="rounded-xl border border-primary/20 bg-primary/5 p-3.5">
        <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-primary">
          <Sparkles className="h-3.5 w-3.5" />
          Self-hosting case
        </div>
        <h2 className="mt-2.5 text-base font-semibold leading-snug">Ultra Mode builds the next loop</h2>
        {!frame.proposal && (
          <p className="mt-2 text-xs leading-5 text-muted-foreground">
            AgentPanelX 使用自己的 Project Owner、Runtime 与 Timeline 管理 Ultra Mode Feature。
          </p>
        )}
        <div className="mt-2.5 flex items-center gap-2 rounded-md bg-background/50 px-2.5 py-2 text-[10px] text-muted-foreground">
          <ShieldCheck className="h-3.5 w-3.5 text-emerald-300" />
          Deterministic showcase · no API calls
        </div>
      </section>

      <section className="panel-surface space-y-2.5 p-3">
        <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          Agent-native operator kit
        </div>
        {SKILLS.map((skill) => (
          <SkillCard key={skill.id} {...skill} state={frame.skillStates[skill.id]} />
        ))}
      </section>

      <section className="panel-surface p-3">
        <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          <GitPullRequestArrow className="h-3.5 w-3.5" />
          Harness Evolution
        </div>
        {frame.proposal ? (
          <div className="mt-3 space-y-3">
            <div>
              <div className="text-sm font-semibold leading-snug">{frame.proposal.title}</div>
              <span className="mt-1.5 inline-flex rounded bg-violet-400/10 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-violet-300">
                {frame.proposal.classification}
              </span>
            </div>
            <div className="space-y-1.5">
              {frame.proposal.evidence.map((item) => (
                <div key={item} className="flex gap-2 text-[10px] leading-4 text-muted-foreground">
                  <Check className="mt-0.5 h-3 w-3 shrink-0 text-emerald-300" />
                  <span>{item}</span>
                </div>
              ))}
            </div>
            <p className="rounded-md border border-violet-400/15 bg-violet-400/5 p-2.5 text-[10px] leading-4 text-violet-100/80">
              {frame.proposal.recommendation}
            </p>
          </div>
        ) : (
          <div className="mt-3 rounded-lg border border-dashed border-border p-3 text-[10px] leading-4 text-muted-foreground">
            BLOCKED 证据完成归因后，这里会出现带证据引用的结构化 Proposal。
          </div>
        )}
      </section>
    </aside>
  );
}

function ShowcaseBoard({
  onOpen,
}: {
  onOpen: (feature: ShowcaseBoardFeature) => void;
}) {
  const [search, setSearch] = useState('');
  const [projectFilter, setProjectFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState<FeatureStatus | 'all'>('all');

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return showcaseBoardFeatures.filter((feature) => {
      const searchable = [
        feature.name,
        feature.project_name,
        feature.branch,
        feature.pending_action,
        feature.current_milestone_key,
        feature.current_stage_key,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return (
        (!query || searchable.includes(query)) &&
        (projectFilter === 'all' || feature.project_id === projectFilter) &&
        (statusFilter === 'all' || feature.status === statusFilter)
      );
    });
  }, [projectFilter, search, statusFilter]);

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      <section className="shrink-0 border-b border-border bg-card/30 px-4 py-4">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-primary">
              <Sparkles className="h-3.5 w-3.5" />
              Static product tour
            </div>
            <h1 className="mt-1.5 text-xl font-semibold tracking-tight">One board, every delivery state</h1>
            <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">
              浏览等待审批、执行中、BROKEN / BLOCKED 与已交付的 Feature。点击任意卡片可进入对应的自举案例章节。
            </p>
          </div>
          <button
            className="btn btn-primary h-9"
            onClick={() => {
              const feature = showcaseBoardFeatures.find(
                (candidate) => candidate.triage_id === 'ultra-mode-self-hosting',
              );
              if (feature) onOpen(feature);
            }}
          >
            Open self-hosting case
            <ArrowRight className="h-3.5 w-3.5" />
          </button>
        </div>
      </section>

      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-2.5">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <label className="relative min-w-[220px] flex-1 sm:max-w-80">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              className="field h-8 pl-8 text-xs"
              placeholder="Search feature, project, branch…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label className="relative">
            <Filter className="pointer-events-none absolute left-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
            <select
              className="field h-8 w-44 pl-7 text-xs"
              value={projectFilter}
              onChange={(event) => setProjectFilter(event.target.value)}
              aria-label="Filter showcase by project"
            >
              <option value="all">All projects</option>
              {showcaseProjects.map((project) => (
                <option key={project.project_id} value={project.project_id}>
                  {project.name}
                </option>
              ))}
            </select>
          </label>
          <select
            className="field h-8 w-36 text-xs"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value as FeatureStatus | 'all')}
            aria-label="Filter showcase by status"
          >
            <option value="all">All statuses</option>
            {FEATURE_STATUSES.map((status) => (
              <option key={status} value={status}>
                {FEATURE_STATUS_LABELS[status]}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span className="hidden items-center gap-1.5 sm:flex">
            <ShieldCheck className="h-3.5 w-3.5 text-emerald-300" />
            Deterministic · no API calls
          </span>
          <span className="tabular-nums">{filtered.length} features</span>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden">
        <div className="flex h-full min-w-max gap-3 p-4">
          {FEATURE_STATUSES.map((status) => (
            <KanbanColumn
              key={status}
              status={status}
              label={FEATURE_STATUS_LABELS[status]}
              features={filtered.filter((feature) => feature.status === status)}
              loading={false}
              compact
              onOpen={(feature) =>
                onOpen(
                  showcaseBoardFeatures.find(
                    (candidate) =>
                      candidate.project_id === feature.project_id &&
                      candidate.triage_id === feature.triage_id,
                  ) ?? showcaseBoardFeatures[0],
                )
              }
            />
          ))}
        </div>
      </div>
    </div>
  );
}

export function ShowcasePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const chapter = searchParams.get('chapter');
  const index = frameIndex(chapter);
  const frame = showcaseFrames[index];
  const chapterRailRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const rail = chapterRailRef.current;
    const selected = rail?.children.item(index) as HTMLElement | null;
    if (!rail || !selected) return;
    rail.scrollTo({
      left: selected.offsetLeft - (rail.clientWidth - selected.clientWidth) / 2,
      behavior: 'smooth',
    });
  }, [index]);

  const setFrame = (next: number) => {
    const bounded = Math.max(0, Math.min(showcaseFrames.length - 1, next));
    setSearchParams({ chapter: showcaseFrames[bounded].id }, { replace: true });
  };

  const conversation = useMemo(() => frame.workspace.conversation, [frame]);

  async function ignoreSend() {
    return false;
  }

  async function advanceFromAction(action: FeatureAction) {
    if (action === 'reject-plan') {
      setFrame(Math.max(0, index - 1));
      return;
    }
    setFrame(index + 1);
  }

  if (!chapter) {
    return (
      <ShowcaseBoard
        onOpen={(feature) =>
          setSearchParams(
            { chapter: feature.entryChapter, feature: feature.triage_id },
            { replace: false },
          )
        }
      />
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      <header className="flex shrink-0 items-center justify-between gap-4 border-b border-border bg-card/30 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-3">
          <button className="btn btn-ghost h-8 shrink-0" onClick={() => setSearchParams({}, { replace: false })}>
            <ArrowLeft className="h-3.5 w-3.5" />
            Board
          </button>
          <span className="hidden text-muted-foreground/30 sm:block">/</span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold">Ultra Mode · self-hosting</span>
              <StatusBadge status={frame.status} />
            </div>
            <div className="hidden truncate text-[10px] text-muted-foreground sm:block">
              showcase/ultra-mode-self-hosting · stable product walkthrough
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn btn-secondary h-8 px-2"
            onClick={() => setFrame(index - 1)}
            disabled={index === 0}
            aria-label="Previous showcase chapter"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Previous</span>
          </button>
          <span className="min-w-12 text-center font-mono text-[10px] text-muted-foreground">
            {String(index + 1).padStart(2, '0')} / {String(showcaseFrames.length).padStart(2, '0')}
          </span>
          <button
            className="btn btn-primary h-8 px-2"
            onClick={() => setFrame(index + 1)}
            disabled={index === showcaseFrames.length - 1}
            aria-label="Next showcase chapter"
          >
            <span className="hidden sm:inline">Next</span>
            <ArrowRight className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      <section className="shrink-0 border-b border-border bg-background/90 px-4 py-3">
        <div ref={chapterRailRef} className="flex items-stretch gap-2 overflow-x-auto pb-1">
          {showcaseFrames.map((candidate, candidateIndex) => {
            const selected = candidateIndex === index;
            const passed = candidateIndex < index;
            return (
              <button
                key={candidate.id}
                className={`group flex min-w-[148px] flex-1 items-center gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors ${
                  selected
                    ? 'border-primary/40 bg-primary/10'
                    : 'border-border bg-card/40 hover:border-primary/20 hover:bg-muted/30'
                }`}
                onClick={() => setFrame(candidateIndex)}
                aria-current={selected ? 'step' : undefined}
              >
                <span
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border ${
                    selected
                      ? 'border-primary/50 bg-primary/15 text-primary'
                      : passed
                        ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300'
                        : 'border-border bg-background text-muted-foreground'
                  }`}
                >
                  {passed ? <Check className="h-3 w-3" /> : selected ? <Circle className="h-2.5 w-2.5 fill-current" /> : <span className="text-[9px]">{candidateIndex + 1}</span>}
                </span>
                <span>
                  <span className={`block text-[10px] font-semibold ${selected ? 'text-primary' : 'text-foreground'}`}>
                    {candidate.label}
                  </span>
                  <span className="block text-[9px] text-muted-foreground">{candidate.eyebrow.split(' · ')[1]}</span>
                </span>
              </button>
            );
          })}
        </div>
        <div className="mt-2 flex items-start justify-between gap-4 px-1">
          <div className="min-w-0">
            <div className="text-[9px] font-semibold uppercase tracking-[0.2em] text-primary">{frame.eyebrow}</div>
            <h1 className="mt-0.5 truncate text-sm font-semibold sm:text-base">{frame.title}</h1>
            <p className="mt-0.5 hidden max-w-4xl text-[11px] text-muted-foreground md:block">{frame.summary}</p>
          </div>
          <button
            className="btn btn-ghost h-7 shrink-0 px-2 text-[10px]"
            onClick={() => setFrame(0)}
            disabled={index === 0}
          >
            <RotateCcw className="h-3 w-3" />
            Restart story
          </button>
        </div>
      </section>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <EvidenceRail frame={frame} />
        <main className="min-w-[360px] flex-1 border-r border-border">
          <ChatArea
            conversation={conversation}
            actions={frame.workspace.available_actions}
            activationStatus={frame.workspace.runtime.data?.activation_status ?? null}
            activationHasReply={frame.workspace.runtime.data?.activation_has_reply ?? false}
            pendingAction={null}
            sending={false}
            notice={null}
            onSend={ignoreSend}
            onAction={advanceFromAction}
            readOnly
            readOnlyLabel="Stable showcase snapshot · choose a chapter above to inspect the full delivery story."
          />
        </main>
        <div className="hidden shrink-0 lg:block">
          <SidePanels
            runtime={frame.workspace.runtime}
            plan={frame.workspace.plan}
            milestones={frame.workspace.milestones}
            git={frame.workspace.git}
            timeline={frame.workspace.timeline}
          />
        </div>
      </div>
    </div>
  );
}
