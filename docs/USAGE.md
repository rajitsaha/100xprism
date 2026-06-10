# How to Use 100x Dev

---

## How it works

100x Dev ships 65 modules as markdown files with YAML frontmatter. Your AI tool reads them and follows the instructions — running commands, enforcing thresholds, looping until checks pass.

Each module is the **single source of truth**. Adapters generate the right format for each tool:

| Delivery | Tools | How modules arrive |
|:---------|:------|:-------------------|
| **Global install** | Claude Code | Each module → `~/.claude/skills/<slug>/SKILL.md`, plus slash command aliases in `~/.claude/commands/` |
| **Per-project (multi-file)** | Cursor | One file per module → `.cursor/rules/<slug>.mdc` (auto-trigger via description) |
| **Per-project (single-file)** | Codex, Windsurf, Copilot, Gemini, Antigravity | Core modules inlined + on-demand index → `AGENTS.md` / `.windsurfrules` / etc. |

---

## Installation

### Step 1 — Global setup (once per machine)

**Mac / Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/rajitsaha/100x-dev/main/get.sh | bash
source ~/.zshrc   # or ~/.bashrc — reload shell to activate the 100x-dev command
```

**Windows** (or anywhere Node.js is installed):
```bash
npm install -g 100x-dev && 100x-dev install
```

The installer:
1. Emits all 65 modules to `~/.claude/skills/`
2. Creates 26 slash command aliases in `~/.claude/commands/`
3. Merges 12 plugins into `~/.claude/settings.json`
4. Adds shell aliases (`cc`, `ccc`, `100x-update`, `100x-check`)
5. Copies 4 project templates to `~/100x-templates/`
6. Optionally installs enforcing hooks (gate-on-commit, secret-scan)

> **Terminal vs Claude Code:** `100x-dev install`, `init`, `update`, and `check` run in your **terminal**. Slash commands like `/commit` and `/gate` run **inside Claude Code**. If you see "command not found", you're in the wrong environment.

### Step 2 — Project setup (once per project)

```bash
cd my-project && 100x-dev init
```

This generates the right instruction file for each enabled tool (`.cursor/rules/`, `AGENTS.md`, `.windsurfrules`, etc.). **Commit the generated file** so teammates get the same modules on clone.

It also scaffolds a `CLAUDE.md` with placeholders for database, cloud, production URLs, and security exceptions — see [Project configuration](#project-configuration).

### Step 3 — Start using

Open Claude Code in your project and try:
```
/gate        # run the quality gate
/test        # run all test layers
/commit      # gate + stage + commit
```

---

## Keeping up to date

```bash
100x-dev check                  # check if a newer version is available
100x-dev update                 # pull latest + sync plugins + regenerate tracked projects
100x-dev update --plugins-only  # refresh plugins only (when repo is already current)
```

`update` does the following:
1. Pulls the latest code from `origin/main`
2. Backs up `~/.claude/commands/` to `~/.claude/commands.bak.<timestamp>`
3. Re-emits all modules to `~/.claude/skills/` and `~/.claude/commands/`
4. Merges any new plugins into `~/.claude/settings.json`
5. Runs `claude plugin update` for each plugin (updates across all scopes)
6. Syncs any installed hooks to their latest versions
7. Regenerates instruction files in all tracked projects

After updating, **restart your Claude Code session** to load the new modules and plugins.

> **Tip:** Plugins (superpowers, claude-mem, hookify, etc.) receive independent updates. Running `update --plugins-only` refreshes them without re-pulling the repo.

Claude Code also shows an update banner at session start when a new version is available.

---

## Custom install location

The `get.sh` installer clones to `~/100x-dev` by default and writes that path into `~/.zshrc` and `~/.claude/settings.json`.

If you cloned elsewhere (e.g. `~/work/100x-dev`), you'll see errors:
```
.zshrc:source: no such file or directory: /Users/<you>/100x-dev/shell/aliases.sh
SessionStart:startup hook error
```

**Fix both files:**

1. **`~/.zshrc`** (or `~/.bashrc`):
   ```bash
   # 100x Dev — point at wherever you cloned the repo
   export DEV_100X_HOME="$HOME/work/100x-dev"   # adjust to your path
   [ -f "$DEV_100X_HOME/shell/aliases.sh" ] && source "$DEV_100X_HOME/shell/aliases.sh"
   ```

2. **`~/.claude/settings.json`** — update the SessionStart hook. Use `$HOME` (not env vars — hooks run in non-interactive shells that don't source `~/.zshrc`):
   ```json
   "hooks": {
     "SessionStart": [
       {
         "matcher": "",
         "hooks": [
           { "type": "command", "command": "$HOME/work/100x-dev/shell/check-update.sh --claude-hook" }
         ]
       }
     ]
   }
   ```

3. **Verify** — open a new terminal (no errors) and a new Claude Code session (no hook errors).

---

## Using the modules

### In Claude Code — slash commands

The following 26 slash commands are available. Run them directly:

**Lifecycle:**
```
/branch                Create conventional feature branch (feat/, fix/, chore/)
/commit                Gate → stage → conventional commit
/grill                 Adversarial code review before opening a PR
/pr                    Gate → push branch → create PR
/push                  Gate → push → monitor CI → verify production health
/release patch         Bump patch version, tag, publish, verify
/release minor         Bump minor version and publish
/release major         Bump major version and publish
/launch                Full deploy pipeline in one command
```

**Quality:**
```
/gate                  5-point quality gate — MANDATORY before every commit
/test                  All test layers, loops until 95% coverage
/test --unit           Unit tests only
/test --integration    Integration tests only (spins up Docker DB)
/test --e2e            Full-stack E2E via docker compose
/test --e2e staging    E2E against staging environment
/test --e2e prod       E2E against production
/lint                  Auto-detect and fix all lint errors (ESLint, TypeScript, ruff)
/security              Vulnerability + secrets scan, auto-fix where possible
/cloud-security        GCP IAM, networking, PII, compliance scan
/eval                  Run module evals — check triggers and output quality
```

**Engineering:**
```
/spec                  Turn a vague request into an implementation-ready spec
/fix                   Autonomous bug fixer — CI failures, docker logs, Slack pastes
/orchestrate           Plan-first methodology for complex multi-step tasks
/techdebt              Dead code, duplication, stale TODOs
/context               7-day git + GitHub activity dump
/update-claude         Write a CLAUDE.md rule after any correction
```

**Data & Infrastructure:**
```
/db                    Query any of 7 database engines from one interface
/query                 Plain-English analytics — describe what you want, get SQL
/connect               Install + auth 27 SaaS CLIs from .env
```

**Documentation & Architecture:**
```
/docs                  Detect code changes and update documentation
/issue                 Investigate a bug and create a detailed GitHub issue
/architect             Architectural Q&A and decision matrices
/enterprise-design     Full technical blueprint — IA, API, data model, stack
```

### Auto-trigger skills (39 modules)

These modules activate automatically when your prompt matches their description. No slash command needed — just describe the task naturally:

- "Write homepage copy" → triggers `copywriting`
- "Audit SEO on this page" → triggers `seo-audit`
- "Optimize the signup flow" → triggers `signup-flow-cro`
- "Plan a product launch" → triggers `launch-strategy`

### In other tools (Cursor, Codex, Windsurf, Copilot, Gemini)

Reference modules by name in your prompts:

```
"Run the gate workflow before committing"
"Run the test workflow — I need 95% coverage"
"Follow the commit workflow"
"Run the security workflow on this project"
```

In Cursor, modules auto-trigger from their description (same as Claude Code). In single-file tools (Codex, Windsurf, Copilot, Gemini), core modules are always available; on-demand modules appear as an index the AI can reference.

---

## Daily workflows

**Typical session:**
```
/context → /spec or /fix → /test → /commit → /grill → /pr
```

**Pre-release:**
```
/context → /techdebt → /grill → /test → /commit → /gate → /pr → /launch → /release
```

**Quick bug fix:**
```
/fix "the login button returns 403" → /test → /commit → /pr
```

---

## Project configuration

`100x-dev init` scaffolds a `CLAUDE.md` in your project. Uncomment and fill in the sections that apply:

```markdown
## Database
# engine: postgres
# connections:
#   default:
#     host: localhost
#     port: 5432
#     name: mydb
#     user: myuser
#     auth: env:DB_PASSWORD

## Cloud (GCP)
# gcp_project: my-gcp-project
# cloud_run_service: my-service
# region: us-central1

## Production
# production_url: https://example.com
# health_url: https://example.com/health

## Security Exceptions
# security_exceptions:
#   - lodash CVE-2020-XXXX: dev dependency only, not in production bundle

## Rules
# Project-specific rules. /update-claude appends here automatically.
```

These sections are consumed by `/db`, `/gate`, `/cloud-security`, `/launch`, `/push`, and `/security`.

---

## Multi-project setup

### One project at a time

```bash
cd ~/projects/my-app && 100x-dev init
```

### Batch apply to all repos

```bash
for dir in ~/projects/*/; do
  [ -d "$dir/.git" ] && (cd "$dir" && 100x-dev init)
done
```

### Auto-apply on every clone (git template hook)

```bash
mkdir -p ~/.git-templates/hooks
cat > ~/.git-templates/hooks/post-checkout << 'HOOK'
#!/usr/bin/env bash
[ "$3" = "1" ] || exit 0   # branch checkout only
PROJECT_ROOT="$(git rev-parse --show-toplevel)"
[ -f "$PROJECT_ROOT/.cursorrules" ] && exit 0   # already set up
command -v 100x-dev >/dev/null && (cd "$PROJECT_ROOT" && 100x-dev init)
HOOK
chmod +x ~/.git-templates/hooks/post-checkout
git config --global init.templateDir ~/.git-templates
```

Every `git clone` or `git init` now gets modules automatically.

### Team onboarding

Add to your team's onboarding checklist:

```
- [ ] Install: curl -fsSL https://raw.githubusercontent.com/rajitsaha/100x-dev/main/get.sh | bash
      (Windows: npm install -g 100x-dev && 100x-dev install)
- [ ] Reload shell: source ~/.zshrc (or ~/.bashrc)
- [ ] Set up project: cd <your-project> && 100x-dev init
- [ ] Open Claude Code and run /gate to verify
```

For Cursor/Codex/Windsurf teams, commit the generated instruction file — new members get modules on clone.

---

## GitHub Actions templates

Copy into any project:

```bash
mkdir -p .github/workflows
cp ~/100x-dev/github-actions/ci.yml      .github/workflows/ci.yml
cp ~/100x-dev/github-actions/release.yml  .github/workflows/release.yml
```

### ci.yml — runs on every push and PR

| Job | What it does |
|:----|:-------------|
| **lint** | ESLint, TypeScript `tsc --noEmit`, ruff. Skips steps that don't apply. |
| **unit-tests** | Unit + integration tests against real Docker Postgres 16 + Redis 7. 95% coverage enforced. |
| **e2e-tests** | Full `docker compose` stack, smoke tests, then Playwright suite. Skipped if no `playwright.e2e.config.ts` or `e2e/` directory. |

### release.yml — runs on version tags (`v*.*.*`)

Pre-release checks → build → GitHub Release → publish to PyPI/npm/Docker Hub → verify from live registry → Homebrew tap update.

**Required secrets:** `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, `NPM_TOKEN`. PyPI uses OIDC trusted publishing (no secret needed). Jobs that don't apply are skipped automatically.

---

## Common CI traps

Three bugs that consistently cause CI failures when AI tools generate pipelines. All three are documented in the project templates and `ci.yml` with `# TRAP:` comments. [Full breakdown →](ci-traps.md)

### 1. npm package not published → Docker build 404

`npm install` passes locally (cached) but fails in Docker with `404 Not Found`.

```json
// Wrong — 404 in Docker
"dependencies": { "@yourorg/internal-client": "^0.1.0" }

// Fix — local file reference
"dependencies": { "@yourorg/internal-client": "file:./internal-client" }
```

### 2. `useState(false)` animation → Playwright timeout

Form renders with `opacity-0` on first paint. `toBeVisible()` fails on invisible elements.

```tsx
// Wrong — opacity-0 until mount effect runs
const [mounted, setMounted] = useState(false);

// Fix — initialize true (no SSR hydration guard needed in SPA)
const [mounted, setMounted] = useState(true);
```

### 3. Integration tests silently excluded

Tests pass in CI but integration failures only appear after merge.

```yaml
# Wrong — integration tests never run
run: pytest tests/unit/

# Fix — run both
run: pytest tests/unit/ tests/integration/
```

---

## Troubleshooting

| Problem | Solution |
|:--------|:---------|
| "command not found: 100x-dev" | Run `source ~/.zshrc` (or `~/.bashrc`) to reload shell aliases |
| Slash command not recognized in Claude Code | Restart your Claude Code session — modules load at startup |
| "source: no such file: ~/100x-dev/shell/aliases.sh" | You cloned to a custom path — see [Custom install location](#custom-install-location) |
| "SessionStart:startup hook error" | Update the hook path in `~/.claude/settings.json` — see [Custom install location](#custom-install-location) |
| Modules not updating after `100x-dev update` | Restart your Claude Code session to pick up new modules |
| `/gate` hangs on Docker check | Docker Desktop must be running, or set `SKIP_DOCKER=1` in your environment |
| Plugin not activating | Check `~/.claude/settings.json` → `enabledPlugins`, then restart Claude Code |

---

## FAQ

**Does this work without an AI coding tool?**
No. Modules are instructions for AI tools. Without an AI reading them, they're just markdown.

**Can I use only some modules?**
Yes — modules are independent. In Claude Code, run only the slash commands you need. Auto-trigger skills activate only when relevant.

**Will this slow down my workflow?**
The gate adds checks before commits. Most runs complete in under 2 minutes. Catching issues locally is faster than debugging production.

**How do I add a custom database engine?**
Add a file to `modules/db/references/your-engine.md` following the pattern of existing engines, then run `100x-dev update`.

**How do I contribute a new module?**
Create `modules/<slug>/SKILL.md` with the required frontmatter (`name`, `description`, `category`, `tier`), run `100x-dev update` to regenerate, and open a PR.

**How do I contribute a new adapter?**
Create `adapters/<tool>.sh`, call `adapters/lib/modules.py` with your tool name, add it to `install.sh`, and open a PR.
