---
name: fix-bugs
description: Autonomously fix bugs from any source — Slack threads, failing CI tests, docker logs, or a plain description. Use when you have a bug report or failing test and want Claude to investigate and fix without step-by-step guidance.
category: engineering
tier: core
slash_command: /fix
allowed-tools: Bash Read Grep Glob Edit Write
---

# Fix — Autonomous Bug Fixer

Fix the bug described or linked. Investigate and fix without step-by-step guidance.

## Do NOT ask for permission — investigate and fix autonomously.

## How to use
- `/fix` — fix the most recent failing CI run
- `/fix <description>` — fix from plain description
- `/fix <docker logs paste>` — trace error to root cause and fix
- `/fix <Slack URL or paste>` — read thread, extract bug report, fix

---

## Phase 1 — Gather context

Read the error in full — message, stack trace, or thread. Do not skip this step.

**If the root cause is not obvious from the error/stack/logs → invoke the
`systematic-debugging` skill first to diagnose before attempting any fix.** `/fix` assumes
you know (or can quickly find) what's broken; for mysterious failures, diagnosis comes
first. Don't guess.

Detect the stack so context-gathering matches the project (canonical block — source:
`_lib/reference.md`):

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel); cd "$PROJECT_ROOT"
CI_SYSTEM=""; ls "$PROJECT_ROOT"/.github/workflows/*.y*ml >/dev/null 2>&1 && CI_SYSTEM=github-actions
TEST_RUNNER=""
grep -q '"vitest"' package.json */package.json 2>/dev/null && TEST_RUNNER=vitest
[ -z "$TEST_RUNNER" ] && grep -q '"jest"' package.json */package.json 2>/dev/null && TEST_RUNNER=jest
[ -z "$TEST_RUNNER" ] && { [ -f pyproject.toml ] || ls requirements*.txt >/dev/null 2>&1; } && TEST_RUNNER=pytest
[ -z "$TEST_RUNNER" ] && [ -f go.mod ] && TEST_RUNNER="go test"
[ -z "$TEST_RUNNER" ] && [ -f Cargo.toml ] && TEST_RUNNER="cargo test"

# If fixing CI on a GitHub-Actions repo with gh available: read the most recent failure
if [ "$CI_SYSTEM" = github-actions ] && command -v gh >/dev/null 2>&1; then
  gh run list --limit 3
  gh run view --log-failed "$(gh run list --limit 1 --json databaseId -q '.[0].databaseId')" 2>/dev/null | tail -80
fi
```

For non-GitHub CI, read the failing job from that provider (GitLab/CircleCI UI or CLI).
For docker logs: parse for ERROR/CRITICAL lines, identify service + file.
For Slack thread: extract the bug report and reproduction steps.

---

## Phase 2 — Locate the code

Find the exact file(s) and line(s) responsible. Use Grep/Glob aggressively — do not guess.

```bash
cd "$PROJECT_ROOT"
git log --oneline -10
git status
```

Read the surrounding code. Understand the intent before touching anything.

---

## Phase 3 — Fix

Make the **minimal correct change**. Do not refactor unrelated code. Do not add workarounds or feature flags — fix the root cause.

If the root cause cannot be determined: say so clearly. Do not guess.

---

## Phase 4 — Verify

Run the specific failing test or reproduce the failure condition, using the
**detected** `$TEST_RUNNER` from Phase 1 (or just defer to `/test`, which auto-detects
every layer):

```bash
# Target only the failing test(s) — substitute the file/name that was failing.
case "$TEST_RUNNER" in
  vitest|jest)  npx "$TEST_RUNNER" run <failing-test> 2>&1 | tail -30 ;;
  pytest)       ./venv/bin/pytest <failing-test> -v --tb=short -q 2>&1 | tail -30 ;;
  "go test")    go test ./<pkg> -run '<TestName>' 2>&1 | tail -30 ;;
  "cargo test") cargo test <test_name> 2>&1 | tail -30 ;;
  *)            echo "No test runner detected — run /test, or reproduce the failure manually" ;;
esac
```

**GATE: The specific failure condition no longer occurs.**

---

## Phase 5 — Summarize

One sentence: what was wrong and what you changed. Then run `/commit`.
