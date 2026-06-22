# Changelog

All notable changes to 100x-dev are recorded here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

---

## [Unreleased]

---

## [2.4.2] — 2026-06-22

### Added
- **`update.sh --yes` / `-y`** — apply updates without the interactive `Apply updates? (Y/n)` prompt, so `update.sh` can run unattended (cron, CI, scripts). A non-interactive run *without* `--yes` (no TTY) now safely **skips** instead of aborting silently under `set -e`. The crontab tip shows both `--check-only` (notify) and `--yes` (auto-apply).

---

## [2.4.1] — 2026-06-22

### Fixed
- **Hidden & hyphenated directories now resolve.** Added an exact reverse-mangle index (`_value.scan_home` / `mangle_path`) built from one cached `$HOME` walk using Claude Code's real transcript-dir naming rule (`[^a-zA-Z0-9]` → `-`), so directories the old heuristic missed — hidden roots like `.claude-mem` and hyphenated names like `100x-dev` — resolve to their real path and show git/fs value. Marker-file discovery and the index now share a single walk (denylist pruning instead of a blanket dotdir skip).
- **Deleted directories are labelled.** Token-spend directories whose path no longer exists on disk (renamed repos, cleaned-up agent worktrees, temp dirs) now render as **(removed)** with a dimmed label instead of a bare `—`, making clear the directory is gone rather than the value being broken.

---

## [2.4.0] — 2026-06-22

### Added
- **Automatic all-directory value view.** The token dashboard now shows every directory that consumed tokens (repo or not) plus every agentic project discovered machine-wide via marker files (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules`, `.windsurfrules`, `.clinerules`, `.github/copilot-instructions.md`) — even with zero Claude token spend.
- **Machine-wide marker-file discovery** — `_value.discover_project_dirs` walks `$HOME` up to a configurable depth and surfaces project roots by marker file; `cached_discover` caches the walk in `value.json` so subsequent builds are instant.
- **Four inline-SVG charts** (zero-dependency, fully offline): a **leverage** scatter (value vs cost with a break-even line), cost-over-time, token-purpose split, and cost-by-directory — all with axis labels and interactive value tooltips.
- **Cached AI one-liners** per directory via the local `claude` CLI (non-blocking background pass; degrades silently when absent).
- **Pluggable cost adapters** (`scripts/adapters/`): `claude_code` is real; `codex` is a documented stub for when the Codex CLI is used locally.
- **Auto-start daemon** — the token + value dashboard launches itself (detached, machine-wide singleton) on shell startup and on `100xprism install` / `init` / `update`, so it's always live without running a command. The startup line surfaces the URL; opt out with `export PRISM_NO_DASHBOARD=1`.
- **"How this is calculated" explainer** on the dashboard, plus **axis labels and interactive value tooltips** on every chart.

### Changed
- **Value is now tool-agnostic** — derived from git history (commits / PRs / files / churn) with a filesystem-mtime fallback for non-repos; no CHANGELOG or manual registration needed.
- **Cost stays Claude-Code-only** (the only tool with local token accounting); directories from other tools show value with `—` cost, never $0.
- **`value.json` is an automatic cache** of per-directory value snapshots, keyed by dir + git HEAD + date window — not a manual registry (store schema v2). The dashboard's transcript cache version was also bumped so every cached summary carries its project directory — fixing directories that previously collapsed into a single bucket.

### Removed
- **`_shipped.py`** — superseded by `_value.py` for all CLI and dashboard parsing.
- **The `100x-value` registration step** and the manual `100x-value`-in-a-repo workflow — value is now derived automatically for every directory.
- **The registry `source` field** and the old "Value — cost vs. what shipped" registry panel — replaced by the automatic per-directory view.

---

## [2.3.4] — 2026-06-21

### Added
- **Value × cost, one dashboard.** `100x-value` no longer just summarizes what shipped — it now **registers each repo** in a central store at `~/.100xprism/value.json`, and `token-dashboard.py` renders a **"Value — cost vs. what shipped"** panel in the same URL: each release's *estimated* token cost (attributed by date window — directional, not billed-per-feature) next to what it delivered. New `scripts/_shipped.py` is the one parser shared by the CLI and the dashboard so they never drift.
- **Dashboard auto-refresh** — the token dashboard rebuilds every 5 minutes (background thread + client poll) so a long-lived tab never goes stale; the header shows an `updated HH:MM:SS` stamp.

### Fixed
- **Unreleased-work double-count** — `value-report.py` derived its "unreleased" boundary from `git describe --tags`, so when the CHANGELOG ran ahead of the git tags (as 2.3.2/2.3.3 did), already-released commits showed up *again* as unreleased. The boundary is now the most recent `chore(release):` commit (tag as fallback), so released versions stop reappearing.

### Changed
- **Token store reads off the request path** — the dashboard refreshes the value store on its rebuild tick (startup / 5-min / manual Rescan) and caches it, instead of running git per `/api/value` request.

---

## [2.3.3] — 2026-06-21

### Added
- **Token & value economics.** New `scripts/value-report.py` (what *shipped*, from CHANGELOG + git) to read next to the token dashboard's cost view. `token-dashboard.py` gains an estimated content-composition breakdown (code vs files-read vs logs vs model prose vs prompts — char-based, labelled an estimate), a machine-global **singleton** (one URL covers every session + repo; relaunching opens the running one), and a `--oneline` startup summary. `100x-tokens` / `100x-value` aliases added; opt-out startup line via `PRISM_NO_TOKEN_LINE`.
- **Positioning** — README + landing page now frame the shift from "make agentic dev autonomous" to "measure what it costs (tokens) and what it's worth (value) — everybody's responsibility," and the landing page links the docs.

### Changed
- **Brand** — primary logo + hero reworked from the prism mark to a **leverage** mark (small effort → 100× outcome). README author credit updated.

---

## [2.3.2] — 2026-06-20

### Added
- **`motion-framer` plugin** — Framer Motion / Motion animation recipes (variants, gestures, layout + exit animations, spring physics, scroll effects) from the community `claude-design-skillstack` marketplace. Plugin count 13 → 14. It's a markdown skill (no executable code/MCP), so a small supply-chain surface.

---

## [2.3.1] — 2026-06-20

### Fixed
- **`lib/adapters/windows.js`** — migrated the Windows adapter off the pre-v2 `workflows/` layout (it read a non-existent `installDir/workflows`, so `100x-dev install`/`init`/`update` threw `ENOENT` and emitted nothing on Windows). It's now a pure-JS port of `adapters/lib/modules.py emit-claude-code`: walks `modules/<slug>/SKILL.md`, writes Claude Code skills + slash-command aliases with a generation marker, writes a manifest, and **prunes** stale skills/aliases (manifest + marker + removed-modules list) without touching the user's own files. Output is byte-for-byte identical to the Python emitter (verified across aliases, skill trees, and manifest). `generateCombinedConfig` (replacing `generateCombinedWorkflows`) now builds the single-file project config from `modules/`; `WORKFLOW_ORDER` and `copyWorkflowsToClaudeCommands` removed; `mergePluginsJson` retains add/remove parity. Closes #54.
- **`test/windows-adapters.test.js`** — rewritten for the `modules/`-based emit, covering skill/alias emission, pruning of removed modules, and preservation of user-authored skills/commands.

---

## [2.3.0] — 2026-06-20

Token-footprint cleanup + update-propagation fixes. Two near-duplicate modules are merged, a local token-usage dashboard is added, and `install`/`update` now remove stale artifacts instead of only adding them.

### Added
- **`scripts/token-dashboard.py`** — offline, dependency-free local dashboard (web UI + `--print`) over `~/.claude/projects/**/*.jsonl`. Breaks usage into the four token purposes (input / output / cache-read / cache-write), shows a startup-bloat meter (fixed context re-sent each turn), and per-project / per-model / per-day breakdowns. Incremental on-disk cache for fast re-runs.
- **`docs/token-optimization.md`** — audit of the installed plugin/skill/hook/MCP footprint (duplication + conflicting capabilities), ranked optimization recommendations, and a monitoring guide.
- **`adapters/lib/sync_plugins.py`** — single, testable plugin-reconciler used by both install and update.

### Changed
- **Module dedup (68 → 66 modules, 42 → 40 auto-trigger skills):** `systems-architect` merged into **`enterprise-design`** (a strict superset); `conversion-copy` merged into **`copywriting`** (folded in as a "Full-Page Mode" section). `figma-translator` repointed to `enterprise-design` + `copywriting`. Counts synced across README/AGENTS/USAGE/install/package.json; dead `trigger-overlap-allow.txt` entries pruned.
- **`assets/postcard-stack.html` + `postcard-stack.png`** — re-rendered for v2.3.0 with corrected counts and the merged module names.
- **`README.md`, `docs/USAGE.md`** — token-usage/optimization docs linked; update-behavior and monitoring sections added.

### Fixed
- **`adapters/lib/modules.py` (`emit-claude-code`)** — now writes a per-skill marker + a manifest and **prunes** skills and slash-command aliases it previously emitted that no longer exist (e.g. merged/removed modules), without ever touching the user's own hand-authored skills/commands. Previously add-only, so removed modules orphaned in `~/.claude/skills` and `~/.claude/commands`. (Cursor/Codex emitters already pruned via markers.)
- **Plugin sync** — install + update now **add and remove**: 100x-dev-managed plugins dropped from `plugins.json` are removed from `settings.json`, without flipping a value the user set or removing plugins the user enabled themselves. Replaces three duplicated add-only inline blocks in `update.sh`/`adapters/claude-code.sh`; `lib/adapters/windows.js` `mergePluginsJson` gains the same parity.
- **Tests** — added `test/claude-code-emit.test.js`, `test/sync-plugins.test.js`, and a removal case in `test/windows-adapters.test.js` (57 pass).

---

## [2.2.0] — 2026-06-12

Frontend UI/UX surface expansion. Three new design-category modules round out coverage alongside the existing `visual-system-architect`, `interaction-engineer`, and `figma-translator`. One additional plugin is curated through `plugins/plugins.json` so fresh installs pick it up automatically.

### Added
- **`modules/a11y-auditor/`** — Senior Accessibility Engineer skill. Audits a page, component, or design against WCAG 2.2 AA. Outputs a triaged finding list (Critical/Serious/Moderate/Minor), focus-order map, contrast report, ARIA decision tree, screen-reader test plan (VoiceOver/NVDA/JAWS/TalkBack), and ready-to-run axe/lighthouse/pa11y commands.
- **`modules/motion-designer/`** — Senior Motion Designer skill. Specifies UI animation (micro-interactions, entrances, state expression, scroll-driven, page transitions, layout transitions). Ships duration + easing tokens, Framer Motion + CSS recipes, performance rules (compositor-only properties, animation budgets), and a `prefers-reduced-motion` strategy on every recommendation.
- **`modules/data-viz/`** — Senior Data Visualization Designer skill. Chart-type decision tree organized by question type, dashboard layout heuristics, library picker (Recharts / visx / D3 / ECharts / Plotly / Observable Plot), color-blind safe palette guidance, and mandatory empty/loading/error/partial states for every chart.
- **`plugins/plugins.json`** — added `chrome-devtools-mcp@claude-plugins-official` (was already installed at user scope; now curated so `100x-dev install` provisions it on fresh machines).

### Changed
- **`README.md`, `docs/USAGE.md`, `ROADMAP.md`** — refreshed module/plugin/skill counts (65→68 modules, 12→13 plugins, 39→42 auto-trigger skills).
- **`assets/postcard-stack.html` + `postcard-stack.png`** — re-rendered with the three new design skills under Engineering & Design (now 9 modules in that group), `chrome-devtools-mcp` in the claude-plugins-official band, and version stamp `v2.2.0`.
- **`scripts/trigger-overlap-allow.txt`** — allow-listed the benign `data-viz ⇄ motion-designer` keyword overlap (0.175). Both share generic UI tokens (loading/states/dashboard/interactive) but cover disjoint domains (chart selection vs. animation specs).

---

## [2.1.1] — 2026-05-31

### Fixed
- **`update.sh`** — `run_plugin_updates()` is now scope-aware. `claude plugin update` defaults to **user** scope and exits non-zero with *"not installed at scope user"* for plugins installed at **project**/**local** scope — which surfaced as spurious update failures (e.g. `ui-ux-pro-max@ui-ux-pro-max-skill`, `understand-anything@understand-anything`). Each plugin is now retried across `user → project → local` and only reported failed when it is absent from every scope. The plugin **name was never wrong** — the id form `name@marketplace` is correct; the bug was scope handling.
- **`update.sh`** — added a `CLAUDE_BIN` override and a `UPDATE_SH_SOURCE_ONLY` source-only guard so the plugin-update logic is unit-testable; **`test/update-plugins.test.js`** covers the project-scope, user-scope, and absent-from-all-scopes paths against a stubbed CLI.

---

## [2.1.0] — 2026-05-31

Frontier-model modernization (issues #21–#27) — generator-layer model routing, enforcing hooks, an eval/meta safety net, Workflow/subagent fan-out, and stack-detection de-hardcoding. Backward compatible; new behavior is opt-in.

Model routing through the generator (issue #21 / Theme 1).

### Added
- **`adapters/lib/modules.py`** — the 9 `<!-- model: X -->` comments are now parsed and routed instead of being dead prose. `MODEL_ALIASES` maps bare tiers (`haiku`/`sonnet`/`opus`) to current IDs, a `sonnet` middle tier is introduced, and all 7 emitters tolerate/strip the key. Non-Claude adapters get a documentation-only `_Runs best on:_` hint, not functional routing.

Workflow/subagent fan-out (issue #25).

### Added
- **`modules/subagents/SKILL.md`** — capability-gated fan-out ladder (Workflow → parallel Agent/Task → serial fallback), the standard structured return contract (`{summary,findings[],files[],risks[],confidence}`), and a Haiku-breadth / Opus-depth routing note; context isolation reframed for the 1M era (parallelism + adversarial independence, not token savings).
- **`modules/orchestrate`, `gate`, `test`, `launch`** — fan out independent work: orchestrate gains a plan → parallel-impl → structured-diff → independent review → merge-gate block; gate runs independent Gates 2–5 in parallel; the serial lifecycle skills degrade gracefully on non-Claude-Code platforms.

Shared stack-detection; de-hardcode GCP/npm (issue #26).

### Added
- **`modules/_lib/reference.md`** — reference-only maintainer doc (never globbed/emitted/counted) holding the canonical, network-free stack-detection block (cloud provider / test runner / CI system / JS package manager).

### Changed
- **`architect`, `issue`, `fix-bugs`** — branch on detected stack instead of assuming GCP+Firebase+Stripe+npm; GCP/Cloud Run paths gate behind `CLOUD=gcp` and resolve the project from `gcloud config`. Fixed `architect`'s malformed `description: >` block-scalar that had stripped all natural-language triggers.

Test skill reference + configurability (issue #27).

### Changed
- **`modules/test/SKILL.md`** — fixed the broken `../docs/e2e-patterns.md` reference (doc relocated to `modules/test/references/e2e-patterns.md`); coverage thresholds (95%/90%) are now defaults that a project can override (CLAUDE.md/AGENTS.md, jest/vitest config, `pyproject.toml`/`.coveragerc`); the coverage loop is bounded by `MAX_ITERATIONS` (default 6) with escalation; Postgres/Redis image versions are detected from the project's compose files instead of hardcoding `postgres:16`/`redis:7`.

---

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
