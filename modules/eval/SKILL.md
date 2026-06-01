---
name: eval
description: Run a module's evals to check it triggers correctly and produces good output. Use when the user wants to evaluate a skill, run evals, grade eval assertions, score skill quality, or asks "do my skills still trigger correctly" or "did this change break a skill". Fans eval cases out to parallel subagents and has Haiku 4.5 grade each assertion into a pass/fail scorecard.
category: quality
tier: on-demand
slash_command: /eval
allowed-tools: Bash Read Agent
---

# Eval — Run skill evals and score them

Turns the dormant `modules/<slug>/evals/evals.json` files into a real, graded scorecard:
does the skill trigger on its prompts, and does its output satisfy each assertion?

The deterministic engine is `scripts/eval-harness.py` (discovery, validation, work-list,
scorecard rendering — no model calls). **Grading is your job**: fan the cases out to
subagents and have Haiku 4.5 judge every assertion with structured output. Installed path
is `~/100x-dev/scripts/eval-harness.py`; in a checkout it's `scripts/eval-harness.py`.

## Phase 0 — Pick the target

```bash
# one module, everything, or just what changed on this branch:
python3 ~/100x-dev/scripts/eval-harness.py validate --module <slug>
python3 ~/100x-dev/scripts/eval-harness.py validate --all
python3 ~/100x-dev/scripts/eval-harness.py validate --changed origin/main
```

Fix any structural errors before grading — a malformed eval file can't be scored.

## Phase 1 — Get the work-list

```bash
python3 ~/100x-dev/scripts/eval-harness.py plan --module <slug> --json
```

This emits `{ "modules": [ { "module", "cases": [ { id, prompt, expected_output,
assertions[], files[] } ] } ] }`. Each `(case, assertion)` is one unit of work.

## Phase 2 — Grade with parallel subagents (Haiku 4.5)

For each case, dispatch **one subagent** (use the **subagents** skill / `Agent` tool, or a
Workflow fan-out) that:

1. **Runs the prompt against the skill.** Load the target skill (its SKILL.md) as
   context, then answer the case `prompt` exactly as the assistant would — this is the
   *candidate response*. Note whether the skill would have auto-triggered on that prompt
   (trigger accuracy) separately from output quality.
2. **Grades each assertion.** Spawn a Haiku 4.5 grader (`model: claude-haiku-4-5`) that,
   given the prompt, the candidate response, the `expected_output`, and one assertion,
   returns **structured output**:

   ```json
   { "module": "<slug>", "case_id": <id>, "assertion": "<text>", "passed": true|false, "reason": "<one line>" }
   ```

Run cases in parallel (independent), cap concurrency sanely, and collect every grader
object into one results array. Default cheap (Haiku) and only escalate a genuinely
ambiguous assertion to a stronger model.

Write the collected array to a results file:

```bash
# results.json = the JSON array of grader objects from every subagent
```

## Phase 3 — Render the scorecard

```bash
python3 ~/100x-dev/scripts/eval-harness.py score --results results.json
```

Produces a per-assertion ✓/✗ scorecard with reasons, per-case and overall tallies, and
exits non-zero if any assertion failed. Use `--json` for machine output.

```
=== marketing-psychology ===
  case 1: 6/7 assertions passed
    ✓ Checks for product-marketing-context.md
    ✗ References specific mental models by name — named only two of the taxonomy
scorecard: 41/43 assertions passed across 1 module(s)
```

## Trigger-overlap lint (no model needed)

Catch skills that would fire on each other's prompts before they ever reach grading:

```bash
python3 ~/100x-dev/scripts/trigger-overlap.py            # report flagged pairs
python3 ~/100x-dev/scripts/trigger-overlap.py --strict   # fail on NEW (non-allow-listed) overlaps
```

Intentional overlaps live in `scripts/trigger-overlap-allow.txt`. If a real change adds a
new high-overlap pair, either differentiate the two descriptions or add the pair to the
allow-list with a note.

## Principles

- **Default cheap.** Haiku grades; escalate only ambiguous assertions.
- **Structured output, not prose.** Every grader returns the object above so the scorecard
  is deterministic.
- **Separate trigger accuracy from output quality.** A skill can answer well yet never
  have triggered — report both.
- **Per-PR vs nightly.** Grade only `--changed` modules on a PR; the nightly CI run covers
  all modules that ship evals.
