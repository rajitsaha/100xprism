# Changelog

All notable changes to 100x-dev are recorded here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

---

## [Unreleased]

Correctness & drift hardening (issue #23) — no breaking changes.

### Fixed
- **`package.json`** — version bumped `2.0.1 → 2.0.4` to match `VERSION`/git tag. The npm-published version had been 3 releases stale.
- **`plugins/plugins.json`** — added `understand-anything@understand-anything` and `ui-ux-pro-max@ui-ux-pro-max-skill` to `plugins[]` (previously only in `extraKnownMarketplaces`), so fresh installs enable all 12 plugins instead of 10.
- **`update.sh`** — `run_plugin_updates()` now reads the plugin list from `plugins.json` (single source of truth) using the fully-qualified `name@marketplace` ids. The old hardcoded bare-name list silently failed for **every** plugin — `claude plugin update <bare-name>` errors with "not found"; only the qualified form works. Failures are now printed by name instead of bucketed as "skipped".
- **`modules/gate/SKILL.md`, `modules/pr/SKILL.md`** — reconciled gate-count wording ("four"/"5 gates" → "all gates", noting Gates 4–5 are conditional).

### Added
- **`scripts/meta-check.py`** — repo consistency checker: parses all module frontmatter, asserts README counts (modules/slash/skills/plugins) match the repo, validates every `evals.json`, and asserts the `VERSION == package.json == tag` version triple.
- **`.github/workflows/meta.yml`** — repo self-CI (distinct from the `github-actions/ci.yml` template): runs `meta-check.py` and the generator/adapter test suite on every PR and push to main.
- **`.github/workflows/release.yml`** — added a pre-release version-triple guard that fails the release if `VERSION`, `package.json`, and the tag disagree.
- **`test/meta-check.test.js`** — tests for the drift gate.

Enforcing hooks (issue #22) — opt-in; no behavior change unless enabled.

### Added
- **`hooks/`** — first-party hook pack (plain `python3`, no deps): `pretooluse-gate.py` blocks `git commit`/`git push` unless `/gate` recorded a pass for the current tree; `pretooluse-secret-scan.py` blocks writes containing obvious credentials; `posttooluse-lint.py` (advisory lint-on-save) and `permission-router.py` (auto-approve read-only Bash, optional model escalation) ship off by default. `gate-pass.py` records the pass; `hooks/hooks.manifest.json` is the single source of truth.
- **`adapters/lib/modules.py emit-hooks [--sync]`** — idempotent, declarative merge of the hook pack into `~/.claude/settings.json` (same pattern as the plugin merge); per-hook toggles via `HOOK_GATE`/`HOOK_SECRET`/`HOOK_LINT`/`HOOK_ROUTER`. `--sync` only refreshes hooks already present.
- **`install.sh`** — new opt-in *Hooks* component with a per-hook selection menu; **`update.sh`** refreshes installed hooks on update.
- **`test/hooks.test.js`** — covers secret-scan, the gate→commit contract, the router, and idempotent merges.

### Changed
- **Gate cache is now keyed on a tree token** (HEAD + tracked diff + untracked status) instead of a bare HEAD, closing the stale-cache-for-a-dirty-tree / post-rebase hole. `modules/gate/SKILL.md` records the pass via `gate-pass.py`; `modules/commit/SKILL.md` Phase 0 checks the same token.
- **`modules/subagents/SKILL.md`** — the permission-routing section now references the real `hooks/permission-router.py` artifact (was prose), fixes the stale `code.claude.com/docs` URL, and marks `ctrl+b` as Claude-Code-specific.

Eval harness (issue #24) — wires up the 32 dormant `evals.json`.

### Added
- **`scripts/eval-harness.py`** — discovers, validates, and inventories the per-module `evals.json`; emits the case/assertion work-list the `/eval` skill grades; renders a per-assertion pass/fail scorecard from graded results (`validate` / `plan` / `score`, with `--module` / `--all` / `--changed` selectors). No model calls — runs anywhere.
- **`scripts/trigger-overlap.py` + `scripts/trigger-overlap-allow.txt`** — deterministic trigger-overlap lint (overlap-coefficient over description trigger tokens); flags the known overlapping pairs (CRO family, conversion-copy⇄copywriting, systems-architect⇄enterprise-design), `--strict` fails only on new (non-allow-listed) overlaps.
- **`modules/eval/`** — new `/eval` skill: fans eval cases out to parallel subagents and has Haiku 4.5 grade each assertion into a scorecard. (Counts: 65 modules / 26 slash commands / 39 auto-trigger skills.)
- **`.github/workflows/evals.yml`** — repo self-CI (distinct from `meta.yml` and the `github-actions/ci.yml` template): trigger-overlap `--strict` on every PR, changed-module eval validation + inventory posted to the PR, nightly full sweep.
- **`test/eval-harness.test.js`, `test/trigger-overlap.test.js`** — cover validation, planning, scoring, and overlap detection.

---

## [2.0.4] — 2026-05-15

Update UX improvement — no breaking changes.

### Changed
- **`update.sh`** — plugin updates now always run regardless of whether the repo itself has new commits. Previously, `update.sh` exited early with "Already up to date" when the local repo matched `origin/main`, silently skipping plugin updates and leaving newly listed plugins (e.g. `ui-ux-pro-max`) unenabled for existing users.
- **`update.sh`** — added `--plugins-only` flag: refreshes all Claude plugins + syncs `settings.json` without touching the repo. Useful when the repo is current but plugins may have independent updates.
- **`docs/USAGE.md`** — documented `--plugins-only` in both the Installation "Keep up to date" section and the "Keeping Workflows Updated" section.
- **`README.md`** — added `update --plugins-only` to the install quick-reference block.

### Architecture
- Plugin list extracted into `run_plugin_updates()` — defined once, called from both "already current" and "fresh pull" paths
- Settings sync (adding newly listed plugins to `enabledPlugins`) now runs on every `update.sh` invocation, not just when there are repo changes

---

## [2.0.3] — 2026-05-15

Lifecycle skill improvements — no breaking changes.

### Changed
- **`modules/commit/SKILL.md`** — added 5-dimension code review step (Phase 5): bugs, security, architecture, design, CLAUDE.md compliance. Gate and review results cached to `~/.100x-dev/gate-cache` and `~/.100x-dev/review-cache` to prevent redundant re-runs when called from `/launch`.
- **`modules/push/SKILL.md`** — added pre-push code review step (Phase 0b) with cache-aware skip logic. Gate cache check added to Phase 0. Review dimensions reference `commit` Phase 5 — no duplication.
- **`modules/launch/SKILL.md`** — writes gate cache after Phase 4 so downstream `/commit` and `/push` skip redundant gate runs. Removed duplicated Phase 5b review (commit already handles it). Eliminated 2× redundant gate + 1× redundant review per launch.

### Architecture
- Review checklist (5 dimensions) is defined once in `commit/SKILL.md` — push and launch reference it
- Gate cache: `~/.100x-dev/gate-cache` — stores last-passing HEAD SHA
- Review cache: `~/.100x-dev/review-cache` — stores last-reviewed HEAD SHA
- Both caches invalidate automatically on any new commit

---

## [2.0.2] — 2026-05-15

Plugin expansion — no breaking changes.

### Added
- **`understand-anything` plugin** (lum1104/Understand-Anything) — AI-powered codebase understanding: knowledge graphs, architecture layers, domain analysis, onboarding tours, and interactive dashboard. Positioned as `/understand` step before `/context` in the pipeline.
- **`ui-ux-pro-max` plugin** (nextlevelbuilder/ui-ux-pro-max-skill) — UI/UX design intelligence: 67 styles, 161 palettes, 57 font pairings, 25 chart types, 15 stacks (React, Next.js, Vue, Svelte, Tailwind, shadcn/ui, Flutter, SwiftUI, and more).
- Both marketplace sources added to `plugins/plugins.json` so fresh installs register them automatically.

### Changed
- `update.sh` — added plugin update loop covering all 12 plugins; runs `claude plugin update` for each on every `update.sh` execution.
- Pipeline in README updated: `/understand → /context → /issue → /spec → /fix → /commit → …`
- Plugin count updated: 10 → 12 across README and banner.
- `assets/postcard-stack.png` — re-rendered banner with new plugins tagged `v2.0.2`.

---

## [2.0.1] — 2026-05-02

Documentation patch — no code changes.

### Added
- `AGENTS.md` — contributor contract for AI agents working in this repo
- `SECURITY.md` — security policy and reporting guidelines
- README activation instructions for 100x-dev
- Custom install location guide in `docs/USAGE.md`
- v2.0.0 release banner (light theme social card) in `assets/`

### Changed
- `.gitignore` — exclude `.DS_Store` and `.playwright-mcp/`

---

## [2.0.0] — 2026-04-30

**Breaking — `workflows/` and `skills/` collapsed into a single `modules/` directory.**

### Added
- **`modules/`** — single source of truth (64 modules) replacing `workflows/` (25) + `skills/` (47, with 8 duplicate pairs deduped)
- **Module frontmatter** — each `modules/<slug>/SKILL.md` declares `category`, `tier` (`core` or `on-demand`), and optional `slash_command` so adapters can dispatch consistently
- **Cross-tool parity** — every module now works in Claude Code, Cursor, Codex, Windsurf, Antigravity, Copilot, and Gemini. Previously skills were Claude Code-only.
- **`adapters/lib/modules.py`** — single Python emitter all adapter scripts call into; replaces the per-adapter shell concat logic
- **Cursor multi-file rules** — `.cursor/rules/<slug>.mdc` per module with `alwaysApply: false`, so Cursor auto-triggers each module by description (zero baseline token cost)
- **Token-tiered concat output** — Codex / Antigravity / Copilot / Gemini get full bodies for `tier: core` modules + a one-line index for `tier: on-demand` modules; Windsurf gets index-only mode to fit its rules budget
- **Slash command aliases** — modules with `slash_command` get a 2-line file in `~/.claude/commands/<name>.md` so `/spec`, `/grill`, `/fix`, etc. still work in Claude Code despite content living in skills
- **`docs/v2-refactor.md`** — architecture and rationale for the v2 layout

### Changed
- `install.sh` — single `Modules` toggle replaces the previous `Workflows` + `Skills` toggles
- `update.sh` — single module-sync path replaces separate workflows/skills syncs; backs up `~/.claude/commands/` before overwrite
- All `adapters/*.sh` are now thin wrappers around `adapters/lib/modules.py`
- `README.md` — describes the unified module model and per-tool generation
- `package.json` files manifest now lists `modules/` instead of `workflows/`

### Removed
- `workflows/` directory — content migrated into `modules/`
- `skills/` directory — content migrated into `modules/`

### Migration notes
- Users on v1.x: run `100x-update` (or `~/100x-dev/update.sh`). Existing `~/.claude/commands/*.md` files are backed up to `~/.claude/commands.bak.<timestamp>` before the new module-based install overwrites them.
- Project-level rules files (`.cursorrules`, `AGENTS.md`, etc.) auto-regenerate via the tracked-projects mechanism in `update.sh`.
- The 8 duplicate workflow ↔ skill pairs (`/spec` ↔ `spec`, `/grill` ↔ `grill-me`, `/fix` ↔ `fix-bugs`, `/orchestrate` ↔ `orchestrate`, `/techdebt` ↔ `techdebt`, `/update-claude` ↔ `update-claude-md`, `/context` ↔ `context-dump`, `/query` ↔ `data-query`) are now single modules. Slash commands and skill descriptions both still trigger them.

---

## [1.6.0] — 2026-04-26

### Added
- **`docs/ci-traps.md`** — new reference guide documenting three CI failures that consistently surface when AI tools generate pipelines: npm 404 from unpublished packages, Playwright `toBeVisible()` timeouts caused by `opacity-0` React animations, and integration tests silently excluded from the gate
- **`Common CI Traps` section in README** — quick-reference callout linking to the full guide
- **`Common CI Traps` section in all project templates** — `node-frontend.md`, `node-fullstack.md`, `python-api.md` each include the relevant traps for their stack
- **`Common CI Traps` section in `docs/USAGE.md`** — full code examples and fixes under the GitHub Actions Templates section
- **`# TRAP:` inline comments in `github-actions/ci.yml`** — three comments at the exact lines where each pitfall occurs (npm ci step, pytest step, E2E step)

### Fixed
- `github-actions/ci.yml` — the `unit-tests` job now includes a reminder comment that `tests/integration/` must be included alongside `tests/unit/`

---

## [1.5.0] — 2026-04-25

### Added
- `/connect` workflow — install, authenticate, and test 27 SaaS CLI tools in one command; reads credentials from `.env`; no MCP required
- `.env.example` — credential stubs for all 27 services with inline token-creation links, grouped by category (cloud, deployment, database, payments, DevOps, comms, registries)
- `templates/.env.example` — same template bundled into project scaffolds
- `workflows/_lib.md` — SaaS credentials reference section so other workflows can point users to `/connect`
- Services covered: GitHub, AWS, GCP, Azure, Vercel, Netlify, Railway, Heroku, Fly.io, Supabase, PlanetScale, Firebase, Stripe, Cloudflare, Docker, Terraform, Sentry, Datadog, Jira, Linear, Slack, Notion, npm, PyPI, DigitalOcean, Render, MongoDB Atlas

---

## [1.4.1] — 2026-04-21

### Fixed
- `shell/aliases.sh` — add `100x-dev` alias so the command works after `curl | bash` install (not just npm global install)
- `install.sh` — separate post-install output into "In Claude Code" vs "In your terminal" sections; add shell reload reminder

---

## [1.4.0] — 2026-04-21

### Added
- `get.sh` — idempotent curl|bash bootstrap: clones on first run, pulls on subsequent runs
- `100x-dev` CLI — `install`, `init`, `update`, `check` subcommands (cross-platform)
- `install-project.sh` — per-project setup extracted from install.sh; called by `100x-dev init`
- `lib/adapters/windows.js` — full Windows support: copies workflows, scaffolds CLAUDE.md, merges plugins.json, generates per-tool instruction files
- `bin/100x-dev.js` — CLI entry point dispatching to bash scripts (Mac/Linux) or JS adapters (Windows)
- `package.json` + `.npmignore` — published as `100x-dev` on npm registry
- 16 tests covering `lib/platform.js` and `lib/adapters/windows.js`

### Changed
- `install.sh` — Phase 1 (global) only; no longer prompts for project path
- README — new install block: `curl | bash` (Mac/Linux) + `npm install -g 100x-dev` (Windows)
- `docs/USAGE.md` — updated all install/init/update instructions to use new CLI commands

---

## [1.3.1] — 2026-04-20

### Fixed
- changelog.sh awk multiline variable — use temp file instead of -v flag
- changelog.sh --release now writes VERSION file automatically
- scaffold CLAUDE.md in user project during Claude Code install (closes #11)

### Changed
- docs: rewrite USAGE.md and e2e-patterns.md — concise, correct, 24 workflows
- chore: remove docs/superpowers/ — internal planning docs, not user-facing
- chore: remove ROADMAP.md — stale, GitHub Issues is source of truth
- docs: fix CHANGELOG order — newest first (1.3.0 → 1.2.0 → 1.1.0 → 1.0.0)
- docs: rewrite README — 300 lines → 130, install command visible in 10s
---

## [1.3.0] — 2026-04-20

### Added
- `/fix` — autonomous bug fixer (CI, docker logs, Slack, or description)
- `/spec` — implementation-ready spec before coding
- `/grill` — adversarial code review before `/pr`
- `/techdebt` — scan and eliminate dead/duplicated code
- `/context` — 7-day git/gh activity dump for session start
- `/query` — plain-English analytics against any database
- `/orchestrate` — plan-first methodology for complex tasks
- `/update-claude` — write CLAUDE.md rules after corrections
- `workflows/_lib.md` — shared conventions reference (excluded from adapter output)
- GitHub Actions release workflow — auto-creates GitHub Release on version tag push
- `install_project()` in Claude Code adapter — scaffolds `CLAUDE.md` with db/cloud/production/security placeholders (closes #11)

### Changed
- `enterprise-design`: replaced 24KB verbose template with lean 3KB systems-architect blueprint format (#7 #8)
- `architect`: added scope banner distinguishing advisory Q&A from full blueprint generation
- `db`: added scope banner differentiating from `/query`
- README rewritten — 300 lines → 130, install command visible within 10 seconds
- `install.sh` now prompts for project path when Claude Code is selected, consistent with all other adapters

### Performance
- cloud-security.md: ~19KB → ~12KB (compact bash replaces verbose Python parsers)
- issue.md: ~10KB → ~8KB (bullet frameworks replace enumerated sub-questions)
- test.md Phase 0: single adaptive docker block replaces 3 alternative strategies
- db-engines: ~17KB → ~5KB (router + per-engine deltas)
- enterprise-design.md: 24KB → 3KB (leaner systems-architect content)

---

## [1.2.0] — 2026-04-20

### Changed
- Removed `firecrawl@claude-plugins-official` from plugins (unused — web scraping not needed for dev workflow)
- Removed `stripe@claude-plugins-official` from plugins (unused — no Stripe integration in core workflows)
- Removed `code-review@claude-plugins-official` from plugins (superseded by `pr-review-toolkit` which provides multi-agent review)
- Plugin count: 13 → 10

### Added
- Plugin scope table in README documenting each plugin's purpose and overlap notes
- Code review pipeline diagram: `/grill` → PR → `/review-pr`
- GitHub issues #9 and #10 tracking remaining overlap remediation

### Performance
- ~3,000–5,000 tokens/session saved by removing 3 unused/redundant plugins from system prompt

---

## [1.1.0] — 2026-04-12

### Added
- Version notification system: daily update check, shell banner, Claude Code session hook, auto-regeneration of tracked projects
- Shared adapter library (`adapters/lib/shared.sh`) — all 6 non-Claude adapters now use shared `_run_generate()` function
- Banner and logo images added to assets/ and README.md header
- `docs/e2e-patterns.md` — extracted Playwright fixture, auth, and CRUD test reference patterns from test.md

### Changed
- Consolidated 47 skills → 38 (merged copy, CRO, SEO skill groups; removed 3 niche skills)
- **Token optimization** (closes #5): reduced per-invocation context overhead by ~3,500–4,500 tokens
  - Gate Phase 0 block deduplicated in commit.md, push.md, release.md (3× identical 12-line blocks → 1-line reference each)
  - `INSTRUCTION_FILE` detection loop (6 lines × 8 workflows) collapsed to a one-liner in all 8 files
  - test.md trimmed from 791 → 470 lines by extracting Phase 4c–4g E2E boilerplate to `docs/e2e-patterns.md`
  - Removed unused plugins: firecrawl, stripe, brightdata (save ~225 tokens/session from skill listing)
  - Added `<!-- model: haiku -->` hint to lint.md + security.md; `<!-- model: opus -->` to architect.md + enterprise-design.md

---

## [1.0.0] — 2026-04-11

### Added
- 16 production workflows: gate, test, commit, push, pr, branch, launch, release, lint, security, docs, issue, architect, cloud-security, enterprise-design, db
- 7 database engine adapters: PostgreSQL, Cloud SQL, Snowflake, Databricks, Athena, Presto, Oracle
- Adapters for 7 AI coding tools: Claude Code, Cursor, Codex, Windsurf, Copilot CLI, Gemini CLI, Antigravity
- 13 curated Claude Code plugins: superpowers, frontend-design, stripe, hookify, pr-review-toolkit, code-review, playwright, firecrawl, github, skill-creator, code-simplifier, security-guidance, claude-mem
- Shell aliases: cc, ccc, 100x-update, 100x-check
- GitHub Actions templates: ci.yml (lint + real-DB tests + E2E), release.yml (multi-registry publish)
- Project templates: node-fullstack, node-frontend, python-api, docker-compose
- Bun auto-detection before enabling claude-mem plugin
