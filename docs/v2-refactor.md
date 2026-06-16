# 100x-dev v2 — Unified Modules

Refactor plan: collapse `workflows/` + `skills/` into a single cross-tool `modules/` directory, eliminate 8 duplicate pairs, and minimize token cost in every supported tool.

> Current note: the original v2 plan treated Codex as a single-file target. The current Codex adapter emits a compact `AGENTS.md`, repo-scoped skills in `.agents/skills/`, and hooks in `.codex/hooks.json` so Codex can use progressive skill loading.

---

## Goals

1. **Cross-tool parity** — every module works in Claude Code, Cursor, Codex, Windsurf, Antigravity, Copilot, Gemini.
2. **One source of truth** — no more workflow ↔ skill drift (currently 8 duplicate pairs).
3. **Token-efficient by default** — modules load on-demand wherever the tool supports it; concatenated rules files stay lean.

---

## Module format

Single canonical file: `modules/<name>/SKILL.md`

```yaml
---
name: spec
description: Use when a feature request is vague or complex — turn it into an implementation-ready spec with acceptance criteria, edge cases, API contracts.
category: engineering            # engineering | quality | data | docs | design | marketing | growth
tier: core                       # core (always-loaded in single-file tools) | on-demand (indexed only)
slash_command: /spec             # optional; omit for skills with no slash entry point
allowed-tools: Read Grep Glob    # Claude Code only; other adapters ignore
---
<body — terse, no narrative fluff>
```

**Body discipline (token cost):**
- No "Welcome", "Let's", or filler. Imperative steps only.
- Bullet > prose. Code blocks > screenshots-of-code.
- Each module body should target **< 1500 tokens** (~6KB markdown). Long modules split into a "core" SKILL.md + a `references/<topic>.md` that the body only references — Claude Code follows the link on demand.
- Examples live in `modules/<name>/examples/` not in the body.

---

## Per-tool generation

| Tool | Output | Tier behavior | Token cost |
|---|---|---|---|
| **Claude Code** | `~/.claude/skills/<name>/SKILL.md` (auto-trigger) + `~/.claude/commands/<name>.md` (slash alias `Use the <name> skill.`) | All modules; body loads only when triggered | ~0 baseline; ~description-len at every prompt |
| **Cursor** | `.cursor/rules/<name>.mdc` (one file per module, `alwaysApply: false`) | All modules; body loads only when description/globs match | ~0 baseline |
| **Codex** | `AGENTS.md` + `.agents/skills/<name>/SKILL.md` + `.codex/hooks.json` | Compact command map in `AGENTS.md`; full modules load as repo skills | ≤ default `AGENTS.md` budget; full bodies on demand |
| **Windsurf** | `.windsurfrules` (concatenated) | **`tier: core` only** + one-line index | ≤ ~6K chars (Windsurf hard limit) |
| **Antigravity** | `ANTIGRAVITY.md` | **`tier: core` only** + index | ≤ ~10K tokens |
| **Copilot** | `.github/copilot-instructions.md` | **`tier: core` only** + index | ≤ ~10K tokens |
| **Gemini** | `GEMINI.md` | **`tier: core` only** + index | ≤ ~10K tokens |

**Why tiered:** Claude Code, Cursor, and Codex support per-skill/rule loading — full module bodies cost little until used. The remaining single-file tools concatenate instructions into one file that loads every session — dumping every module there would burn tokens for no gain. Tiering keeps single-file tools lean while still giving them an **index** so the agent can ask the user to invoke the right module by name.

**Index format** (for single-file tools):
```markdown
## Available on-demand modules

Engineering: orchestrate, spec (use when vague request), grill (adversarial review),
techdebt, fix-bugs, subagents, data-query, context-dump, update-claude-md,
terminal-setup.

Marketing & Growth: copywriting, seo-audit, ai-seo, page-cro, paid-ads,
email-sequence, … [37 total]

To invoke: tell Claude "use the <name> module" or paste the trigger context.
```

That's ~10 lines to index 50+ modules vs. ~30K tokens to inline them.

---

## Tier classification (initial)

**`tier: core`** (always-loaded; ~25 modules, the actual dev lifecycle):
- Quality gates: gate, lint, security, cloud-security, test
- Lifecycle: commit, push, pr, branch, release
- Cloud & data: connect, db, query, launch
- Docs & discovery: docs, issue, context, architect, update-claude-md
- Engineering helpers: spec, grill-me, fix-bugs, orchestrate, techdebt
- Design: enterprise-design

**`tier: on-demand`** (indexed only in single-file tools; ~47 modules):
- All 36 marketing/growth skills
- Engineering helpers that don't need always-on context: subagents, data-query, context-dump, terminal-setup, marketing-ideas

---

## Dedupe — 8 workflow ↔ skill pairs

Resolution: **merge body into one canonical SKILL.md, keep slash command via `slash_command` field.**

| Old workflow | Old skill | New module |
|---|---|---|
| `/orchestrate` | orchestrate | `modules/orchestrate/SKILL.md` (slash_command: `/orchestrate`, tier: core) |
| `/spec` | spec | `modules/spec/SKILL.md` (slash_command: `/spec`, tier: core) |
| `/techdebt` | techdebt | `modules/techdebt/SKILL.md` (slash_command: `/techdebt`, tier: core) |
| `/fix` | fix-bugs | `modules/fix-bugs/SKILL.md` (slash_command: `/fix`, tier: core) |
| `/grill` | grill-me | `modules/grill-me/SKILL.md` (slash_command: `/grill`, tier: core) |
| `/update-claude` | update-claude-md | `modules/update-claude-md/SKILL.md` (slash_command: `/update-claude`, tier: core) |
| `/context` | context-dump | `modules/context-dump/SKILL.md` (slash_command: `/context`, tier: on-demand) |
| `/query` | data-query | `modules/data-query/SKILL.md` (slash_command: `/query`, tier: core) |

For each pair: pick the more concise body, merge any unique content, drop the other. The slash command name stays so users' muscle memory doesn't break.

`/db` is **kept separate** from `/query` — they do different things.

---

## Adapter rewrite

`adapters/lib/shared.sh` becomes:

```bash
# Reads modules/ once, dispatches per tool.
# Caches parsed frontmatter so all 7 adapters share one parse pass.

emit_claude_code() { ... }      # writes ~/.claude/skills/* + ~/.claude/commands/*
emit_cursor() { ... }           # writes .cursor/rules/*.mdc
emit_concat() {                 # used by codex/windsurf/antigravity/copilot/gemini
  local out_file=$1 max_chars=$2
  emit_core_modules >> "$out_file"
  emit_index_of_on_demand >> "$out_file"
  enforce_size_limit "$out_file" "$max_chars"
}
```

A single Python helper parses frontmatter once into JSON, shell adapters consume that. Faster, simpler, no double-parse.

---

## Migration steps

1. Create `modules/` directory.
2. **For each of the 25 workflows:** convert to `modules/<slug>/SKILL.md` with frontmatter. Slug = workflow name (or skill name if a duplicate exists).
3. **For each of the 47 skills:** if it duplicates a workflow, merge during step 2; otherwise copy `skills/<name>/SKILL.md` → `modules/<name>/SKILL.md` and add `tier`/`slash_command` fields.
4. Audit each module body for token cost — split bodies > 1500 tokens into `references/`.
5. Rewrite `adapters/lib/shared.sh` and individual adapter scripts.
6. Update `install.sh` / `update.sh` to call new emit logic.
7. Delete `workflows/` and `skills/` (or symlink for one release).
8. Update `README.md`, postcard, install banners.
9. Bump VERSION to `2.0.0` (breaking structural change).
10. Add `CHANGELOG.md` entry with migration note for users with custom workflows.

---

## Postcard implications

Current postcard claims "10 plugins · 25 workflows · 47 skills". Post-refactor it becomes:

> **10 plugins · 64 modules** (25 with slash commands, 47 with auto-trigger descriptions; 8 collapsed dupes)

Or simpler:
> **10 plugins · 64 modules** (cross-tool — works in Claude Code, Cursor, Codex, Windsurf, Antigravity)

---

## Decisions baked in (override before execution if needed)

1. **Directory name**: `modules/` (covers both old workflows + skills semantically).
2. **Slash command coverage**: Only modules that previously had a `/x` command get a `slash_command` field. New skills don't auto-get slashes.
3. **Windsurf strategy**: Tier-based — `core` modules + index of `on-demand` ones. Stays under the size limit.
4. **Token budget per module body**: 1500 tokens. Split via `references/` if over.
5. **Module slug** = old workflow name when one exists (keeps muscle memory); else old skill name.

---

## Open questions

- Do we ship a one-time migration command (`100x-dev migrate-v2`) for users with existing installs?
- Postcard regeneration — manual or automate from `modules/` metadata?
- Is `tier: on-demand` the right name, or `tier: skill` vs `tier: workflow`?
