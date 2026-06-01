---
name: pr
description: Create a GitHub Pull Request with full AI review. Human approves and merges.
category: lifecycle
tier: core
slash_command: /pr
---

# PR — Create Pull Request with AI Review

Create a GitHub Pull Request with full AI review. Human approves and merges.

## Do NOT merge automatically. Always stop after PR creation and AI review.

---

## Step 0 — Smart default: ensure feature branch

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT"
DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
DEFAULT_BRANCH="${DEFAULT_BRANCH:-main}"
CURRENT_BRANCH=$(git branch --show-current)
```

**If on the default branch:** Run the **branch** workflow first to create a feature branch. Then continue.

**If on a feature branch:** Continue with PR creation.

---

## Phase 1 — Pre-flight

Run the **gate** workflow. All gates must pass before creating a PR (Gates 1–3 always; Gates 4–5 when applicable).

**If any gate fails → STOP. Fix issues first. Do NOT create a PR with failing gates.**

---

## Phase 2 — Push branch

```bash
git push -u origin "$CURRENT_BRANCH"
```

---

## Phase 3 — Generate PR content

### Title
- Derive from branch name and commit messages
- Under 70 characters
- Use conventional format: `feat: add user authentication`

### Body
Generate from the diff against the default branch:

```bash
git log "$DEFAULT_BRANCH"..HEAD --oneline
git diff "$DEFAULT_BRANCH"...HEAD --stat
```

Structure the body as:

```markdown
## Summary
- [2-3 bullet points describing what changed and why]

## Changes
- [list of key files/areas modified]

## Test plan
- [ ] [How this was tested]
- [ ] [What to verify during review]

## Related issues
[Auto-detected from commit messages: "fixes #42", "closes #13", etc.]
```

---

## Phase 4 — Create PR

```bash
gh pr create \
  --title "<generated title>" \
  --body "<generated body>" \
  --base "$DEFAULT_BRANCH" \
  --head "$CURRENT_BRANCH"
```

Capture the PR number and URL from the output.

---

## Phase 5 — AI Review

Review the full diff and post findings as a PR comment.

### What to review

```bash
# Get the full diff for review
git diff "$DEFAULT_BRANCH"...HEAD
```

Analyze the diff across 5 categories:

**1. Code quality**
- Clean code, naming, complexity, DRY, dead code
- Large functions or files that need splitting
- Unclear logic that needs comments

**2. Spec compliance**
- Does the code match the PR title and description
- Are all claimed changes actually present
- Are there unclaimed changes (scope creep)

**3. Security**
- SQL injection, XSS, command injection risks
- Hardcoded secrets, API keys, tokens
- Authentication and authorization gaps
- OWASP top 10 patterns

**4. Test coverage**
- Are new code paths tested
- Are edge cases covered
- Test quality — do tests verify behavior or just mock everything

**5. Breaking changes**
- API signature changes (added/removed/changed parameters)
- Database schema changes
- Configuration changes
- Removed or renamed exports
- Changed default behavior

### Post review as PR comment

```bash
gh pr comment <PR_NUMBER> --body "<review content>"
```

**Review format:**

```markdown
## 100x Dev — AI Review

### Summary
[1-2 sentence overall assessment]

### Findings

#### Critical (must fix before merge)
- [file:line] [description]

#### Important (should fix)
- [file:line] [description]

#### Minor (consider fixing)
- [file:line] [description]

### Checklist
- [ ] All critical findings addressed
- [ ] Tests cover new code paths
- [ ] No secrets in diff
- [ ] No breaking changes (or documented)

### Verdict: ✅ APPROVE / ⚠️ CHANGES REQUESTED / ❌ BLOCK
```

If no issues found:

```markdown
## 100x Dev — AI Review

### Summary
Clean implementation. No issues found.

### Verdict: ✅ APPROVE
```

---

## Phase 6 — Stop (Human-in-the-Loop)

**DO NOT MERGE.** Print the PR summary and stop.

```
╔══════════════════════════════════════════════════════╗
║               PULL REQUEST CREATED                    ║
╠══════════════════════════════════════════════════════╣
║ PR:       #<number> — <title>                        ║
║ Branch:   <branch> → <default_branch>                ║
║ Review:   AI review posted ✅                         ║
║ Gate:     ✅ All gates passed                         ║
╠══════════════════════════════════════════════════════╣
║ URL:      <pr_url>                                   ║
║ STATUS:   Awaiting human approval. DO NOT auto-merge. ║
╚══════════════════════════════════════════════════════╝
```

Merge is the human's responsibility. This workflow ensures everything is ready for review.
