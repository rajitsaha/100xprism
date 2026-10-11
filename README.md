<div align="center">

<img src="assets/100xprism-logo.svg" alt="100xPrism logo" width="120" />

# 100xPrism

### Stop vibe coding. Ship production-grade software.

[![Version](https://img.shields.io/github/v/release/rajitsaha/100xprism?style=flat-square&label=version&color=brightgreen)](https://github.com/rajitsaha/100xprism/releases/latest)
[![npm](https://img.shields.io/npm/v/100xprism?style=flat-square&color=red)](https://www.npmjs.com/package/100xprism)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

**One source of truth.** 68 modules generate native config for **Claude Code · Cursor · Codex · Pi · Hermes/OpenClaw**. Quality gates run on every commit.

<img src="assets/100xprism-hero.svg" alt="100xPrism — one config, every AI coding tool · 2 plugins, 28 slash commands, 40 auto-trigger skills" width="100%" />

</div>

---

## Install

**npm (any platform — macOS, Linux, Windows):**
```bash
npm install -g 100xprism && 100xprism install
```

**curl (macOS / Linux):**
```bash
curl -fsSL https://raw.githubusercontent.com/rajitsaha/100xprism/main/get.sh | bash
```

Either way, `100xprism install` clones the toolkit to `~/100xprism` and provisions your AI tools. The npm package is a thin launcher — the modules, hooks, and plugins live in that clone, which `100xprism update` keeps current.
Before installing, 100xPrism cleans legacy startup hooks, stale command links, and any old owned dashboard process. Install does not edit `~/.zshrc`, `~/.bashrc`, or `~/.bash_profile`. If you want optional aliases for the current terminal only, run `source ~/100xprism/shell/aliases.sh`.

> **Windows:** plugin sync works, but native Windows module emit is being reworked ([#54](https://github.com/rajitsaha/100xprism/issues/54)). For full module support today, install under **WSL** with either method above.

**Set up a project:**
```bash
cd your-project && 100xprism init
```

**Keep up to date:**
```bash
100xprism update                    # pull latest, then add/update/remove skills + plugins
100xprism update --plugins-only     # refresh plugins only (repo already current)
100xprism update --no-slim          # skip the one-time always-on index slim
100xprism slim --dry-run            # preview what slimming would change
100xprism hermes                    # install/refresh Hermes/OpenClaw skills on demand
100xprism audit --fix               # repair drift: dead hook paths, v1 leftovers, missing catalog
100xprism uninstall                 # remove skills, aliases, hooks, catalog, links (keeps your data)
100xprism uninstall --projects      # …and generated files in every tracked repo
100xprism uninstall --purge         # …and all of ~/.100xprism
npm install -g 100xprism@latest     # (optional) upgrade the launcher itself
```

**Cleanup contract.** 100xprism removes only what it can prove it wrote — a generated
marker, a manifest entry, a `/100xprism/hooks/` path, or an exact v1 file header — so
your own skills, commands, hooks, and a hand-written `AGENTS.md` are never touched.
`install`/`update` sweep v1 leftovers and prune stale skills; `uninstall` reverses the rest.
Run `100xprism uninstall` *before* `npm rm -g 100xprism`: npm's own hook only drops
settings.json hooks that would point at deleted scripts.

`install` and `update` auto-run the Hermes adapter whenever `~/.hermes` or the `hermes`
CLI is detected — `100xprism hermes` is only needed if you installed Hermes after
100xprism and don't want to wait for the next scheduled update.

`install` and `update` are **fully reconciling**, not append-only — every run:
- **adds** newly shipped skills, slash commands, and curated plugins,
- **updates** changed ones in place, and
- **removes** skills, slash-command aliases, and 100xprism-managed plugins that were deleted or merged upstream.

Your own hand-authored skills/commands and any plugins you enabled yourself are never touched. See [docs/USAGE.md](docs/USAGE.md#keeping-up-to-date) for details.

> **Cloned to a custom path?** The default install lives at `~/100xprism`. If you cloned elsewhere, update your shell + Claude Code config — see [Custom install location](docs/USAGE.md#custom-install-location).

---

## The pipeline

```
/understand → /context → /issue → /spec → /fix → /commit
                                                    ↓
              /techdebt ← /gate → /grill → /pr → /push → /release
```

Every `/commit` and `/push` runs a 5-point gate — tests, security, build, Docker, cloud. Nothing ships without passing.

---

## What you get

| | |
|---|---|
| **68 modules** | 28 slash commands + 40 auto-trigger skills — see [full reference below](#slash-commands) |
| **2 Claude Code core plugins** | GitHub + security guidance by default; other integrations are profile recommendations or manual opt-ins |
| **7 database engines** | Postgres, Cloud SQL, Snowflake, Databricks, Athena, Presto, Oracle — one `/db` interface |
| **27 SaaS CLIs** | `/connect` installs + authenticates GitHub, AWS, Stripe, Supabase, and more from `.env` |
| **4 project templates** | node-fullstack · node-frontend · python-api · docker-compose |
| **CI/Release pipelines** | Drop-in GitHub Actions for lint + real-DB tests + E2E + semantic-release |

---

## Optional vendor skill packs

Some skills only matter to some projects, so they don't ship by default. `/pack`
installs them on request instead:

```bash
100xprism pack               # list every pack, install state, and anything detected here
100xprism pack detect        # only what matches the current project
100xprism pack install databricks
100xprism pack uninstall databricks
```

Packs are ownership-tracked per install — `100xprism pack uninstall` only removes
what it added, never a plugin or command you enabled yourself. Where the vendor
ships a CLI (e.g. `databricks aitools install`), that's preferred; otherwise
100xPrism installs directly per platform. First pack: **Databricks** (30+ skills —
Unity Catalog, Asset Bundles, Lakeflow, model serving, vector search).

---

## Token, delivery & value economics

For a while, the goal was singular: make spec-driven, agentic development as **autonomous** as possible — let the agent plan, build, gate, and ship with less and less human steering.

That part is mostly working. The harder, more important question is the one that comes next: **what is all this autonomy costing, what did it help deliver, and what was that delivery worth?** Every agent run spends real money in tokens. The dashboard measures cost and observable engineering delivery separately; business or human value remains explicitly unmeasured until an outcome source is connected.

This is a first, deliberately humble attempt to **make the measurement chain visible to everyone**, because watching it isn't one person's job. **It's everybody's responsibility.**

```bash
100xprism tokens                  # local dashboard
100xprism tokens --json           # fast, versioned provider-counter report
100xprism tokens --json --tool codex
100xprism audit --json             # standing-context estimate + skills/plugins/hooks
100xprism value                    # delivery economics
```

- **`100xprism tokens`** — one offline, machine-wide dashboard at a single URL: the input/output/cache split, a startup-bloat meter, an *estimated* code-vs-files-read-vs-logs-vs-chat composition, and $ cost — by project, model, session, and skill. Claude Code and Codex provide exact local token counters; Cursor agent-transcript JSONL and Antigravity local artifacts contribute project/session/activity coverage but are never assigned invented token cost because their local formats expose no counters. Cursor chats, `state.vscdb`, and legacy transcript `.txt` are outside the collector's scope. It auto-refreshes every 30 seconds once started. Start it explicitly with `100xprism tokens`, `100xprism dashboard`, or `100xprism install --dashboard`; shell startup never starts it.
- **`100x-value`** — the dashboard joins exact local token counters to observable delivery evidence. It shows **every directory that consumed tokens** plus agentic projects discovered machine-wide via marker files. Git supplies commits, deduplicated merged PRs, releases, files, insertions, and deletions; non-repos use an explicitly labeled filesystem-mtime estimate. Directories from unsupported tools show `—` cost, never a misleading $0. Delivery unit costs use attributed spend only, show attribution coverage, and are never presented as business ROI.
- **Budgets, provenance, recommendations & GitHub PR insights** — optional daily/weekly limits drive dashboard and shell alerts. A provenance strip shows pricing coverage, outcome-join coverage, source counts, and date range. Recommendations are ranked by estimated opportunity and pair the observed evidence with an action that preserves AI-native autonomy. Optional GitHub CLI integration can fetch PR metadata for locally checked-out remotes plus configured users/repos.

### Paying only for the skills a repo uses

An installed module's **description** may be re-sent on every turn; its **body** is
loaded only when invoked by tools that support progressive disclosure. Fresh installs
therefore default to the 12 must-have workflows plus one generated `100x-resolver`.
Specialist bodies stay on disk and are loaded through `/100x <workflow>` (or the
closest native skill invocation) only when needed.

```bash
100xprism optimize              # enforce must-only for user scope + this repo
100xprism optimize --all-projects
100xprism optimize --skills=profile  # widen to detected project profiles
100xprism optimize --skills=all      # restore every module
100xprism slim ...                   # compatibility alias
```

Current deterministic description-footprint estimates (characters ÷ 4): Claude falls
from ~4,816 tokens in `all` mode to ~574 in `must`; Cursor falls from ~1,942 to
~261. Codex and Pi use the same must-first selection. CI enforces committed must-mode
budgets, and `100xprism audit` separately inventories instruction files, aliases,
plugins, and hooks. These are standing-context estimates, not provider-billed usage.

Full guide: [docs/token-optimization.md](docs/token-optimization.md).

---

## What it actually changed

Two open-source products built with this toolkit. Everything in the **Measured**
column is read straight from git history and reproducible with
`100xprism value` — nothing is modelled.

| Measured from git | [100xprism](https://github.com/rajitsaha/100xprism) | [agentbreeder](https://github.com/agentbreeder/agentbreeder) |
|:---|---:|---:|
| First commit → first public release | **6 days** | **32 days** |
| Releases shipped | 26 | 17 |
| Median gap between releases | **1 day** | **2 days** |
| Merged PRs | 70 | 171 |
| Commits | 204 | 772 |
| Lines added | 78,447 | 541,370 |
| Peak commits in a single day | 39 | 61 |
| Active engineering days | 29 | 53 |

Combined: **43 releases · 255 merged PRs · 650K lines · 94 active engineering days.**

The same toolkit drove two further products that are not open source — a
draft-content generation SaaS and an AI-native real-estate deal-analysis SaaS —
adding **2,219 commits · 970 merged PRs · 1.13M lines** over **171 active days**.

Across all five: **3,282 commits · 1,225 merged PRs · 1.78M lines added ·
5,846 files**, by one person.

### The comparison, stated as an estimate

There is no counterfactual — nobody built these twice. So this is arithmetic on a
stated assumption, not a measurement, and you should substitute your own numbers:

> **Assume** a conventional team merges ~2 feature-sized PRs per engineer-week.
> 1,225 merged PRs ÷ 2 ≈ **612 engineer-weeks ≈ 11.8 engineer-years**, delivered
> in 265 active days. At a $85/hr contract rate that block of work prices at
> roughly **$2.1M**.

Three honest caveats. PR size varies enormously, so PR count is a coarse unit. A
solo builder skips coordination overhead a team pays, but also carries no review
redundancy. And the figure moves a long way if you assume 1 or 4 PRs per
engineer-week — which is exactly why the assumption is written down rather than
buried in a headline number.

What is *not* claimed: that any of this is business value. Revenue, retention, and
customer outcomes are unmeasured here, and the dashboard deliberately refuses to
invent a score for them.

### Where the speed comes from

| Lever | Effect |
|:---|:---|
| `/spec` → `/orchestrate` → `/fix` | A vague request becomes an implementation-ready spec before any code is written |
| `/gate` + the `gate-on-commit` hook | Broken work cannot reach `main` — enforcement is a hook, not a reminder |
| `/issue` | An observation becomes a root-caused, actionable GitHub issue in one step |
| `/release` | 26 releases at a 1-day median cadence, because shipping is one command |
| 40 auto-trigger skills | Marketing, SEO, pricing, and CRO work happens in-repo instead of waiting on a contractor |

---

## Slash commands

The following 28 slash commands are available. Run them inside Claude Code. In Codex, use the generated repo skill by name instead, for example `$gate`, `$commit`, or `/skills`.

### Lifecycle

| Command | What it does |
|:--------|:-------------|
| `/branch` | Create a conventional feature branch (`feat/`, `fix/`, `chore/`) |
| `/commit` | Gate → stage → conventional commit |
| `/grill` | Adversarial code review before opening a PR |
| `/pr` | Gate → push branch → create PR |
| `/push` | Gate → push → monitor CI → verify production health |
| `/release patch\|minor\|major` | Semantic versioning + publish to PyPI/npm/Docker Hub |
| `/launch` | Full deploy pipeline in one command |

### Quality

| Command | What it does |
|:--------|:-------------|
| `/gate` | **Mandatory** 5-point quality gate (tests, security, build, Docker, cloud) |
| `/test` | All test layers (unit, integration, E2E) — loops until 95% coverage |
| `/lint` | Auto-detect and fix all lint errors (ESLint, TypeScript, ruff) |
| `/security` | Vulnerability + secrets scan, auto-fix where possible |
| `/cloud-security` | GCP IAM, networking, PII, and compliance scan |
| `/eval` | Run module evals — check triggers and output quality |

### Engineering

| Command | What it does |
|:--------|:-------------|
| `/spec` | Turn a vague request into an implementation-ready spec |
| `/fix` | Autonomous bug fixer — CI failures, docker logs, Slack pastes |
| `/orchestrate` | Plan-first methodology for complex multi-step tasks |
| `/techdebt` | Dead code, duplication, stale TODOs |
| `/context` | 7-day git + GitHub activity dump — orient before coding |
| `/update-claude` | Write a CLAUDE.md rule after any correction |

### Data & Infrastructure

| Command | What it does |
|:--------|:-------------|
| `/db` | Query any of 7 database engines from one interface |
| `/query` | Plain-English analytics — describe what you want, get SQL |
| `/connect` | Install + auth 27 SaaS CLIs from `.env` |

### Documentation & Architecture

| Command | What it does |
|:--------|:-------------|
| `/docs` | Detect code changes and update documentation |
| `/issue` | Investigate a bug and create a detailed GitHub issue |
| `/architect` | Architectural Q&A and decision matrices |
| `/enterprise-design` | Full technical blueprint — IA, API, data model, stack |

### Auto-trigger skills (40)

These modules activate automatically when you describe a relevant task — no slash command needed.

| Category | Modules |
|:---------|:--------|
| **Marketing copy** | copywriting, copy-editing, cold-email, email-sequence, ad-creative, social-content |
| **SEO** | seo-audit, ai-seo, programmatic-seo, schema-markup, site-architecture |
| **CRO & conversion** | page-cro, signup-flow-cro, onboarding-cro, form-cro, popup-cro, paywall-upgrade-cro |
| **Growth & strategy** | content-strategy, marketing-ideas, marketing-psychology, launch-strategy, referral-program, churn-prevention, free-tool-strategy, ab-test-setup, analytics-tracking, pricing-strategy |
| **Sales** | sales-enablement, competitor-alternatives, paid-ads, revops, product-marketing-context |
| **Design & UX** | visual-system-architect, interaction-engineer, figma-translator, motion-designer, data-viz, a11y-auditor |
| **Engineering** | subagents, terminal-setup |

After `100xprism slim`, most of these load through the generated `100x-resolver` catalog instead of sitting in your always-on context — same capability, a fraction of the standing token cost.

---

## How it works in your tool

| Tool | Generated artifact | Auto-trigger? |
|:-----|:-------------------|:--------------|
| **Claude Code** | `~/.claude/skills/<slug>/` + slash command aliases | Yes — per description |
| **Cursor** | `.cursor/rules/<slug>.mdc` (one file per module) | Yes — per description |
| **Codex** | `AGENTS.md` + `.agents/skills/<slug>/` + `.codex/hooks.json` | Yes — repo skills |
| **Pi** | `.pi/skills/<slug>/` (retention-filtered, per project) | Yes — per description |
| **Hermes/OpenClaw** | `~/.hermes/skills/100xprism/<slug>/SKILL.md` (global, reconciling) | Yes — description re-derived to fit Hermes's ~57-char always-on budget |

Every supported tool loads module bodies on demand rather than inlining them, so the always-on context stays small. `tier: core` marks the modules Cursor keeps resident (`alwaysApply: true`) — since v3.1 that is **`gate` alone**, because `gate` is the only one that must fire unprompted; `commit`, `push`, and `branch` are explicitly invoked and their gate enforcement is guaranteed by the `gate-on-commit` hook, not by prompt residency. Everything else is fetched when its description matches. Claude Code plugins remain Claude-specific; use Codex `/plugins` for Codex-native plugins.

> **Removed in v3.0.0:** Windsurf, Copilot, Gemini, and Antigravity. Those adapters emitted a single concatenated file (~60K chars) that sat in context on every turn — the opposite of progressive disclosure.
>
> **This deletes files in your projects.** `100xprism update` removes the `.windsurfrules`, `GEMINI.md`, `ANTIGRAVITY.md`, and `.github/copilot-instructions.md` it previously generated from every project in `~/.100xprism/tracked-projects`; `100xprism init` does the same for the project it runs in.
>
> Only files carrying the `Generated by 100xprism` header are touched, so a hand-written file of the same name is left alone. **If you edited a generated file, it is still removed** — but every removal is copied to `~/.100xprism/removed-artifacts/<timestamp>/` first, and is only removed once that copy succeeds. These files are also normally committed, so deletions show up in `git status` for you to review.

---

## Deprecations & removals

What recent releases took away, and what to do about it. Full detail in the
[changelog](CHANGELOG.md).

| Version | Removed / changed | Migration |
|:---|:---|:---|
| **3.1** | `commit`, `push`, `branch` dropped from `tier: core` — they no longer sit resident in Cursor's context | None. They are still slash commands; gate enforcement now comes from the `gate-on-commit` hook rather than prompt residency |
| **3.1** | Specialist modules leave the always-on skill index on your first `update` | Automatic and announced once. Undo with `100xprism slim --skills=all`; skip with `100xprism update --no-slim` |
| **3.1** | Project config moves out of `CLAUDE.md` into `.claude/100xprism.yml` | None required — modules read the new file first and **fall back to `CLAUDE.md`**, so existing repos keep working |
| **3.1** | New `CLAUDE.md` scaffolds use a router table instead of commented config blocks | Applies to newly scaffolded projects only; existing files are never rewritten |
| **3.0.1** | Nine modules re-tiered `core` → `on-demand` (Cursor context down 61%) | None. No content changed, no effect on Claude Code, Codex, or slash commands |
| **3.0.0** | **Windsurf, Copilot, Gemini, Antigravity adapters deleted** | Automatic. Generated files are backed up to `~/.100xprism/removed-artifacts/<timestamp>/` before removal. Use Claude Code, Cursor, or Codex |
| **3.0.0** | `emit-concat` subcommand, `render_concat`, `render_index_only` | Internal API — use `emit-cursor` / `emit-codex` / `emit-claude-code` |
| **3.0.0** | Instruction-file discovery narrowed to `CLAUDE.md AGENTS.md .cursorrules` | Move any config out of `.windsurfrules` / `GEMINI.md` / `copilot-instructions.md` |
| **2.4.9** | 11 orphaned module reference files | None — they were unreachable from any `SKILL.md` |
| **2.4.9** | Dashboard and update-check no longer auto-start from shell startup | Start explicitly: `100xprism tokens`, or `100xprism install --dashboard` |
| **2.4.x** | `systems-architect` → merged into `enterprise-design`; `conversion-copy` → merged into `copywriting` | Use the surviving module; `update` prunes the old skill and its alias |

**Nothing is removed without a backup.** Every deletion inside your repositories is
copied to `~/.100xprism/removed-artifacts/<timestamp>/<full-project-path>/` first and
removed only if that copy succeeded — and only if the file carries the
`Generated by 100xprism` header in its first 10 lines.

---

## Common CI traps it fixes

`npm install` 404 inside Docker · `useState(false)` opacity-0 breaking Playwright · integration tests silently excluded from the gate. [Full breakdown →](docs/ci-traps.md)

---

## More

- [Full usage guide](docs/USAGE.md) — daily patterns, multi-project setup, CI templates, project config, troubleshooting
- [Architecture](docs/v2-refactor.md) — why modules replaced workflows + skills
- [Token usage & optimization](docs/token-optimization.md) — audit your plugin/skill footprint and monitor token spend with the local dashboard
- [Changelog](CHANGELOG.md) · [Roadmap](ROADMAP.md) · [Issues](https://github.com/rajitsaha/100xprism/issues)

---

<div align="center">

Built by [Rajit Saha](https://www.linkedin.com/in/rajsaha/) · 23 years building data and platform systems at scale

[![LinkedIn](https://img.shields.io/badge/LinkedIn-Connect-blue?style=for-the-badge&logo=linkedin)](https://www.linkedin.com/in/rajsaha/)
[![GitHub](https://img.shields.io/badge/GitHub-Follow-black?style=for-the-badge&logo=github)](https://github.com/rajitsaha)

If this saves you time, **[star the repo](https://github.com/rajitsaha/100xprism)**.

</div>
