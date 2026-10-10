# AGENTS.md

Contributor contract for AI coding agents working on this repo. Users see `README.md` and `docs/USAGE.md`; this file is for agents editing the repo itself.

## What this repo is

100xprism is a **distributor**, not an app. The product is `modules/` (68 SKILL.md files with YAML frontmatter). Adapters in `adapters/` render those modules into each AI tool's native format: Claude Code skills, Cursor `.mdc`, Codex repo skills + `AGENTS.md`, a **Pi package** (retention-filtered `.pi/skills/` + extensions), and Hermes/OpenClaw global skills. Users install via `npm i -g 100xprism` or `get.sh`. For Pi: `pi install git:github.com/rajitsaha/100xprism`.

Every adapter uses progressive disclosure — descriptions in the always-on index, bodies loaded on demand. The single-file concat adapters (Windsurf/Copilot/Gemini/Antigravity) were removed in v3.0.0 precisely because they could not do this.

## The golden rule

**A module is the source of truth. Adapter output is generated.** Edit `modules/<slug>/SKILL.md`. Never hand-edit `~/.claude/skills/*`, `.cursor/rules/*.mdc`, `.pi/skills/*`, or any rendered artifact in a consumer project — those are reset by the next adapter run.

## Module shape

Each module is one file: `modules/<slug>/SKILL.md` with frontmatter:

```yaml
---
name: <slug>
description: <one-line trigger guidance — used by Claude Code/Cursor for auto-activation>
category: <docs|code|growth|...>
tier: <core|on-demand>
slash_command: /<name>   # optional — only for the 28 command-style modules
retention: <must|profile|resolver>   # optional — overrides the derived class
profiles: <core, code, data, …>      # optional — overrides the category default
---
```

A module must work across **all 5 adapters**. If you add tool-specific instructions, gate them inside the module body, not the frontmatter. Hermes has one extra constraint the others don't: its always-on index truncates `description:` past ~57 chars, so `adapters/lib/modules.py`'s `hermes_description()` re-derives a short one for that adapter only (override per-module with `hermes_description:` in frontmatter if the derived cut reads awkwardly) — the source module's `description:` is unaffected.

## Retention: what earns a permanent slot

A module's **description** is re-sent on every turn once it is installed; its **body**
is free until invoked. So installing a module has a standing cost, and retention
decides which modules are worth it. Derived in `retention_of()`, overridable per module:

| Class | Meaning | Derivation |
|---|---|---|
| `must` | Deterministic machinery or house policy a model won't reproduce unprompted — what `gate` runs, the order `release` runs it in, the protocol `pair-loop` speaks. | the 12 slugs in `MUST_HAVE` |
| `profile` | Earns its place only in repos of a matching kind. | default; also **any module owning a slash command** — the user can type it, so it must resolve |
| `resolver` | General expertise a capable model already has. Never installed; one row in the generated catalog, loaded by path on demand. | `marketing` / `design` without a slash command |

Two switches control how much of that is applied. Fresh emits default to the
`must` set plus one resolver; wider profile and `all` modes require explicit opt-in:

- **user scope** — `~/.100xprism/config.json` `"skills": all|profile|must` (or `PRISM_SKILLS`)
- **per project** — `<project>/.100xprism.json` `"profiles": [...]` (or `PRISM_PROFILES`); `["all"]` opts back out

`100xprism optimize` writes both (`100xprism slim` remains a compatibility alias).
Keep the must-only default and reversible widening when you touch the emitters.

Modules routed out of the index are copied to a catalog directory *outside* whatever
the tool indexes (`~/.100xprism/100xprism-catalog/`, `.cursor/100xprism-catalog/`) and
listed in a generated `100x-resolver` artifact. Codex follows the same retention
policy for parity even though `.agents/skills` is loaded on demand.

## After editing a module

Run the Claude Code adapter as a smoke test — it surfaces frontmatter errors and prints module counts:

```bash
./adapters/claude-code.sh
```

Expected output reports `13 skills` plus the `/100x` route and `56 catalog module(s)`
in the default must mode (or whatever the current totals are — an alias is written only
when the command name differs from the slug, e.g.
`fix-bugs` → `/fix`; a same-name alias would double-list the module and pay its
description twice). If the skill count drops unexpectedly, you broke a frontmatter parse.

The Hermes adapter is a useful second smoke test specifically for description-budget
issues, since it is the only adapter that truncates:

```bash
HOME=$(mktemp -d) ./adapters/hermes.sh
```

To smoke-test the routed index instead, set the mode explicitly:

```bash
HOME=$(mktemp -d) PRISM_SKILLS=profile ./adapters/claude-code.sh
```

For the full repo check, run:

```bash
npm run check
```

## Things that are easy to get wrong

- **Don't add a `CLAUDE.md` to this repo.** This file (`AGENTS.md`) covers all tools. The `CLAUDE.md` template that ships to *consumer* projects lives under `templates/`, not at the root.
- **The consumer `CLAUDE.md` scaffold is duplicated.** It lives in both `adapters/claude-code.sh` (`install_project`) and `lib/adapters/windows.js` (`scaffoldClaudeMd`). Change one, change the other — `test/windows-adapters.test.js` only guards the JS copy.
- **Keep `retention_of` / `profiles_of` / `detect_profiles` in sync** between `adapters/lib/modules.py` and `lib/adapters/windows.js`. A parity test in `test/retention-profiles.test.js` compares both across every real module, so drift fails CI rather than shipping.
- **Machine-readable project config belongs in `.claude/100xprism.yml`, not `CLAUDE.md`** — the instruction file is re-sent every turn. Modules read the yml first and fall back to the instruction file, so old repos keep working. Its keys must stay flush-left: `/db` and friends match them with an anchored `grep`.
- **Cleanup requires proof of ownership.** Anything install/update/uninstall deletes must carry a
  generated marker, a manifest entry, a `/100xprism/hooks/` path, or match
  `adapters/lib/legacy-v1-commands.json`. JS cleanup lives in `lib/cleanup.js` and mirrors the
  Python markers; `test/cleanup-contract.test.js` guards both. If you add a new artifact, teach
  `lib/cleanup.js` to remove it and extend that test.
- **Don't bump the version manually.** Use `/release` or follow `docs/USAGE.md` — `package.json`, `VERSION`, and the git tag must move together.
- **Don't commit `.DS_Store` or `.playwright-mcp/`** (already in `.gitignore`, but worth knowing).
- **Marketing assets in `assets/`** are generated from the HTML files in the same dir via Playwright. If you change the HTML, regenerate the PNG.

## Where to look

- `docs/USAGE.md` — user-facing usage (install, init, per-tool behavior)
- `docs/v2-refactor.md` — why `modules/` replaced the old `workflows/` + `skills/` split
- `adapters/lib/modules.py` — the parser; if frontmatter changes, this is the file to update
