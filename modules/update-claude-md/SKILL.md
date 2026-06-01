---
name: update-claude-md
description: After any correction from the user, update CLAUDE.md with a rule to prevent the same mistake from recurring. Use when the user corrects your behavior or says "don't do that again". Ruthlessly iterate CLAUDE.md until mistake rate measurably drops.
category: engineering
tier: core
slash_command: /update-claude
allowed-tools: Read Edit Write
model: haiku
---

# Update-Claude — CLAUDE.md Rule Writer

After any correction, update CLAUDE.md with a rule to prevent the same mistake recurring.

## How to use
- `/update-claude` — after a correction, write a rule to CLAUDE.md

---

## Phase 1 — Identify the pattern

What went wrong? What should have been done instead? Be specific — one clear pattern, not a vague note.

---

## Phase 2 — Write a rule

Formulate as a clear, actionable rule:
```
- [Rule]: [What to do / not to do]. Why: [brief context].
```

Short rules stick. Long paragraphs get ignored.

---

## Phase 3 — Update CLAUDE.md

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
INSTRUCTION_FILE=$(for f in CLAUDE.md AGENTS.md .cursorrules .windsurfrules .github/copilot-instructions.md GEMINI.md; do [ -f "$PROJECT_ROOT/$f" ] && echo "$PROJECT_ROOT/$f" && break; done)
```

Append the rule under a `## Rules` or `## Corrections` section in the project's instruction file. If no instruction file exists, create `CLAUDE.md` in the project root.

---

## Phase 4 — Confirm

Tell the user: "Updated [file] with: [rule summary]"

If the rule isn't reducing mistakes after 2–3 sessions: rewrite it — be more specific or more prominent.
