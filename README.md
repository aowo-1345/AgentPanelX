# AgentPanelX

<p align="center">
  <img src="frontend/public/agentpanelx-mark.svg" alt="AgentPanelX" width="76" />
</p>

<h3 align="center">Autonomous Harness Orchestrator</h3>

<p align="center">
  Coding agents are already effective at individual implementation tasks. Long-running projects still need someone to preserve intent, review plans, isolate concurrent work, recover interrupted execution, and decide what happens next.<br />
  AgentPanelX turns that coordination work into a persistent Project Runtime.
</p>

<p align="center">
  A local-first control plane for long-running coding projects.<br />
  Project Owner agents maintain intent, roll plans forward, coordinate coding agents, and turn delivery history into evidence for Harness Evolution.
</p>

<p align="center">
  <a href="https://aowo-1345.github.io/AgentPanelX/"><strong>Website</strong></a>
  ·
  <a href="https://aowo-1345.github.io/AgentPanelX/console"><strong>Try the Console</strong></a>
  ·
  <a href="docs/architecture.md">Architecture</a>
  ·
  <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="https://github.com/aowo-1345/AgentPanelX/actions/workflows/ci.yml"><img src="https://github.com/aowo-1345/AgentPanelX/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-7c3aed" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/Python-3.12-3776ab" alt="Python 3.12" />
  <img src="https://img.shields.io/badge/local--first-Project_Runtime-111827" alt="Local-first" />
</p>

## Agent-native onboarding

Already using Codex or Claude Code? Let your coding agent install, verify, and explain AgentPanelX for you.

```text
Install AgentPanelX locally and verify that the Web Console starts. Then explain how
Project Owner, Project Runtime, Git worktrees, and the Observe / Control / Attribution
skills work together. Use the repository documentation as the source of truth and
report missing prerequisites instead of guessing.
```

Open this repository in your coding agent, send the request above, and keep the agent in the repository root. The conventional installation steps are available in [Quick start](#quick-start).

![AgentPanelX Web Console](docs/assets/showcase/board.png)

## What AgentPanelX does

That Runtime provides four connected capabilities:

| Capability | What it provides |
| --- | --- |
| **Project Owner rolling delivery** | Maintains user intent and coordinates Planner, Reviewer, and coding agents across Plan, Milestone, and Stage boundaries. |
| **Isolated execution** | Runs each Feature in its own Git branch and worktree so multiple coding agents do not share a working directory. |
| **Observable runtime** | Projects conversations, reasoning, tool input/output, approvals, Git state, Plan, and Timeline into one Web Console. |
| **Recovery and Harness Evolution** | Preserves BLOCKED evidence, reconstructs the failure context, and turns recurring delivery gaps into structured proposals. |

## Project Owner-driven rolling delivery

```mermaid
flowchart TB
    subgraph Cycle["Project Owner-driven rolling delivery"]
        direction LR
        Intent["Long-term user intent<br/>Goals · Constraints · Decisions"]
        Planning["1 · Rolling planning<br/>Project Owner + Rolling Summary<br/>Requirements → Architecture → Roadmap<br/>Planner / Reviewer hard gates"]
        Delivery["2 · Durable isolated delivery<br/>Reviewed Milestone → Claimed Stage<br/>Worktree + CLI Coding Agent<br/>Tested commit + Candidate ref"]
        Decision{"3 · Evidence-based decision<br/>Accept · Next Stage · Replan"}
        Result(["Integrated delivery<br/>or next rolling cycle"])
        Intent --> Planning --> Delivery --> Decision --> Result
    end

    Delivery -->|failure| Blocked["BLOCKED checkpoint<br/>Activation · Context · Git · Timeline"]
    subgraph Recovery["3 · Recovery and Harness Evolution"]
        direction LR
        Blocked --> Observe["Observe<br/>Recover authoritative evidence"]
        Observe --> Control["Control<br/>Resume the bounded Runtime step"]
        Observe --> Attribution["Attribution<br/>Fork Historical Project Owner"]
        Attribution --> Proposal["Harness Evolution Proposal<br/>Prompt · Contract · Runtime · Engineering"]
    end

    Evidence["Authoritative Project Runtime evidence<br/>Messages + Activations · SQLite Context + Stage Runs · Git Commits + Refs · EventBus + Timeline"]
    Planning -.-> Evidence
    Delivery -.-> Evidence
    Blocked -.-> Evidence

    classDef owner fill:#172554,stroke:#60a5fa,color:#eff6ff,stroke-width:2px;
    classDef gate fill:#3f2a0c,stroke:#fbbf24,color:#fffbeb;
    classDef delivery fill:#052e2b,stroke:#34d399,color:#ecfdf5;
    classDef evidence fill:#18181b,stroke:#71717a,color:#f4f4f5;
    classDef recovery fill:#2e1065,stroke:#c084fc,color:#faf5ff;
    style Cycle fill:#0d1117,stroke:#30363d,color:#8b949e;
    style Recovery fill:#0d1117,stroke:#30363d,color:#8b949e;
    class Planning owner;
    class Decision gate;
    class Delivery,Result delivery;
    class Evidence evidence;
    class Blocked,Observe,Control,Attribution,Proposal recovery;
```

This is not a prompt chain. Every cycle is bound to durable identities: an approved Plan commit, a reviewed Milestone snapshot, a claimed Stage Run, an output commit, a candidate ref, and a recoverable Owner Activation. Project Owner keeps those facts and the long-term intent in one Runtime, concentrating human involvement at the decisions where judgment has the highest leverage.

## Agent-native operations

The Web Console is the human interface. Three repository Skills expose the same Project Runtime to Codex and other compatible coding agents:

| Skill | Purpose | Boundary |
| --- | --- | --- |
| [Observe](.codex/skills/agentplanex-project-observe/SKILL.md) | Reconstruct Runtime, Plan, Git, Milestone, Stage, and Timeline facts. | Read-only; does not approve or drive execution. |
| [Control](.codex/skills/agentplanex-project-control/SKILL.md) | Send messages, approve or reject plans, start delivery, and drive authorized runtime actions. | Uses the real Runtime; never edits SQLite or Git refs directly. |
| [Attribution](.codex/skills/agentplanex-project-attribution/SKILL.md) | Restore a BLOCKED checkpoint, fork a read-only Historical Project Owner, question its decisions, and produce a Harness Evolution proposal. | Retrospective and read-only; does not resolve the block itself. |

See [Agent-native operations](docs/skills.md) for the complete workflow and permission model.

## Quick start

### Requirements

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- Node.js and npm
- Git
- Bubblewrap (`bwrap`) on Linux
- At least one supported CLI coding agent for real delivery runs

### Install and run

```bash
git clone https://github.com/aowo-1345/AgentPanelX.git
cd AgentPanelX

uv sync
cd frontend && npm ci && npm run build && cd ..

uv run agentplanex-web
```

Open `http://127.0.0.1:13475`.

The FastAPI process serves the React application and the same-origin `/api` from one port. Model credentials are only required for real Project Owner activations; they are read from environment variables declared in [`config/settings.yaml`](config/settings.yaml).

## Architecture

```text
React Web Console
      │ same-origin /api + silent polling
FastAPI Workspace API
      │
WorkspaceService ── WorkspaceWorker
      │
Feature ProjectRuntime
      ├── Project Owner / Planning / Delivery
      ├── EventBus ── Timeline projection
      ├── Git worktree / Plan commits / Candidate refs
      └── project-local SQLite / Messages / Snapshots / Stage runs
```

Each Feature owns a managed worktree and a project-local SQLite runtime. Git and filesystem effects remain separated from business decisions; Plan and Milestone gates validate exact subjects; the EventBus records execution facts without becoming a second source of truth.

Read [Architecture](docs/architecture.md) for component boundaries, message sequencing, delivery contracts, polling projections, and BLOCKED attribution.

## Development

```bash
uv run pytest
uv run ruff check .
uv run mypy

cd frontend
npm run check
npm run lint
npm run build
```

The default test suite does not call a model gateway. See [Contributing](CONTRIBUTING.md) for repository conventions and Project Owner tool debugging.

## Documentation

- [Architecture](docs/architecture.md)
- [Agent-native operations](docs/skills.md)
- [Console walkthrough](docs/showcase.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [MIT License](LICENSE)
