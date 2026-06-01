---
name: techdebt
description: Find and eliminate technical debt — duplicated code, dead code, redundant abstractions. Use at the end of a coding session or when the codebase feels bloated. Scans, reports, and fixes issues with user confirmation.
category: engineering
tier: on-demand
slash_command: /techdebt
allowed-tools: Bash Read Grep Glob Edit
model: haiku
---

# Techdebt — Technical Debt Scanner

Scan the codebase for technical debt and eliminate it. Run at end of session or when the codebase feels bloated.

## Do NOT ask for permission — scan, report, then ask before fixing.

## How to use
- `/techdebt` — full scan + report, confirm before fixing
- `/techdebt fix` — scan and fix without confirmation

---

## Phase 1 — Scan

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT"
```

Look for:

1. **Duplicated code** — copy-pasted logic that should be a shared utility
2. **Dead code** — unused functions, variables, imports, exports, commented-out blocks
3. **Redundant abstractions** — over-engineered helpers used in only one place
4. **Inconsistent patterns** — same operation done 3+ different ways across files
5. **Stale TODOs / FIXMEs** — old comments no longer relevant

Use Grep and Glob to identify candidates. Confirm each item is truly dead/duplicated before reporting.

---

## Phase 2 — Report

```
## Tech Debt Found

### Duplicated Code
- `src/utils/formatDate.ts:12` and `src/helpers/dates.ts:45` — identical logic
  Fix: consolidate into formatDate.ts, delete dates.ts

### Dead Code
- `src/api/legacyAuth.ts` — no imports found anywhere
  Fix: delete file

### Stale TODOs
- `src/components/Modal.tsx:89` — TODO from 6 months ago, feature shipped
  Fix: remove comment
```

---

## Phase 3 — Fix (with confirmation)

For each item, ask the user to confirm unless they said "fix". After fixes:

```bash
# Run tests to verify nothing broke
npm test 2>&1 | tail -20
```

**GATE: Tests still pass after cleanup.**
