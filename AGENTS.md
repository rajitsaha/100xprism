# AGENTS.md

Contributor contract for AI coding agents working on this repo. Users see `README.md` and `docs/USAGE.md`; this file is for agents editing the repo itself.

## What this repo is

100xprism is a **distributor**, not an app. The product is `modules/` (66 SKILL.md files with YAML frontmatter). Adapters in `adapters/` render those modules into each AI tool's native format (Claude Code skills, Cursor `.mdc`, Codex repo skills + `AGENTS.md`, and Windsurf/Copilot/Gemini/Antigravity single-file configs). Users install via `npm i -g 100xprism` or `get.sh`.

## The golden rule

**A module is the source of truth. Adapter output is generated.** Edit `modules/<slug>/SKILL.md`. Never hand-edit `~/.claude/skills/*`, `.cursor/rules/*.mdc`, or any rendered artifact in a consumer project — those are reset by the next adapter run.

## Module shape

Each module is one file: `modules/<slug>/SKILL.md` with frontmatter:

```yaml
---
name: <slug>
description: <one-line trigger guidance — used by Claude Code/Cursor for auto-activation>
category: <docs|code|growth|...>
tier: <core|on-demand>
slash_command: /<name>   # optional — only for the 26 command-style modules
---
```

A module must work across **all 8 adapters**. If you add tool-specific instructions, gate them inside the module body, not the frontmatter.

## After editing a module

Run the Claude Code adapter as a smoke test — it surfaces frontmatter errors and prints module counts:

```bash
./adapters/claude-code.sh
```

Expected output ends with `68 skills + 26 slash command aliases` (or whatever the current totals are). If the count drops unexpectedly, you broke a frontmatter parse.

For the full repo check, run:

```bash
npm run check
```

## Things that are easy to get wrong

- **Don't add a `CLAUDE.md` to this repo.** This file (`AGENTS.md`) covers all tools. The `CLAUDE.md` template that ships to *consumer* projects lives under `templates/`, not at the root.
- **Don't bump the version manually.** Use `/release` or follow `docs/USAGE.md` — `package.json`, `VERSION`, and the git tag must move together.
- **Don't commit `.DS_Store` or `.playwright-mcp/`** (already in `.gitignore`, but worth knowing).
- **Marketing assets in `assets/`** are generated from the HTML files in the same dir via Playwright. If you change the HTML, regenerate the PNG.

## Where to look

- `docs/USAGE.md` — user-facing usage (install, init, per-tool behavior)
- `docs/v2-refactor.md` — why `modules/` replaced the old `workflows/` + `skills/` split
- `adapters/lib/modules.py` — the parser; if frontmatter changes, this is the file to update
