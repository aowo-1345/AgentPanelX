---
name: resume-handoff
description: Manages persistent HANDOFF.md creation and resume between AI coding sessions. Activates when HANDOFF.md exists, when user mentions resume-handoff, persistent handoff, resume, or continuing saved work.
---

# Handoff Detection

> Adapted from the Claude Code `handoff` plugin for codex, but named `resume-handoff` to emphasize persistent repo-level resume behavior. Same `HANDOFF.md` format and workflow, so handoffs created by either agent are interoperable. See sibling files `create.md`, `quick.md`, `resume.md` for the full behavior instructions.

## On Session Start

Check if `HANDOFF.md` exists in the working directory. If found:

1. Read it silently
2. Tell the user: "Found a handoff from a previous session: [title]. [1-sentence goal]. Resume from here?"
3. If they agree, follow the flow in `resume.md`

## Trigger Words

Activate when user says: "resume-handoff", "persistent handoff", "save state", "resume", "continue saved work", "pick up where", "continue later", "take over".

## Creating vs Resuming

- User wants to **create**: They're wrapping up or switching agents → follow `create.md`
- User wants a **minimal** handoff: simple task or quick transfer → follow `quick.md`
- User wants to **resume**: They're starting fresh with existing handoff → follow `resume.md`

## Proactive Suggestions

Consider suggesting a handoff when:
- User says "I need to go" or "let's stop here"
- A significant milestone is reached
- You've been working for a long time with lots of context

Say: "Want me to create a handoff so you (or another agent) can continue later?"

## Behaviors

| Behavior | Instructions | Use When |
|----------|--------------|----------|
| Full handoff | `create.md` | Full handoff with all context |
| Quick handoff | `quick.md` | Minimal handoff, just essentials |
| Resume | `resume.md` | Continue from existing handoff |

## Cross-Agent Interop

`HANDOFF.md` lives at the repo root by convention and follows the same template across Claude Code and codex. A handoff produced by one agent must be resumable by the other without translation.
