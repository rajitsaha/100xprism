# 100x-dev — Power-Up Roadmap

> Modernizing 64 modules · 7 adapters · 12 plugins for the mid-2026 frontier-model era
> (Opus 4.8 @ 1M context · Sonnet 4.6 · Haiku 4.5 · Workflow tool · native hooks · MCP).
>
> **Source of truth:** [`docs/superpowers/specs/2026-05-31-100x-powerup-review-design.md`](docs/superpowers/specs/2026-05-31-100x-powerup-review-design.md)
> Generated from a 9-reviewer audit (105 findings, 33 net-new ideas) + adversarial verification.

## Guiding insight

`adapters/lib/modules.py` is **the fan-out point** — any capability taught there propagates to all 64 modules across 7 platforms at once. Today it emits only prose and silently drops every modern affordance (model routing, hooks, Workflows, structured output, MCP). Most leverage lives at the generator layer, not in individual skills.

## Sequencing rule

Sequencing matters more than raw priority labels. The **highest-leverage edits (model routing, fan-out) are also the highest-blast-radius** — they touch all 64 modules × 7 adapters. So the **eval/meta safety net is built first**, and it supplies the cost/quality-per-tier evidence that makes model re-tiering falsifiable instead of a guess.

---

## Phase 0 — Immediate, zero blast radius

Ship now, decoupled from everything else.

| Issue | Title | P/E |
|------|-------|-----|
| [#23](https://github.com/rajitsaha/100x-dev/issues/23) | **Version bump `package.json` 2.0.1 → 2.0.4 (do this line-1 first)** + plugin-count drift + CI meta job | P0/M |

*The npm-published version is 3 releases stale — a live user-facing bug. Bump it standalone immediately; the larger meta-CI portion of #23 belongs in Phase 1.*

## Phase 1 — Safety net (prerequisite for Phases 2–3)

Build the verification layer **before** any high-blast-radius edit. Nothing in Phase 2 ships until this is green.

| Issue | Title | P/E | Note |
|------|-------|-----|------|
| [#24](https://github.com/rajitsaha/100x-dev/issues/24) | Build an eval harness that runs the 32 dormant `evals.json` in CI | P0/L | Haiku-4.5 graders, Workflow fan-out, structured output. **Add a cost/quality-per-tier scorecard** — this is what justifies model re-tiering. |
| [#23](https://github.com/rajitsaha/100x-dev/issues/23) | CI meta/consistency job (version triple, counts, frontmatter parse) | P0/M | Separate workflow from the existing `github-actions/ci.yml` *template*. |
| [#33](https://github.com/rajitsaha/100x-dev/issues/33) | Trigger-overlap lint (subset) | P1/M | Deterministic check so routing/fan-out edits can't silently break triggering. |

## Phase 2 — P0 leverage plays

Gated on Phase 1. These are the repo-wide multipliers.

| Issue | Title | P/E | Note |
|------|-------|-----|------|
| [#21](https://github.com/rajitsaha/100x-dev/issues/21) | Wire model routing into `modules.py` (the `<!-- model -->` comments are a no-op) | P0/M | Add `MODEL_ALIASES` → current IDs; add a `sonnet` middle tier; ensure all 7 emitters tolerate/strip the new key. Non-Claude adapters get a documentation-only `_Runs best on:_` hint, **not** functional routing. |
| [#22](https://github.com/rajitsaha/100x-dev/issues/22) | First-party enforcing hooks (gate-on-commit, secret-scan) + `emit-hooks` generator | P0/L | **Must land with the gate-cache tree-hash fix** — switch push cache key from bare `HEAD` to a clean-tree tree-hash, or the hook hardens a broken foundation. |

## Phase 3 — P1 capability upgrades

| Issue | Title | P/E |
|------|-------|-----|
| [#25](https://github.com/rajitsaha/100x-dev/issues/25) | Workflow/subagent fan-out for `orchestrate`, `gate`, `test`, `launch` | P1/M |
| [#26](https://github.com/rajitsaha/100x-dev/issues/26) | Shared stack-detection helper; de-hardcode GCP/Firebase/Stripe/npm/pytest | P1/L |
| [#27](https://github.com/rajitsaha/100x-dev/issues/27) | Create missing `test` e2e-patterns ref; make thresholds/DB images configurable | P1/M |
| [#28](https://github.com/rajitsaha/100x-dev/issues/28) | Parameterize Co-Authored-By trailer; replace blocking sleeps in commit/release | P1/S |
| [#29](https://github.com/rajitsaha/100x-dev/issues/29) | Refresh stale external facts in marketing skills (dated, capability-based) | P1/M |
| [#30](https://github.com/rajitsaha/100x-dev/issues/30) | Agentic research paths (WebSearch/WebFetch + subagent fan-out) for SEO/sales | P1/M |
| [#31](https://github.com/rajitsaha/100x-dev/issues/31) | Unify data access: delegate `/query` to db-engines, add MCP paths | P1/M |
| [#32](https://github.com/rajitsaha/100x-dev/issues/32) | Cross-cloud security: split `cloud-security` by provider, de-hardcode region | P1/L |
| [#33](https://github.com/rajitsaha/100x-dev/issues/33) | Resolve triggering overlaps; consolidate near-duplicate skills | P1/M |
| [#34](https://github.com/rajitsaha/100x-dev/issues/34) | Per-module emitters + MCP-aware integration for Codex/Gemini/connect | P1/L |

## Phase 4 — P2 net-new capabilities

| Issue | Title | P/E |
|------|-------|-----|
| [#35](https://github.com/rajitsaha/100x-dev/issues/35) | Pin plugins via lockfile; reconcile `ui-ux-pro-max-skill` key mismatch; drop native-duplicate plugins | P2/M |
| [#36](https://github.com/rajitsaha/100x-dev/issues/36) | Runtime AI-personalization + LLM-as-analyst for CRO/lifecycle | P2/L |
| [#37](https://github.com/rajitsaha/100x-dev/issues/37) | `emit-workflow`: generate platform Workflow definitions from phased modules | P2/L |
| [#38](https://github.com/rajitsaha/100x-dev/issues/38) | Repo-grounded design generators with model frontmatter + project Step 0 | P2/L |

---

## Dependency graph (critical path)

```
#23 version bump ─────────────────────────────► (ship today)

#24 eval harness ┐
#23 meta CI      ├─► #21 model routing ─► #25 fan-out ─► #37 emit-workflow
#33 trigger lint ┘                       └─► re-tiering (needs #24 cost scorecard)

gate-cache tree-hash fix ─► #22 enforcing hooks
```

## Cross-cutting guardrails (apply to every phase)

- **Cost/latency budget.** Every routing/fan-out change pushes work toward bigger models + parallel subagents → more $/run and latency. Default-cheap → escalate-on-uncertainty; cap fan-out concurrency; let #24 measure quality-per-dollar per tier.
- **Single source of truth.** `plugins.json` and `modules.py` drive counts/lists — kill the hand-maintained parallel lists in `README.md` and `update.sh`.
- **Migration safety.** Promoting `<!-- model -->` to real frontmatter changes what all 7 emitters see — run meta/eval CI before it lands.
