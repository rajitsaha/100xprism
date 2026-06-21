---
name: push
description: Quality gate re-runs before pushing. **Do NOT push if any gate fails.**
category: lifecycle
tier: core
slash_command: /push
---

# Push — Gate → Push → Monitor CI/CD

Quality gate re-runs before pushing. **Do NOT push if any gate fails.**

## Do NOT ask for permission. Do NOT use `--no-verify` or `--force`.

---

## Phase 0 — Quality Gate (MANDATORY)

Check gate cache first — skip if it already passed for this exact tree state. The cache
is keyed on a tree token (HEAD + tracked diff + untracked files), not a bare HEAD, so a
dirty or rebased tree never reuses a stale pass — the same token `/commit` writes and the
`gate-on-commit` hook checks:

```bash
TOKEN=$(python3 ~/100xprism/hooks/gate-pass.py --print 2>/dev/null)
CACHED=$(cat ~/.100xprism/gate-cache 2>/dev/null)
[ -n "$TOKEN" ] && [ "$CACHED" = "$TOKEN" ] && echo "Gate: skipped (already passed for this tree)" && GATE_DONE=true || GATE_DONE=false
```

If `GATE_DONE=false`: run the **gate** workflow. On pass, record it:
```bash
python3 ~/100xprism/hooks/gate-pass.py
```

Do NOT push until gate passes. If gate fails → STOP, fix, clear cache, new commit, re-run.

---

## Phase 0b — Code Review (pre-push)

Check review cache — skip if HEAD already reviewed by a prior `/commit` run:

```bash
HEAD=$(git rev-parse HEAD)
REVIEWED=$(cat ~/.100xprism/review-cache 2>/dev/null)
[ "$REVIEWED" = "$HEAD" ] && echo "Review: skipped (already done for $HEAD)" && REVIEW_DONE=true || REVIEW_DONE=false
```

If `REVIEW_DONE=false`, review unpushed commits now:

```bash
PR=$(gh pr list --head "$(git branch --show-current)" --json number -q '.[0].number' 2>/dev/null)
```

- **PR exists** → run the **code-review** skill: `/review $PR`
- **No PR** → spawn **one** review Agent on `git diff origin/main..HEAD` covering the five dimensions defined in the **commit** skill Phase 5 (bugs, security, architecture, design, CLAUDE.md). Single-pass — no separate agents per dimension.

On clean review:
```bash
echo "$HEAD" > ~/.100xprism/review-cache
```

**Critical or High issues** → fix, new commit, clear cache, re-run gate. Do NOT push.
**Minor issues** → logged, non-blocking.

---

## Phase 1 — Push

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT"
git push origin main
```

Let any pre-push hooks run. **Never bypass with `--no-verify`.**

---

## Phase 2 — Handle pre-push hook failures

If the hook fails:
1. Read the failure output carefully
2. Fix the root cause — never bypass
3. Create a **NEW commit** with the fix (never `--amend` over a pushed commit)
4. Re-run the **gate** workflow to confirm fixes pass
5. Push again

If push is rejected (non-fast-forward):
```bash
git pull --rebase origin main
git push origin main
```

---

## Phase 3 — Monitor GitHub Actions & Auto-Fix

```bash
gh run list --limit 3
RUN_ID=$(gh run list --limit 1 --json databaseId -q '.[0].databaseId')
gh run watch "$RUN_ID"
```

No timeout — watch until CI completes or fails.

### If CI passes → continue to Phase 4.

### If CI fails → Auto-fix loop (max 3 attempts)

```bash
gh run view "$RUN_ID" --log | tail -100
```

Read the failure logs and classify the error:

**Auto-fixable failures (language-agnostic):**

| Failure type | Detection signals | Fix strategy |
|:-------------|:------------------|:-------------|
| Lint/format | ESLint, Prettier, ruff, black, gofmt, rustfmt, checkstyle, rubocop, swiftlint, ktlint | Run the **lint** workflow, commit fixes |
| Type errors | TypeScript (`tsc`), mypy, pyright, Go compiler, Rust compiler (`rustc`), Java (`javac`), Kotlin | Read errors, fix types, commit |
| Test failures | Jest, Vitest, pytest, Go test, cargo test, JUnit, RSpec, PHPUnit, XCTest, ExUnit | Read failing test, fix test or code, commit |
| Dependency issues | npm/yarn/pnpm lockfile, pip requirements, go.mod, Cargo.lock, Maven/Gradle, Bundler, Composer | Install/update deps, commit lockfile |
| Build failures | webpack, vite, esbuild, Go build, cargo build, Maven/Gradle build, make, CMake, Swift build | Read build errors, fix, commit |

**Detection logic:**
1. Read CI log output
2. Identify the tool/framework from error signatures
3. Match to the fix strategy above
4. If no match → classify as unfamiliar → escalate

**For each auto-fix attempt:**
1. Apply the fix
2. Create a new commit (never amend)
3. Re-run the **gate** workflow
4. Push again
5. Monitor CI again

```
CI FAILED — Attempt N/3
Failure:  [tool/framework] [error type]
Fix:      [what was done]
Action:   Committing fix, re-running gate, re-pushing...
```

**Unfamiliar failures (escalate to human after any attempt):**
- Infrastructure errors (Docker build fails in CI but not locally)
- Permission / secrets / environment variable errors
- Timeout / flaky test patterns (same test passes locally)
- Network connectivity issues
- Unknown error codes or tools

```
╔══════════════════════════════════════════════════════╗
║            CI FAILED — ESCALATING TO HUMAN            ║
╠══════════════════════════════════════════════════════╣
║ Attempts:   N/3 exhausted (or unfamiliar failure)    ║
║ Last error: [error summary]                          ║
║ Diagnosis:  [root cause analysis]                    ║
║ Suggestion: [recommended fix]                        ║
╠══════════════════════════════════════════════════════╣
║ This requires human judgment. Auto-fix not possible.  ║
╚══════════════════════════════════════════════════════╝
```

**After 3 failed auto-fix attempts → STOP. Report all attempted fixes and escalate.**

---

## Phase 4 — Production verification

After all CI/CD workflows succeed:

```bash
# Detect project instruction file
INSTRUCTION_FILE=$(for f in CLAUDE.md AGENTS.md .cursorrules .windsurfrules .github/copilot-instructions.md GEMINI.md; do [ -f "$PROJECT_ROOT/$f" ] && echo "$PROJECT_ROOT/$f" && break; done)
```

1. **Health checks** — read health endpoint URLs from the project instruction file or README. Hit each and confirm HTTP 200:
```bash
[ -n "$INSTRUCTION_FILE" ] && grep -E "https?://[^ ]*/health" "$INSTRUCTION_FILE" 2>/dev/null | head -3
# Also try common defaults:
# /health, /healthz, /api/health, /status
```

Retry up to 5 times with 10-second intervals (deployment may still be rolling out).

2. **Confirm deployment** — verify the deployed version matches the pushed commit if a version endpoint exists.

---

## Output

```
=== Push Complete ===
Gate:     ✅ ALL GATES PASSED
Push:     <branch> → origin/<branch> ✅
CI/CD:    ✅ All workflows passed (N auto-fixes applied)
Health:   ✅ All endpoints responding
Status:   SHIPPED ✅
```
