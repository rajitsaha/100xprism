# Design — Issue #25: Workflow/subagent fan-out for orchestrate, gate, test, launch

_Source: 100x-dev power-up review (Theme 5). Priority P1 · Effort M._
_Parent spec: `2026-05-31-100x-powerup-review-design.md`._

## Problem

`orchestrate`, `gate`, `test`, and `launch` run independent work serially in a single
context — a pre-platform-feature pattern. The `Workflow` tool, parallel `Agent`/`Task`
subagents, and structured-output return contracts now make deterministic fan-out cheap
and auditable. The skills' own prose ("use subagents", "throw more compute at it") shows
intent; the concrete wiring never landed.

## Constraints

- These skills are emitted to **7 platforms**. `Workflow`/`Agent`/`Task` are
  Claude-Code-specific (adversarial-pass correction #4: non-Claude routing is
  documentation-only). Guidance must degrade gracefully.
- **No generator changes.** Pure prose edits to 5 `SKILL.md` files — keeps #25 out of
  the per-module-emitter lane (#34).
- The `e2e-patterns.md` broken reference (test/SKILL.md:384) is **#27's** scope, not
  touched here.

## Design

### Shared convention (lands in `subagents` first)

Two reusable primitives the other four skills reference rather than redefine:

1. **The fan-out ladder** — one capability-gated instruction:
   > Prefer the `Workflow` tool for deterministic, auditable fan-out. If unavailable,
   > dispatch parallel `Agent`/`Task` subagents. If neither exists (non-Claude-Code
   > platforms), run the phases serially in-context.

2. **The structured return contract** — exploration/review/verdict subagents return a
   fixed schema, not prose, so the parent reduces deterministically:
   ```
   { summary, findings[] {severity, title, file, detail}, files[], risks[], confidence }
   ```
   Plus: model-routing note (**Haiku for breadth/mechanical, Opus for depth/judgment**)
   and a reframe of "Context Isolation" — with 1M context, isolate for **parallelism +
   adversarial independence**, not token savings.

### Per-skill changes

- **`orchestrate`** — add a "Fan-out execution" section: plan → parallel implementation
  subagents (one per workstream) → gather structured diffs → independent `/code-review`
  reviewer subagent → merge/verify gate. Reference `dispatching-parallel-agents` +
  `subagent-driven-development`. Decouple the hardcoded `docs/superpowers/plans/...`
  path (use native plan mode / `writing-plans` output location).
- **`gate`** — Gates 2/3/4/5 (security, build, docker, cloud-security) are mutually
  independent → fan out as parallel subagents each returning a
  `{gate,status,findings[],severity}` verdict, reduced into the existing summary box.
  Gate 1 (the test loop) stays the long pole. Verdicts cache on the **tree hash**
  (consistent with the `gate-pass.py` token, not bare HEAD).
- **`test`** — Phase 1 unit layers (Vitest FE / Jest BE / pytest Python) are
  independent → fan out before the shared-Docker integration phase (Phase 2), which
  stays serial because it shares one DB.
- **`launch`** — after the Phase 1 test gate, fan out Phase 2/3/4 (lint ‖ security ‖
  build). Replace the prose "retry 5× / sleep 10s" health check with a bounded backoff
  loop. Fix the gate-cache drift at line 116 (`echo HEAD > gate-cache` →
  `gate-pass.py`) so it matches the #22 hook contract.

## Acceptance criteria (from #25)

- [ ] `orchestrate` documents a concrete Workflow fan-out example.
- [ ] `gate` aggregates parallel structured verdicts rather than serial ASCII boxes.
- [ ] `subagents` specifies a structured return schema.

## Testing / verification

- `python3 adapters/lib/modules.py` regen + meta-check CI must still pass (frontmatter
  parse, counts). Run the eval harness on the 4 changed modules.
- Manual read-through: confirm the serial fallback path is intact for non-CC platforms.
- `/gate` before commit.
