---
name: commit
description: Quality gate runs FIRST. **Do NOT commit if any gate fails.**
category: lifecycle
tier: core
slash_command: /commit
---

# Commit — Gate → Stage → Commit

Quality gate runs FIRST. **Do NOT commit if any gate fails.**

## Do NOT ask for permission. Do NOT skip the gate.

---

## Phase 0 — Quality Gate (MANDATORY)

Check gate cache first — skip if it already passed for this exact tree state. The
cache is keyed on a tree token (HEAD + tracked diff + untracked files), not a bare
HEAD, so a dirty or rebased tree never reuses a stale pass:

```bash
TOKEN=$(python3 ~/100x-dev/hooks/gate-pass.py --print 2>/dev/null)
CACHED=$(cat ~/.100x-dev/gate-cache 2>/dev/null)
[ -n "$TOKEN" ] && [ "$CACHED" = "$TOKEN" ] && echo "Gate: skipped (already passed for this tree)" && GATE_DONE=true || GATE_DONE=false
```

If `GATE_DONE=false`: run the **gate** workflow. On pass, record it (same token the
gate-on-commit hook checks):
```bash
python3 ~/100x-dev/hooks/gate-pass.py
```

Do NOT proceed until gate reports `✅ ALL GATES PASSED`. If any gate fails → STOP, fix, clear cache, re-run gate.

---

## Phase 1 — Review changes

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT"
git status
git diff --stat
git diff --name-only HEAD 2>/dev/null || true
```

---

## Phase 2 — Update docs (if needed)

Based on what changed, update corresponding documentation:

| Changed files | Doc to update |
|---|---|
| API routes or handlers | API reference doc (README, project instruction file, or `docs/`) |
| CLI commands or flags | CLI reference doc |
| New features or config | README.md or project-equivalent |
| Removed files/features | Remove stale references from all docs |
| `.claude/commands/**` | No doc update needed |

Read the project instruction file for specific doc file paths. Skip if no docs are affected.

---

## Phase 3 — Stage files

Stage only task-related files. Never stage `.env`, `dist/`, `node_modules/`, `venv/`, unrelated work:

```bash
git add -u
# Or stage specific files if -u picks up unrelated changes:
# git add path/to/file1 path/to/file2
git diff --staged --stat
```

---

## Phase 4 — Write and create commit

Use [Conventional Commits](https://www.conventionalcommits.org/). Focus on **why**, not just what.

| Type | When to use |
|---|---|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `test` | Adding or updating tests |
| `chore` | Tooling, config, scripts, CI |
| `docs` | Documentation only |
| `refactor` | Code change with no behavior change |
| `perf` | Performance improvement |
| `security` | Vulnerability fix |

```bash
git commit -m "$(cat <<'EOF'
<type>(<scope>): <short summary under 72 chars>

- <Key change 1 and why>
- <Key change 2 and why>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 5 — Code Review

Check review cache — skip if this HEAD was already reviewed:

```bash
HEAD=$(git rev-parse HEAD)
REVIEWED=$(cat ~/.100x-dev/review-cache 2>/dev/null)
[ "$REVIEWED" = "$HEAD" ] && echo "Review: skipped (already done for $HEAD)" && exit 0
```

Otherwise, run a full review:

```bash
git diff HEAD~1 --stat
PR=$(gh pr list --head "$(git branch --show-current)" --json number -q '.[0].number' 2>/dev/null)
[ -n "$PR" ] && echo "PR #$PR found" || echo "No PR — running diff review"
```

**If a PR exists** → run the **code-review** skill: `/review $PR`

**If no PR** → spawn **one** review Agent on `git diff HEAD~1`. The Agent reads the full diff and scans surrounding codebase for context. Cover all five dimensions in a single pass:

### Review dimensions (all required)

**1. Bug review**
- Null/undefined dereferences, off-by-one errors, wrong conditions
- Unhandled error paths, missing awaits, silent catch blocks
- Race conditions, state mutation side effects

**2. Security review**
- Injection vectors (SQL, shell, XSS), auth bypass, missing input validation
- Exposed secrets or tokens, insecure defaults, overly permissive CORS/headers
- Privilege escalation risks in new routes or handlers

**3. Architecture review**
- Does the change respect existing layer boundaries (e.g. routes → services → DB, not routes → DB directly)?
- Does it introduce patterns inconsistent with the rest of the codebase?
- Are new abstractions justified, or is this premature generalization?
- Does it create circular dependencies or tight coupling between modules?
- Does it belong in the right layer/module — or is it a responsibility leak?

**4. Design review**
- Are names (variables, functions, files) clear and consistent with existing conventions?
- Is the interface (function signatures, return types, API shape) clean and minimal?
- Is there unnecessary duplication that should be extracted — or over-extracted abstraction that should be inlined?
- Do new data structures match the project's existing patterns (shape, naming, serialization)?
- Are SOLID principles respected — single responsibility, open/closed, dependency inversion?

**5. CLAUDE.md / project rules compliance**
- Read the project instruction file and verify all new code follows its conventions
- Flag any deviation from documented patterns, naming rules, or architectural decisions

### Severity

| Level | Action |
|---|---|
| **Critical** | Bug, security hole, or architectural violation that breaks correctness or safety → fix before push |
| **High** | Design smell or pattern inconsistency that will compound over time → fix before push |
| **Minor** | Style, naming preference, small improvement → log, non-blocking |

**Critical or High issues found** → fix now, create a new commit, re-run gate. Do NOT push.

On clean review, cache the result so push skips re-reviewing the same HEAD:
```bash
echo "$(git rev-parse HEAD)" > ~/.100x-dev/review-cache
```

---

## Phase 6 — Verify

```bash
git log --oneline -3
```

---

## Output

```
=== /commit Complete ===
Gate:         ✅ ALL GATES PASSED
Staged files: N
Commit:       <short-hash> <message>
Review:       ✅ No critical issues | ⚠️ N minor notes
Status:       COMMITTED ✅
```
