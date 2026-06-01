# Design — Issue #26: Shared stack-detection helper + de-hardcode GCP/npm

_Source: 100x-dev power-up review (Theme 6). Priority P1 · Effort L._
_Parent spec: `2026-05-31-100x-powerup-review-design.md`._

## Problem

`architect`, `issue`, and `fix-bugs` independently hardcode GCP+Firebase+Stripe+Resend
and npm/pytest, producing wrong-stack advice and broken commands (e.g.
`gcloud logging … --project=<project>` runs the literal placeholder) for
AWS/Vercel/pnpm/go/cargo users. `architect` also has a malformed `description: >`
block-scalar that strips its natural-language triggers, and a "property lookups" domain
leak from the source project.

## Constraints

- Each skill is emitted **standalone to 7 platforms**, so a shared *runtime* file isn't
  reliably present (the fragility #31 flags for db's hardcoded `~/.claude` path). "Detected
  once" therefore means **one canonical snippet** embedded per skill, with a single
  maintainer source of truth — not a sourced file.
- **No generator changes.** The shared doc is `reference.md` (not `SKILL.md`), so it is
  never globbed, emitted, or counted; meta-check stays green.
- cloud-security's full multi-cloud split is **#32** — here we only add the shared
  detector it will later consume.

## Design

### Canonical detection block — source of truth: `modules/_lib/reference.md`

Restores `_lib` (dropped in the v2.0.0 unification) as a **reference-only** maintainer
doc holding the shared conventions (preamble, autonomy banner, GATE format, `/connect`
credentials) **plus** a new stack-detection preamble. Pure detection, no output — sets
shell vars the prose branches on:

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel); cd "$PROJECT_ROOT"
INSTRUCTION_FILE=$(for f in CLAUDE.md AGENTS.md .cursorrules .windsurfrules .github/copilot-instructions.md GEMINI.md; do [ -f "$PROJECT_ROOT/$f" ] && echo "$PROJECT_ROOT/$f" && break; done)

# CLOUD: gcp | aws | azure | vercel | ""   (gcloud config / aws / .vercel / terraform / instruction-file grep)
# TEST_RUNNER: jest | vitest | pytest | "go test" | "cargo test" | ""   (lockfiles + manifests)
# CI_SYSTEM: github-actions | gitlab | circle | ""   (.github/workflows, .gitlab-ci.yml, .circleci)
```

Detection is **lightweight** — config + lockfile + IaC + instruction-file greps, no
network calls. Each of the 3 skills embeds the block verbatim at its first step and cites
`_lib` as the source.

### Per-skill changes

- **`architect`**
  - Replace the `description: >` block-scalar with a keyword-packed one-liner (triggers:
    "should we use X or Y", scaling, cost, data-tier, multi-tenancy, decision matrix);
    move the Scope note into the body.
  - Run the detection block in Step 1; reframe Steps 2–3 lenses as **provider-neutral**
    (compute / networking / data-tier / resilience) with **GCP and AWS named as parallel
    examples**, not the assumed stack. Genericize the Step 7 topology diagram.
  - Delete the "should not block **property lookups**" domain leak → "unrelated requests".
- **`issue`**
  - Detection block in Phase 1; gate the `gcloud logging` read behind `CLOUD=gcp` and
    resolve the project from `gcloud config get-value project`, not the literal
    `<project>`.
  - Rename dimension 2.3 "Cloud Architecture" provider-neutral; soften 2.5's hardcoded
    Stripe/Firebase/Resend to "payment / auth / email providers (e.g. …)".
  - Upgrade the duplicate-issue check from `gh issue list | grep` to fetching titles +
    bodies and matching semantically; gate behind `gh` availability.
- **`fix-bugs`**
  - Move the `systematic-debugging` escalation from the end note **up to Phase 1** as a
    branch (root cause unclear → diagnose first).
  - Gate the Phase 1 `gh run` path behind `CI_SYSTEM=github-actions` + `gh` present.
  - Phase 4 verify uses `$TEST_RUNNER` (or defers to `/test`) instead of hardcoded
    `npm test` / `pytest`.

## Acceptance criteria (from #26)

- [ ] On a non-GCP repo, none of the three skills emit gcloud commands or GCP-only sections.
- [ ] `architect` triggers on "should we use X or Y / scaling / decision matrix" without
      naming the command.
- [ ] `fix-bugs` uses the detected test runner.

## Testing / verification

- `meta-check.py` (counts unchanged — `_lib` has no SKILL.md), 46 repo tests,
  `trigger-overlap.py --strict`, `eval-harness validate`.
- `modules-frontmatter.test.js` must accept architect's new one-liner description.
- Manual: confirm the GCP sections are gated and the AWS/Vercel/go/cargo paths read
  correctly; confirm architect's description yields real trigger phrases.
