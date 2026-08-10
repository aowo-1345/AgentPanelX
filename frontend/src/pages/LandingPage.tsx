import '@fontsource-variable/manrope';

import {
  Activity,
  ArrowDown,
  ArrowRight,
  Bot,
  Check,
  ChevronRight,
  CircleHelp,
  Eye,
  GitBranch,
  Github,
  History,
  Layers3,
  Play,
  RefreshCcw,
  Route,
  ShieldCheck,
  TerminalSquare,
  Workflow,
  Zap,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import boardImage from '@/assets/showcase/live-board.png';
import liveWorkspaceImage from '@/assets/showcase/live-workspace.png';
import { BrandMark } from '@/components/common/BrandMark';

const repositoryUrl =
  import.meta.env.VITE_PUBLIC_REPOSITORY_URL?.trim() ||
  'https://github.com/aowo-1345/AgentPanelX';
const staticSite = import.meta.env.VITE_PUBLIC_STATIC_SITE === 'true';
const consolePath = '/console';
const demoPath = staticSite ? '/console' : '/showcase';

const features = [
  {
    icon: Bot,
    title: 'Project Owner 代理用户',
    description:
      '基于 mini-swe-agent 维护长期目标和项目上下文，把一次需求滚动拆成可继续推进的 Plan、Milestone 与 Stage。',
  },
  {
    icon: Route,
    title: '长周期滚动交付',
    description:
      'Planner、Reviewer 与 Executor 围绕同一交付 Contract 协作。一次会话结束，不代表项目上下文归零。',
  },
  {
    icon: GitBranch,
    title: '每个任务独立 Worktree',
    description:
      '每张卡片绑定独立 branch 与 Git worktree。多个 Coding Agent 可以处理同一仓库，而不共享工作目录。',
  },
  {
    icon: Eye,
    title: '过程不再藏在终端里',
    description:
      '对话、thinking、工具调用、输出、审批请求和当前 Stage 投影到同一个 Workspace，运行到哪里一眼可见。',
  },
  {
    icon: RefreshCcw,
    title: '从失败现场继续',
    description:
      'BLOCKED 会固定 Runtime、Plan、Git 与 Timeline 证据。恢复时从现场继续，而不是让用户重新解释整个项目。',
  },
  {
    icon: History,
    title: 'Harness Evolution',
    description:
      '事后回放规划、审查与执行链路，把反复出现的阻塞归因为 Harness 缺口，并沉淀成下一轮改进提案。',
  },
];

const steps = [
  {
    number: '01',
    title: '注册仓库，描述结果',
    description: '选择本地 Git 仓库和主分支，用自然语言告诉 Project Owner 这次想交付什么。',
  },
  {
    number: '02',
    title: 'Project Owner 滚动规划',
    description: 'Owner 保持用户意图，协调 Planner 与 Reviewer，把当前最重要的工作固化为可执行计划。',
  },
  {
    number: '03',
    title: 'Coding Agent 隔离执行',
    description: '每个 Feature 在独立 worktree 中运行。工具调用、Stage 状态和 Git 结果持续回到看板。',
  },
  {
    number: '04',
    title: '恢复、归因、继续交付',
    description: '失败先恢复证据，再决定自动推进或请求人类介入；重复问题进入 Harness Evolution。',
  },
];

const faqs = [
  {
    question: 'AgentPanelX 是另一个 Coding Agent 吗？',
    answer:
      '不是。Codex 等 CLI Coding Agent 负责写代码；AgentPanelX 是运行和协调它们的项目 Harness，负责目标、计划、隔离执行、状态与恢复。',
  },
  {
    question: '它和普通 Kanban 有什么区别？',
    answer:
      '卡片不是手工更新的任务标签，而是连接真实 Runtime、Git worktree、Plan、Stage、工具记录和交付结果的执行入口。',
  },
  {
    question: '为什么需要 Project Owner？',
    answer:
      '长周期项目不能只靠一次 prompt。Project Owner 代理用户维护意图，滚动决定下一步，并把需要人类判断的时刻集中到少数高杠杆节点。',
  },
  {
    question: '代码和运行数据会上传吗？',
    answer:
      'AgentPanelX 默认在本地运行。仓库、worktree、SQLite Runtime 和 Timeline 都留在本机；模型流量取决于你配置的 Coding Agent 或兼容网关。',
  },
  {
    question: 'Showcase 里的流程是真实执行吗？',
    answer:
      '交互 Showcase 使用确定性快照，方便无 API Key 演示完整状态。官网的 Board 与自举 Workspace 来自真实本地 Runtime，并与演示数据明确区分。',
  },
];

function GithubLink({ compact = false }: { compact?: boolean }) {
  return (
    <a
      className={
        compact
          ? 'inline-flex h-9 items-center gap-2 rounded-xl border border-white/10 bg-white/[0.025] px-3.5 text-xs font-bold text-white/85 shadow-[inset_0_1px_0_rgba(255,255,255,0.035)] transition duration-200 hover:-translate-y-px hover:border-white/20 hover:bg-white/[0.06]'
          : 'inline-flex h-12 items-center justify-center gap-2 rounded-xl border border-white/12 bg-white/[0.025] px-6 text-sm font-bold text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.04),0_12px_35px_rgba(0,0,0,0.18)] transition duration-200 hover:-translate-y-0.5 hover:border-white/25 hover:bg-white/[0.06]'
      }
      href={repositoryUrl}
      target="_blank"
      rel="noreferrer"
    >
      <Github className="h-4 w-4" />
      {compact ? 'GitHub' : 'View on GitHub'}
    </a>
  );
}

function SectionHeading({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description?: string;
}) {
  return (
    <div className="mx-auto max-w-3xl text-center">
      <span className="inline-flex rounded-full border border-white/[0.09] bg-white/[0.025] px-3 py-1 font-mono text-[10px] uppercase tracking-[0.2em] text-white/45">
        {eyebrow}
      </span>
      <h2 className="mt-5 text-[32px] font-semibold leading-[1.08] tracking-[-0.045em] text-white sm:text-[42px]">
        {title}
      </h2>
      {description && (
        <p className="mx-auto mt-4 max-w-2xl text-sm leading-7 text-white/48 sm:text-[15px]">
          {description}
        </p>
      )}
    </div>
  );
}

function ProductFrame({
  image,
  alt,
  fade = false,
  imageClassName = 'h-auto w-full',
}: {
  image: string;
  alt: string;
  fade?: boolean;
  imageClassName?: string;
}) {
  return (
    <div className="relative h-full overflow-hidden rounded-[22px] border border-white/[0.1] bg-[#0a0c10] shadow-[0_44px_140px_rgba(0,0,0,0.62)]">
      <div className="relative h-full">
        <img className={`block ${imageClassName}`} src={image} alt={alt} />
        {fade && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-[24%] bg-gradient-to-t from-[#07080a] via-[#07080a]/45 to-transparent" />
        )}
      </div>
    </div>
  );
}

function PromptCard({
  accent,
  prompt,
  skill,
}: {
  accent: string;
  prompt: string;
  skill: string;
}) {
  return (
    <article className="rounded-2xl border border-white/[0.08] bg-white/[0.018] p-5 sm:p-6">
      <p className="text-sm leading-6 text-white/78">
        <span className="mr-2 font-mono text-white/30">›</span>
        {prompt}
      </p>
      <div className="mt-5 flex items-center gap-2 border-t border-white/[0.07] pt-4 font-mono text-[10px] text-white/38">
        <span className={`h-1.5 w-1.5 rounded-full ${accent}`} />
        {skill}
      </div>
    </article>
  );
}

export function LandingPage() {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen overflow-x-hidden bg-[#07080a] font-['Manrope_Variable',ui-sans-serif,system-ui,sans-serif] text-foreground selection:bg-blue-400/25">
      <header className="sticky top-0 z-50 border-b border-white/[0.065] bg-[#07080a]/82 backdrop-blur-2xl">
        <div className="mx-auto flex h-16 max-w-[1200px] items-center justify-between px-5 lg:px-6">
          <button
            className="flex items-center gap-3 text-[15px] font-semibold tracking-[-0.025em] text-white"
            onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
          >
            <BrandMark />
            AgentPanelX
          </button>

          <nav className="hidden items-center gap-8 text-xs font-medium text-white/46 md:flex" aria-label="Primary navigation">
            <a className="transition-colors hover:text-white" href="#features">Features</a>
            <a className="transition-colors hover:text-white" href="#self-hosting-demo">How it works</a>
            <a className="transition-colors hover:text-white" href="#skills">Skills</a>
            <a className="transition-colors hover:text-white" href="#install">Install</a>
            <a className="transition-colors hover:text-white" href="#faq">FAQ</a>
          </nav>

          <div className="flex min-w-8 items-center justify-end">
            <GithubLink compact />
          </div>
        </div>
      </header>

      <main>
        <section className="relative overflow-hidden border-b border-white/[0.065]">
          <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(rgba(148,163,184,0.027)_1px,transparent_1px),linear-gradient(90deg,rgba(148,163,184,0.027)_1px,transparent_1px)] bg-[size:64px_64px] [mask-image:linear-gradient(to_bottom,black,transparent_78%)]" />
          <div className="pointer-events-none absolute left-1/2 top-[-180px] h-[760px] w-[1050px] -translate-x-1/2 rounded-full bg-[radial-gradient(circle,rgba(56,189,248,0.115),rgba(59,130,246,0.075)_34%,transparent_70%)] blur-2xl" />
          <div className="pointer-events-none absolute left-[-180px] top-[260px] h-[620px] w-[720px] rounded-full bg-[radial-gradient(circle,rgba(168,85,247,0.075),rgba(76,29,149,0.025)_42%,transparent_72%)] blur-3xl" />

          <div className="relative mx-auto max-w-[1360px] px-5 pb-20 pt-16 text-center sm:pt-20 lg:px-6 lg:pb-24">
            <h1 className="mx-auto max-w-4xl bg-gradient-to-b from-white via-slate-100 to-slate-400 bg-clip-text text-[44px] font-semibold leading-[1.02] tracking-[-0.06em] text-transparent drop-shadow-[0_12px_36px_rgba(148,163,184,0.08)] sm:text-[58px] lg:text-[68px]">
              Autonomous Harness Orchestrator
            </h1>
            <p className="mx-auto mt-7 max-w-[820px] text-base font-medium leading-7 text-white/74 sm:text-[17px] sm:leading-8">
              Project Owner–driven end-to-end delivery runtime
            </p>
            <p className="mx-auto mt-3 max-w-[820px] text-sm leading-7 text-white/42 sm:text-[15px]">
              A Kanban control plane built for Agent-Native development workflows. The Project Owner Agent acts on the user’s behalf to coordinate multi-agent delivery, focusing human involvement on architecture and plan validation. It turns human decisions and task execution history into traceable evidence, then proposes improvements for continuous project delivery.
            </p>

            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <button
                className="inline-flex h-12 items-center justify-center gap-2 rounded-xl bg-white px-6 text-sm font-bold text-[#07080a] shadow-[0_14px_38px_rgba(255,255,255,0.11)] transition duration-200 hover:-translate-y-0.5 hover:bg-white/90 hover:shadow-[0_18px_45px_rgba(255,255,255,0.14)]"
                onClick={() => navigate(consolePath)}
              >
                Try Console
                <ArrowRight className="h-4 w-4" />
              </button>
              <GithubLink />
              <a
                className="inline-flex h-12 items-center justify-center gap-2 px-3 text-sm font-bold text-white/68 transition hover:text-white"
                href="#self-hosting-demo"
              >
                <ArrowDown className="h-4 w-4" />
                How it works
              </a>
            </div>

            <div className="mx-auto mt-10 grid max-w-[980px] gap-3 text-left md:grid-cols-3">
              {[
                {
                  icon: Eye,
                  name: 'Observe',
                  title: '恢复完整执行现场',
                  detail: '还原 Runtime、Plan、Git 与 Timeline，确认项目运行到了哪里。',
                  iconClass: 'border-blue-300/15 bg-blue-400/[0.07] text-blue-200',
                  glowClass: 'from-blue-400/[0.08]',
                },
                {
                  icon: Zap,
                  name: 'Control',
                  title: '介入关键决策节点',
                  detail: '审批、推进或恢复下一阶段，把人工判断集中在高杠杆时刻。',
                  iconClass: 'border-amber-300/15 bg-amber-400/[0.07] text-amber-200',
                  glowClass: 'from-amber-400/[0.07]',
                },
                {
                  icon: History,
                  name: 'Attribution',
                  title: '驱动 Harness Evolution',
                  detail: '回放阻塞证据链，把一次失败沉淀为下一轮可执行的优化。',
                  iconClass: 'border-violet-300/15 bg-violet-400/[0.07] text-violet-200',
                  glowClass: 'from-violet-400/[0.08]',
                },
              ].map(({ icon: Icon, name, title, detail, iconClass, glowClass }) => (
                <article
                  key={name}
                  className="group relative overflow-hidden rounded-2xl border border-white/[0.085] bg-[#090b0f]/80 p-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.035)] backdrop-blur-sm"
                >
                  <div className={`pointer-events-none absolute inset-x-0 top-0 h-24 bg-gradient-to-b ${glowClass} to-transparent opacity-80`} />
                  <div className="relative flex items-center gap-3">
                    <span className={`flex h-9 w-9 items-center justify-center rounded-xl border ${iconClass}`}>
                      <Icon className="h-4 w-4" />
                    </span>
                    <div>
                      <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-white/35">Agent-native Skill</p>
                      <h3 className="mt-0.5 text-sm font-semibold text-white/90">{name}</h3>
                    </div>
                  </div>
                  <h4 className="relative mt-5 text-[13px] font-semibold text-white/78">{title}</h4>
                  <p className="relative mt-2 text-[11px] leading-5 text-white/38">{detail}</p>
                </article>
              ))}
            </div>

            <div className="relative mx-auto mt-12 max-w-[1280px] text-left sm:mt-14">
              <div className="pointer-events-none absolute -inset-x-28 -inset-y-24 bg-[radial-gradient(ellipse_at_50%_38%,rgba(59,130,246,0.18),rgba(79,70,229,0.075)_38%,transparent_70%)] blur-3xl" />
              <div className="pointer-events-none absolute -inset-x-10 bottom-[-48px] h-52 bg-[radial-gradient(ellipse_at_center,rgba(15,23,42,0.75),transparent_70%)] blur-3xl" />
              <div className="relative">
                <ProductFrame
                  image={boardImage}
                  alt="AgentPanelX Live Console 中的真实项目 Kanban"
                  fade
                />
              </div>
            </div>
          </div>
        </section>

        <section id="features" className="scroll-mt-16 border-b border-white/[0.065] py-24 sm:py-32">
          <div className="mx-auto max-w-[1160px] px-5 lg:px-6">
            <SectionHeading
              eyebrow="Features"
              title="Project Owner–driven 长周期项目滚动交付，最大化人类杠杆"
              description="用户代理基于批准的路线图，动态更新实施规划，建模项目全周期证据链"
            />

            <div className="mt-14 grid gap-3 md:grid-cols-2 lg:grid-cols-3">
              {features.map(({ icon: Icon, title, description }) => (
                <article
                  key={title}
                  className="group relative min-h-[232px] overflow-hidden rounded-2xl border border-white/[0.075] bg-white/[0.016] p-6 transition duration-300 hover:-translate-y-0.5 hover:border-blue-300/20 hover:bg-white/[0.027]"
                >
                  <div className="pointer-events-none absolute -right-16 -top-20 h-40 w-40 rounded-full bg-blue-400/[0.045] blur-3xl transition group-hover:bg-blue-400/[0.08]" />
                  <span className="relative flex h-9 w-9 items-center justify-center rounded-xl border border-white/[0.09] bg-black/25 text-white/62 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
                    <Icon className="h-4 w-4" />
                  </span>
                  <h3 className="relative mt-5 text-[15px] font-semibold tracking-[-0.015em] text-white/90">{title}</h3>
                  <p className="relative mt-3 text-[13px] leading-6 text-white/43">{description}</p>
                </article>
              ))}
            </div>

            <div className="mt-28 border-t border-white/[0.065] pt-24 sm:mt-32 sm:pt-28">
              <SectionHeading
                eyebrow="Delivery loop"
                title="从规划实施路线图，到滚动交付的实施现场"
                description="AgentPanelX 接管项目推进的连续性；人只在真正需要判断的地方介入。"
              />
            </div>

            <div className="relative mt-16 grid gap-8 md:grid-cols-2 lg:grid-cols-4 lg:gap-7">
              <div className="absolute left-[12.5%] right-[12.5%] top-5 hidden h-px bg-gradient-to-r from-transparent via-white/15 to-transparent lg:block" />
              {steps.map((step) => (
                <article key={step.number} className="relative">
                  <span className="relative z-10 flex h-10 w-10 items-center justify-center rounded-full border border-blue-300/20 bg-[#090b0f] font-mono text-[10px] text-blue-200 shadow-[0_0_28px_rgba(59,130,246,0.12)]">
                    {step.number}
                  </span>
                  <h3 className="mt-5 text-[15px] font-semibold text-white/90">{step.title}</h3>
                  <p className="mt-3 text-[13px] leading-6 text-white/42">{step.description}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section id="self-hosting" className="relative scroll-mt-16 overflow-hidden border-b border-white/[0.065] py-24 sm:py-32">
          <div className="pointer-events-none absolute left-[26%] top-1/3 h-[560px] w-[820px] -translate-x-1/2 rounded-full bg-violet-500/[0.065] blur-[150px]" />
          <div className="pointer-events-none absolute right-[-10%] top-[28%] h-[520px] w-[720px] rounded-full bg-blue-500/[0.055] blur-[150px]" />
          <div className="relative mx-auto max-w-[1240px] px-5 lg:px-6">
            <SectionHeading
              eyebrow="How it works"
              title="Self-hosted delivery, agent-native operations"
            />

            <div className="mx-auto mt-16 max-w-[1180px] space-y-6">
              <article id="self-hosting-demo" className="relative scroll-mt-20 overflow-hidden rounded-[24px] border border-white/[0.085] bg-white/[0.015] p-5 shadow-[0_36px_110px_rgba(0,0,0,0.34)] sm:p-6">
                <div className="pointer-events-none absolute inset-x-0 top-0 h-48 bg-gradient-to-b from-violet-400/[0.055] to-transparent" />
                <div className="relative mb-6 px-1 sm:flex sm:items-end sm:justify-between sm:gap-8 sm:px-2">
                  <div>
                    <h3 className="text-lg font-semibold tracking-[-0.035em] text-white/92 sm:text-xl">
                      <span className="text-violet-200/65">Self-hosting</span>
                      <span className="mx-2.5 text-white/20">·</span>
                      Built with AgentPanelX
                    </h3>
                    <p className="mt-3 max-w-3xl text-sm leading-6 text-white/42">
                      Project Owner 正在推进 AgentPanelX 自身的前端改造。下面是同一 Runtime 中的对话、Plan、Git 与 Timeline。
                    </p>
                  </div>
                  <span className="mt-4 hidden shrink-0 rounded-full border border-violet-300/10 bg-violet-400/[0.04] px-3 py-1.5 font-mono text-[9px] uppercase tracking-[0.16em] text-violet-200/45 sm:inline-flex">
                    Live project runtime
                  </span>
                </div>
                <div className="relative [mask-image:linear-gradient(to_bottom,black_93%,transparent)]">
                  <ProductFrame
                    image={liveWorkspaceImage}
                    alt="AgentPanelX 自举开发中的 Project Owner Workspace"
                    fade
                  />
                </div>
                <div className="relative mt-1 flex justify-center">
                  <button
                    className="inline-flex h-10 items-center gap-2 rounded-xl border border-white/[0.09] bg-white/[0.025] px-4 text-xs font-bold text-white/65 transition hover:border-white/20 hover:bg-white/[0.05] hover:text-white"
                    onClick={() => navigate(`${demoPath}?chapter=intent`)}
                  >
                    Explore the self-hosting flow
                    <ChevronRight className="h-3.5 w-3.5" />
                  </button>
                </div>
              </article>

              <article id="skills" className="relative scroll-mt-20 overflow-hidden rounded-[26px] border border-white/[0.085] bg-white/[0.015] p-5 shadow-[0_36px_110px_rgba(0,0,0,0.3)] sm:p-7">
                <div className="pointer-events-none absolute inset-x-0 top-0 h-48 bg-gradient-to-b from-blue-400/[0.055] to-transparent" />
                <div className="relative mb-7 max-w-5xl px-1 sm:px-2">
                  <h3 className="text-lg font-semibold tracking-[-0.035em] text-white/92 sm:text-xl">
                    <span className="text-blue-200/65">Agent-native operations</span>
                    <span className="mx-2.5 text-white/20">·</span>
                    Project Runtime as Skills
                  </h3>
                  <p className="mt-3 text-sm leading-6 text-white/42">
                    Control 让外部 Codex 作为用户分身，审批计划、推进 Stage 并端到端验证交付；Observe 从 Runtime、Plan、Git 与 Timeline 恢复当前或历史执行现场；Attribution 在 BLOCKED 检查点 fork 只读 Historical Project Owner，通过质询与反思追溯根因，并形成 Harness Evolution Proposal。
                  </p>
                </div>

                <div className="relative grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
                  <div className="overflow-hidden rounded-[18px] border border-white/[0.09] bg-[#0b0d11] shadow-[0_24px_70px_rgba(0,0,0,0.35)]">
                    <div className="flex h-11 items-center justify-between border-b border-white/[0.075] px-4">
                      <div className="flex items-center gap-2 font-mono text-[10px] text-white/35">
                        <TerminalSquare className="h-3.5 w-3.5" />
                        ~/my-project
                      </div>
                      <span className="rounded-full border border-white/[0.075] px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider text-white/30">Codex</span>
                    </div>
                    <div className="p-5 font-mono text-xs leading-6 sm:p-6">
                      <div className="flex gap-2 text-white/90">
                        <span className="select-none text-white/25">›</span>
                        <span>Hey Codex, help me find out why this delivery is blocked.</span>
                      </div>
                      <div className="mt-4 rounded-xl border border-blue-300/10 bg-blue-400/[0.035] p-3.5">
                        <div className="flex items-center gap-2 text-blue-200/85">
                          <Activity className="h-3.5 w-3.5" />
                          <span>agentplanex-project-observe</span>
                        </div>
                        <div className="mt-3 space-y-1.5 text-white/44">
                          <div><span className="mr-2 text-emerald-300">✓</span>Runtime context restored</div>
                          <div><span className="mr-2 text-emerald-300">✓</span>Plan, Git and Timeline evidence verified</div>
                          <div><span className="mr-2 text-amber-300">!</span>Delivery is waiting for PLAN_APPROVAL</div>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
                    <PromptCard
                      accent="bg-amber-300"
                      prompt="Hey Codex, approve the current plan and drive the next delivery step."
                      skill="agentplanex-project-control"
                    />
                    <PromptCard
                      accent="bg-violet-300"
                      prompt="Hey Codex, trace this failure and propose a Harness Evolution."
                      skill="agentplanex-project-attribution"
                    />
                  </div>
                </div>
              </article>
            </div>
          </div>
        </section>

        <section id="install" className="scroll-mt-16 border-b border-white/[0.065] bg-white/[0.008] py-24 sm:py-32">
          <div className="mx-auto max-w-[1160px] px-5 lg:px-6">
            <SectionHeading
              eyebrow="Install"
              title="Run AgentPanelX locally"
              description="在本地启动 Web Console 与 Project Runtime，并连接你的 Git 仓库和 CLI Coding Agent。"
            />

            <div className="mt-14 grid gap-4 lg:grid-cols-3">
              <article className="flex min-h-[430px] flex-col rounded-[22px] border border-white/[0.085] bg-white/[0.018] p-6 sm:p-7">
                <div className="flex items-start justify-between gap-4">
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/[0.09] bg-black/20 text-blue-200">
                    <Play className="h-4 w-4 fill-current" />
                  </span>
                  <span className="rounded-full border border-blue-300/15 bg-blue-400/[0.055] px-2.5 py-1 text-[9px] font-semibold uppercase tracking-[0.12em] text-blue-200/70">Recommended</span>
                </div>
                <h3 className="mt-6 text-lg font-semibold tracking-[-0.025em] text-white">Try the Console</h3>
                <p className="mt-3 text-[13px] leading-6 text-white/43">
                  无需模型、API Key 或真实仓库。直接查看 Project Owner、Tool calls、BLOCKED、归因与 Harness Evolution。
                </p>
                <div className="mt-7 space-y-3 text-xs text-white/45">
                  {['Interactive project runtime', 'No API credentials', 'Seven delivery states'].map((item) => (
                    <div key={item} className="flex items-center gap-2.5">
                      <Check className="h-3.5 w-3.5 text-emerald-300/80" />
                      {item}
                    </div>
                  ))}
                </div>
                <button
                  className="mt-auto inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-white text-sm font-semibold text-[#07080a] transition hover:bg-white/90"
                  onClick={() => navigate(demoPath)}
                >
                  Open Console
                  <ArrowRight className="h-4 w-4" />
                </button>
              </article>

              <article className="flex min-h-[430px] flex-col rounded-[22px] border border-white/[0.085] bg-white/[0.018] p-6 sm:p-7">
                <div className="flex items-start justify-between gap-4">
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/[0.09] bg-black/20 text-white/70">
                    <TerminalSquare className="h-4 w-4" />
                  </span>
                  <span className="font-mono text-[9px] uppercase tracking-[0.15em] text-white/26">source</span>
                </div>
                <h3 className="mt-6 text-lg font-semibold tracking-[-0.025em] text-white">Build from source</h3>
                <p className="mt-3 text-[13px] leading-6 text-white/43">
                  Python 3.12、uv、Node.js、Git，以及至少一个可用的 CLI Coding Agent。
                </p>
                <div className="mt-6 overflow-hidden rounded-xl border border-white/[0.075] bg-black/30 font-mono text-[10px] leading-5 text-white/60">
                  <div className="flex items-center justify-between border-b border-white/[0.065] px-3 py-2 text-[9px] text-white/27">
                    <span>~/projects</span>
                    <span>bash</span>
                  </div>
                  <div className="space-y-2 overflow-x-auto p-4">
                    <div><span className="mr-2 text-blue-300">$</span>git clone &lt;repository-url&gt; AgentPanelX</div>
                    <div><span className="mr-2 text-blue-300">$</span>cd AgentPanelX &amp;&amp; uv sync</div>
                    <div><span className="mr-2 text-blue-300">$</span>cd frontend &amp;&amp; npm install</div>
                    <div><span className="mr-2 text-blue-300">$</span>npm run build &amp;&amp; cd ..</div>
                    <div><span className="mr-2 text-blue-300">$</span>uv run agentplanex-web</div>
                  </div>
                </div>
                <p className="mt-auto pt-5 font-mono text-[10px] text-emerald-300/70">→ http://127.0.0.1:13475</p>
              </article>

              <article className="flex min-h-[430px] flex-col rounded-[22px] border border-white/[0.085] bg-white/[0.018] p-6 sm:p-7">
                <div className="flex items-start justify-between gap-4">
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/[0.09] bg-black/20 text-violet-200">
                    <Layers3 className="h-4 w-4" />
                  </span>
                  <span className="font-mono text-[9px] uppercase tracking-[0.15em] text-white/26">runtime</span>
                </div>
                <h3 className="mt-6 text-lg font-semibold tracking-[-0.025em] text-white">Connect the Console</h3>
                <p className="mt-3 text-[13px] leading-6 text-white/43">
                  注册本地 Git 仓库，让 Project Owner、Coding Agent 和三个 Agent-native Skills 共享同一份 Runtime 证据。
                </p>
                <div className="mt-7 space-y-4 text-xs text-white/45">
                  <div className="flex gap-3">
                    <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-300/75" />
                    <span>Repository 与 worktree 保留在本机</span>
                  </div>
                  <div className="flex gap-3">
                    <Workflow className="mt-0.5 h-4 w-4 shrink-0 text-blue-300/75" />
                    <span>一个端口提供 Console 与 Runtime API</span>
                  </div>
                  <div className="flex gap-3">
                    <Zap className="mt-0.5 h-4 w-4 shrink-0 text-amber-300/75" />
                    <span>真实 Activation 才需要模型凭据</span>
                  </div>
                </div>
                <button
                  className="mt-auto inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-white/[0.1] bg-white/[0.025] text-sm font-semibold text-white/78 transition hover:border-white/20 hover:bg-white/[0.055] hover:text-white"
                  onClick={() => navigate(consolePath)}
                >
                  Open Console
                  <ArrowRight className="h-4 w-4" />
                </button>
              </article>
            </div>
          </div>
        </section>

        <section id="faq" className="scroll-mt-16 py-24 sm:py-32">
          <div className="mx-auto max-w-4xl px-5 lg:px-6">
            <SectionHeading eyebrow="FAQ" title="Before you run it" />
            <div className="mt-12 divide-y divide-white/[0.075] border-y border-white/[0.075]">
              {faqs.map((faq, index) => (
                <details key={faq.question} className="group py-1" open={index === 0}>
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-6 py-5 text-left text-sm font-semibold text-white/82 marker:hidden sm:text-[15px]">
                    <span className="flex items-center gap-3">
                      <CircleHelp className="h-4 w-4 shrink-0 text-white/28" />
                      {faq.question}
                    </span>
                    <ChevronRight className="h-4 w-4 shrink-0 text-white/25 transition-transform group-open:rotate-90" />
                  </summary>
                  <p className="pb-6 pl-7 pr-10 text-[13px] leading-7 text-white/43">{faq.answer}</p>
                </details>
              ))}
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-white/[0.065]">
        <div className="mx-auto flex max-w-[1160px] flex-col gap-4 px-5 py-8 text-xs text-white/35 sm:flex-row sm:items-center sm:justify-between lg:px-6">
          <div className="flex items-center gap-2.5">
            <BrandMark />
            <span className="font-semibold text-white/75">AgentPanelX</span>
          </div>
          <div className="flex items-center gap-5">
            <a className="transition-colors hover:text-white" href="#features">Features</a>
            <a className="transition-colors hover:text-white" href="#install">Install</a>
            {repositoryUrl && (
              <a className="transition-colors hover:text-white" href={repositoryUrl} target="_blank" rel="noreferrer">GitHub</a>
            )}
            <span>MIT License</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
