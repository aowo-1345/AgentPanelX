# Repository Guide for Coding Agents

## Project identity

This repository contains **AgentPanelX**, a local-first control plane for long-running
coding projects. The Python package and command names use `agentplanex`. Repository
directory names may vary between clones and forks; use the Git root and repository
documentation to identify the project instead of relying on the parent directory name.

AgentPanelX coordinates a Project Owner, rolling plans, isolated Git worktrees, staged
delivery, and an observable Project Runtime. The Web Console and repository Skills are
different interfaces to the same Runtime evidence and state transitions.

## Sources of truth

Use these sources in order of relevance:

1. Executable code, tests, and persisted Runtime contracts.
2. `docs/architecture.md` for component and state-transition boundaries.
3. `docs/skills.md` for Observe, Control, and Attribution permissions.
4. `CONTRIBUTING.md` for development and submission conventions.
5. `README.md` or `README.zh-CN.md` for product orientation and setup.

Showcase data is a deterministic demonstration fixture. Do not treat it as evidence that
the corresponding Runtime behavior exists or has completed.

## Repository map

- `src/agentplanex/domains/`: domain objects and persisted contracts.
- `src/agentplanex/services/`: Runtime orchestration and business decisions.
- `src/agentplanex/infrastructure/`: Git, SQLite, workspace, and process side effects.
- `src/agentplanex/web/`: FastAPI host, API schemas, and web projections.
- `src/agentplanex/project_owner_agent/`: Project Owner loop, models, and tools.
- `frontend/src/`: React Web Console.
- `tests/`: backend behavior and integration tests.
- `.codex/skills/`: repository-native operation and investigation workflows.
- `config/settings.yaml`: declarative Runtime and model settings; credentials belong in
  environment variables, never in this file.

## Development principles

- Define the user-observable behavior before adding an abstraction or subsystem.
- Prefer the smallest change that preserves existing domain and service boundaries.
- Keep business decisions separate from Git, filesystem, database, and process effects.
- Change Runtime state through its services, executions, CLI, or API. Never bypass the
  Runtime by editing SQLite records or managed Git refs directly.
- Preserve unrelated working-tree changes and do not operate on repositories or projects
  outside the user's stated scope.
- Do not represent fixture data, mocked responses, or manually edited state as evidence
  of a working end-to-end feature.
- Update tests and documentation when a public contract or user workflow changes.

## Verification

Run checks proportional to the change. Prefer targeted tests while iterating, then run
the relevant full group before handing off a completed change.

Backend:

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

Frontend:

```bash
cd frontend
npm run check
npm run lint
npm run build
```

Default verification must not call a model gateway. Credentialed or `live_model` checks
must be explicitly requested and reported separately. For user-interface behavior,
prefer evidence from the real application flow over source inspection alone.

## Runtime operations

When the environment supports repository Skills:

- Use Observe to reconstruct current or historical Runtime facts without mutation.
- Use Control only for explicitly authorized actions through the real Runtime.
- Use Attribution for read-only investigation of BLOCKED history and Harness Evolution
  proposals, not for resolving the block directly.

If Skills are unavailable, follow the same boundaries documented in `docs/skills.md`.

## Safety and repository hygiene

- Never commit credentials, authorization data, private Runtime data, model transcripts,
  request identifiers, temporary worktrees, or unredacted user/tool output.
- Treat `.agentplanex/` as local Runtime state, not source code or a fixture to commit.
- Keep network-dependent tests opt-in and make missing prerequisites visible.
- Inspect Git status before and after changes; commit only the intended files.
