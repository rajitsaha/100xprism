---
name: subagents
description: Use subagents to throw more compute at hard problems, keep the main context window clean, and auto-approve safe permissions via hooks. Use when a task is complex, exploratory, or can be parallelized across multiple independent workstreams.
category: engineering
tier: on-demand
allowed-tools: Agent Task Workflow
---

Invoke this skill when a task is complex, exploratory, or parallel in nature. Subagents keep the main context clean and focused.

## When to Use Subagents

- Append "use subagents" to any request where you want Claude to throw more compute at the problem
- Use for: codebase exploration, parallel analysis, research, large refactors, test writing
- Do NOT use for: simple one-file edits, quick lookups, single-step tasks

## Three Subagent Strategies

### A. Parallelization
Offload independent tasks to separate subagents running simultaneously:
```
use 5 subagents to explore the codebase
→ Explore entry points and startup
→ Explore React component structure
→ Explore tools implementation
→ Explore state management
→ Explore testing infrastructure
```

### B. Context Isolation
Offload heavy research or analysis to a subagent so the main agent's context stays focused on the implementation.
- Research → subagent → returns a structured result (see *Standard return contract* below)
- Main agent uses the result, never sees the raw noise

**Reframe for the 1M-context era:** with a 1M-token window, token savings is no longer the
main reason to isolate. Isolate for **parallelism** (independent work running at once) and
**adversarial independence** (a fresh subagent with zero authoring context reviews work
without self-review bias). Treat context savings as a side benefit, not the goal.

### C. Permission Routing via Hook
Auto-approve obviously-safe tool calls so they don't interrupt with a prompt, while
anything risky still falls through to human review. 100x-dev ships this as a real,
installable artifact — not just advice:

- **Artifact:** `~/100x-dev/hooks/permission-router.py` (a `PreToolUse` Bash hook).
- **Tier 1 (offline):** a deterministic allowlist auto-approves read-only commands
  (`ls`, `cat`, `git status`, `grep`, …); destructive/network/credential commands are
  never auto-approved.
- **Tier 2 (optional):** set `HOOK_ROUTER_MODEL=claude-haiku-4-5` (needs the `claude`
  CLI) to route ambiguous commands to a cheap model; only a confident "safe" verdict
  grants permission, so escalation to a deeper model (e.g. Opus 4.8) or a human is the
  default for anything uncertain. The router **never blocks** — it only grants.
- **Enable it:** re-run the installer and turn on the *permission-router* hook (it ships
  off by default), or run `python3 ~/100x-dev/adapters/lib/modules.py emit-hooks` with
  `HOOK_ROUTER=1`.
- See `~/100x-dev/hooks/README.md` and the hooks docs:
  <https://docs.claude.com/en/docs/claude-code/hooks>

## Fan-out ladder (how to parallelize)

When a skill says "fan out" or "run these in parallel", pick the highest rung your
platform supports — they all reach the same outcome, only the determinism differs:

1. **`Workflow` tool (best — Claude Code).** Use it for deterministic, auditable fan-out:
   one stage per independent unit, structured-output return per agent, a reduce step that
   aggregates verdicts. Phase order and concurrency caps are enforced by the runtime, not
   by prose.
2. **Parallel `Agent`/`Task` subagents (good).** Dispatch the independent units as
   concurrent subagents in a single message; gather their structured returns and reduce.
   No runtime enforcement, but real parallelism.
3. **Serial in-context (fallback — any platform).** If neither tool exists (most
   non-Claude-Code platforms), run the units one after another in the main context. The
   outcome is identical; only wall-clock and isolation are lost.

A fan-out site is only worth it when the units are **genuinely independent** (no shared
mutable state, no ordering dependency). Anything that shares a resource — one Docker DB,
one build dir — stays serial or needs isolation (worktrees).

## Standard return contract

Exploration / research / review / verdict subagents return a **fixed structure, not
prose**, so the parent can reduce deterministically (sort by severity, filter by
confidence, dedup by file):

```json
{
  "summary":    "one-line headline of what was found",
  "findings":   [{ "severity": "critical|high|medium|low", "title": "", "file": "path:line", "detail": "" }],
  "files":      ["paths touched or inspected"],
  "risks":      ["what could be wrong / what wasn't covered"],
  "confidence": "high|medium|low"
}
```

Skills adapt the shape to their job (e.g. `gate` subagents return
`{gate, status, findings[], severity}`), but the principle is constant: **structured in,
structured out**. With the `Workflow` tool, pass this as the agent's `schema` so the
return is validated automatically.

**Model routing for fan-out:** route breadth/mechanical scans (file sweeps, lint, simple
greps) to **Haiku** for cheap concurrency; route depth/judgment (security triage,
root-cause analysis, go/no-go verdicts) to **Opus** with extended thinking. Default
cheap, escalate on uncertainty.

## Usage Patterns

```
# More compute on a hard problem
"Refactor the auth system. Use subagents."

# Parallel exploration
"Use 5 subagents to map out every API endpoint and their dependencies"

# Background agent (Claude Code only)
ctrl+b  →  runs current task in background agent
```

## Principles

- One focused task per subagent
- Subagents return summaries, not raw data dumps
- Use background agents (ctrl+b, Claude Code only) for long-running tasks so you can keep working
- Subagents are especially valuable for: codebase mapping, test generation, doc writing, and competitive analysis
