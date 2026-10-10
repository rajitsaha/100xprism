---
name: docs
description: Sync documentation with what shipped: refresh READMEs, guides and API references, and prune stale mentions. Use once a feature lands or when asked to refresh docs.
category: docs
tier: on-demand
slash_command: /docs
---

# Docs — Documentation Sync

You are a documentation engineer. Detect code changes, update corresponding docs, remove stale references, validate quality.

## Do NOT ask for permission — just update docs.
## This workflow does NOT run tests or lint. Those are separate workflows (test, lint).

---

## Step 1 — Detect what changed

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT"
git diff --name-only HEAD 2>/dev/null || true
git diff --name-only --cached 2>/dev/null || true
git ls-files --others --exclude-standard 2>/dev/null || true
```

If no code changes, skip to Step 4.

---

## Step 2 — Map changes to docs

Read the project instruction file (CLAUDE.md, AGENTS.md, .cursorrules, or equivalent) to understand which doc files exist and their purposes. Apply the general mapping:

| Changed files | Doc to update |
|---|---|
| API routes or handlers | API reference doc |
| CLI commands or flags | CLI reference doc |
| Config schema or models | Configuration reference |
| New features or major changes | `README.md` |
| Removed files or features | Remove stale references everywhere |
| Infrastructure or deployment | Architecture or infra doc |
| `.claude/commands/**` | No doc update needed |

For each affected doc: read the current doc → read the changed source → update.

---

## Step 3 — Update documentation

### Standards
- GitHub-flavored Markdown
- Code blocks with language tags (` ```bash `, ` ```typescript `, ` ```python `)
- Every API route: description, request/response, auth requirement
- Every CLI command: usage, arguments, flags, examples
- Direct voice, second-person ("you"), imperative for instructions
- No emojis unless already present in the file
- Tables for structured data

### Stale reference removal
Search all doc files for references to deleted/renamed functions, routes, components, env vars. Remove or update any stale references found.

---

## Step 4 — Validate

```bash
# Link check — verify internal file references
grep -rn '\[.*\](\.\.' docs/ README.md CLAUDE.md AGENTS.md .cursorrules 2>/dev/null | head -20 || true

# Unclosed code blocks
for f in docs/*.md README.md CLAUDE.md AGENTS.md .cursorrules ARCHITECTURE.md 2>/dev/null; do
  [ -f "$f" ] || continue
  count=$(grep -c '```' "$f" 2>/dev/null || echo 0)
  if [ $((count % 2)) -ne 0 ]; then echo "WARNING: $f has unclosed code block"; fi
done
```

---

## Step 5 — Stage doc files only

```bash
git add docs/ README.md CLAUDE.md AGENTS.md .cursorrules ROADMAP.md ARCHITECTURE.md 2>/dev/null
git diff --cached --stat
```

---

## Output

```
=== Docs Summary ===
Changed code:  [list of changed source files]
Docs updated:  [list of doc files modified]
Stale removed: [list of removed references]
Validation:    Links OK, code blocks OK
Status:        PASSED | NO CHANGES NEEDED
```
