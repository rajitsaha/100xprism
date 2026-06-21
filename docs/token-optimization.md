# Token usage: audit, optimization & monitoring

A review of the installed Claude Code footprint (plugins, skills, hooks, MCP
servers, subagents) for duplication and token cost, plus a local dashboard to
monitor usage. Numbers below were measured from local transcripts
(`~/.claude/projects/**/*.jsonl`).

## TL;DR

- **93% of input tokens are cache reads** — the fixed context (system prompt +
  tool/skill/agent descriptions + SessionStart injections) re-sent on every turn.
  The lever is shrinking that fixed block and cutting redundant tool surface.
- **Startup context** measured at **median ~13K / avg ~26K tokens** before you
  type anything — much of it duplicated capability.
- Four overlapping **memory systems** were running at once; the claude-mem
  observer alone accounted for ~1.5B input tokens of history.

## The four token "purposes"

| Bucket | What it is | Relative $/token |
|---|---|---|
| **cache read** | Fixed context + history re-sent each turn, served from cache | 0.1× (cheap/token, huge volume) |
| **cache write** | Context written to cache on a miss / when it changes | 1.25× |
| **input** | New uncached text per turn (your messages, fresh tool results) | 1× |
| **output** | Model generations | 5× (costliest/token) |

Cost is dominated by **fixed-context size × number of turns**, not by what you type.

## Duplication & conflicting capabilities found

Overlaps span the whole installed ecosystem, not just this repo's modules:

| Capability | Competing implementations |
|---|---|
| Memory | claude-mem (MCP + observer + SessionStart dump) · remember plugin · `.remember/` · `MEMORY.md` |
| Code review | code-review plugin · pr-review-toolkit (7 agents) · code-simplifier · this repo's `grill-me`/`pr`/`commit` · superpowers review skills |
| Planning / orchestration | `orchestrate`,`spec` · superpowers planning skills · claude-mem `make-plan`/`do` · ralph-wiggum · built-in Workflow/Plan mode |
| Browser automation | playwright MCP **+** chrome-devtools-mcp (~55 tool schemas combined) |
| UI / design | frontend-design · ui-ux-pro-max · this repo's `visual-system-architect`/`interaction-engineer`/`motion-designer` |
| Debugging / TDD | `fix-bugs` vs superpowers `systematic-debugging`; `test` vs `test-driven-development` |

Within this repo's `modules/`, the only genuine duplicates were two pairs, now
**merged**:

- `systems-architect` → **`enterprise-design`** (the latter is a strict superset).
- `conversion-copy` → **`copywriting`** (folded in as a "Full-Page Mode" section;
  `figma-translator` repointed accordingly).

## Changes applied

### Live environment (`~/.claude/settings.json`, backed up first)

Disabled globally (re-enable per-project via a project `.claude/settings.json`):

| Plugin disabled | Why |
|---|---|
| `claude-mem` | Kept `remember` + `.remember/` instead; removes the ~15K SessionStart injection and the observer |
| `chrome-devtools-mcp` | Kept `playwright`; one browser stack is enough |
| `pr-review-toolkit` | 7 verbose agents loaded every session; overlaps the repo's own review path |
| `ui-ux-pro-max` | Overlaps `frontend-design` + this repo's design modules |

> Restore anytime: `cp ~/.claude/settings.json.bak.<timestamp> ~/.claude/settings.json`.
> A claude-mem observer process may still be running from before — it won't be
> relaunched in new sessions.

### This repo

- Merged the two duplicate module pairs (above); module count 68 → 66,
  auto-trigger skills 42 → 40. Counts synced across README/AGENTS/USAGE/install/package.
- Removed dead entries from `scripts/trigger-overlap-allow.txt`.
- Added `scripts/token-dashboard.py` (below).

### Update propagation (so removals actually reach users)

A merge/removal is only useful if `100xprism update` cleans up the old artifacts.
Two gaps were fixed so it does:

- **Claude Code skills + slash aliases now prune.** `emit-claude-code` writes a
  per-skill marker + a manifest, then removes any skill/alias it previously
  emitted that no longer exists (e.g. `systems-architect`, `conversion-copy`) —
  while never deleting the user's own hand-authored skills/commands. (Cursor and
  Codex emitters already pruned via markers.)
- **Plugins now add *and* remove.** `adapters/lib/sync_plugins.py` (used by both
  install and update) adds newly-declared plugins and removes ones 100xprism
  previously installed but has since dropped from `plugins.json`, without
  touching plugins the user enabled themselves or flipping a value they set. The
  managed set is tracked in a sidecar beside `settings.json`.

Single-file tool configs (Codex `AGENTS.md`, `.windsurfrules`,
`copilot-instructions.md`, `GEMINI.md`, `ANTIGRAVITY.md`) are regenerated
wholesale on update, so removed modules simply stop appearing. 100xprism does not
generate `CLAUDE.md` — it scaffolds an editable project file once and leaves it
to you.

## Further recommendations (not yet applied)

1. Move rarely-used **user-scoped plugins to project scope** (`understand-anything`,
   `vercel`) so they don't load for every project.
2. Disable the `google-drive-write` MCP server if unused.
3. Pick **one reviewer** and **one planner** path to reduce ambiguity + description weight.
4. Operational habits: `/context` to see the live window, `/clear` between unrelated
   tasks, and push big exploration into subagents to keep the main context lean.

## Local monitoring

### Built-in
- `/context` — live breakdown of what's filling the window right now.
- `/cost` — session tokens + cost.

### This repo's dashboard

```bash
python3 scripts/token-dashboard.py          # web UI at http://127.0.0.1:8787
python3 scripts/token-dashboard.py --print  # text summary, no server
```

Offline, no dependencies. Reads `~/.claude/projects/**/*.jsonl` and shows the four
token purposes, a **startup-bloat meter** (fixed context re-sent per turn), and
breakdowns by project / model / day. First run scans all transcripts (slow); later
runs use an incremental on-disk cache (`~/.claude/.token-dashboard-cache.json`).
Edit the `RATES` table at the top of the script to match your plan for the cost estimate.

### Other options
- `npx ccusage@latest` and `npx ccusage@latest blocks --live` — terminal dashboards.
- OpenTelemetry (`CLAUDE_CODE_ENABLE_TELEMETRY=1` + an OTLP exporter) → Prometheus +
  Grafana for a persistent web dashboard graphing input/output/cache over time.

> No tool attributes tokens to a specific skill/plugin; that granularity only comes
> from `/context` (current window) or building it from the raw transcripts.
