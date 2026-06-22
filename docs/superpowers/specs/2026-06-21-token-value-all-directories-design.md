# Token + Value, All Directories — Design

**Date:** 2026-06-21
**Status:** Approved (brainstorming), pending spec review
**Supersedes:** the registry/CHANGELOG-gated "Value — cost vs. what shipped" panel (v2.3.3–v2.3.4)

## Problem

The token dashboard (`scripts/token-dashboard.py`) already knows **every** directory
that consumed tokens (from `~/.claude/projects/**/*.jsonl`). But the **value** half —
"what did that spend buy?" — is gated behind a manual `100x-value` registration step
and a `CHANGELOG.md` requirement. So most directories show nothing or `$0`, and the
view answers "what did *this registered repo* ship" rather than the question the user
actually asked: **"across all directories where I spent tokens — repo or not — where did
they go and what did they buy?"**

## Goal

One automatic view over every directory that consumed tokens. No registration, no
CHANGELOG requirement. Cost on one axis, delivered value (git + filesystem activity) on
the other, with graphical distribution and short AI summaries. Tool-agnostic where the
data allows.

## Key insight (drives the architecture)

Cost and value fall on different data sources:

- **Cost** is per-tool and lives in each agent's own logs. Only **Claude Code** writes
  per-message token accounting locally (`input / output / cache_read / cache_write` in
  JSONL). Verified on this machine: **Antigravity** transcripts
  (`~/.gemini/antigravity/.../transcript.jsonl`) carry *no* token counts; its
  conversations are protobuf/SQLite and opaque. **Cursor** usage is server-side.
  **Codex** CLI *does* write `~/.codex/sessions/*.jsonl` rollouts (newer versions with
  token-count events) but is not installed here.
- **Value** is tool-agnostic. Git history and filesystem mtimes don't know — or care —
  which agent (or human) produced a change. A commit made by Codex, Antigravity, or a
  person is mined identically.

Therefore: **two independent layers**, joined on the resolved real directory path. The
value layer covers Codex/Cursor/Antigravity "for free"; the cost layer is a pluggable
adapter that today only has a real Claude Code implementation.

## Architecture

### Cost layer — pluggable adapters

A single interface, one real implementation, one stub:

```
adapter.iter_usage() -> yields (real_dir_path, day, input, output, cache_read, cache_write)
```

- **`ClaudeCodeAdapter`** — today's `~/.claude/projects/**` reader, refactored out of
  `token-dashboard.py` into an adapter. Behavior unchanged; this is the only adapter
  that produces real numbers in v1.
- **`CodexAdapter`** — documented **stub** (reads `~/.codex/sessions/*.jsonl` when
  present). Not wired up / not relied on in v1; placeholder for when Codex is used
  locally.
- Cursor / Antigravity — **no adapter**. No local token data exists to read. Their
  directories still appear via the value layer with cost rendered `—`.

The dashboard's existing aggregations (`by_project`, `by_project_day`, composition,
cost rates) consume the merged adapter stream. Net behavior for Claude-only users is
identical to today.

### Value layer — tool-agnostic, automatic (replaces registry logic in `_shipped.py`)

For each **resolved real directory** that appears in the cost layer (and any extra git
repos discovered there):

1. **Resolve** the transcript dir name to a real path (`-Users-rajit-foo-bar` →
   `/Users/rajit/foo/bar`) — extend the existing un-mangle logic in `_shipped.py`.
2. **Window** = that directory's token-spend date range (first → last day of cost),
   reused from `by_project_day`. Falls back to "last 30 days" for value-only dirs that
   have no cost.
3. **Git repo** → mine `git log --since/--until` for the window: commit count, merged
   PRs (`(#NN)` in subjects + merge commits), files changed, insertions/deletions, top
   subjects. Cache keyed by `HEAD sha + window` so unchanged repos aren't re-mined.
4. **Non-repo (or path gone)** → filesystem fallback: count files created/modified
   (mtime) within the window, gitignore-/extension-aware to skip build artifacts and
   `node_modules`. Labeled an estimate.
5. **AI summary** → one line describing what the window accomplished (see below).

`value.json` is repurposed from a manual **registry** into an automatic **cache** of
value snapshots, keyed `dir + window-hash`. The `source: registry/auto` distinction is
removed.

### AI summaries — offline, cached, non-blocking

A **separate background pass** (after the cost+value rebuild) shells out to the local
`claude` CLI (`/Users/rajit/.local/bin/claude`), feeding it the git subjects / file
list for each window, and writes the one-liner into the cache. If the CLI is absent or a
call fails, the row shows git/file facts with no summary — graceful degradation. The
serve path stays zero-dependency and never blocks on this.

### Serve path & refresh

Unchanged operationally: zero-dependency `http.server`, offline. Git + value derivation
run on the existing 5-minute background rebuild tick (off the request path); AI summaries
are a slower follow-on pass. `/api/value` returns per-directory cost+value snapshots.

### Join & display rule

Both layers key on the **resolved real directory path**:

- Claude cost + git value → both shown.
- Value-only dir (Antigravity/Cursor/human-only repo) → value shown, cost `—` with a
  small tool/source badge, never `$0`.

## UI / UX

Developer-facing local analytics instrument. Thesis = **leverage** (the project's own
rebrand: small effort → 100× outcome), so the hero is the cost↔value *relationship*, not
a number grid. Deliberately avoids the generic AI-dashboard defaults (cream/serif/
terracotta; black + acid-green; hairline broadsheet).

### Design tokens

- **Palette — "ledger at dusk," dark-first:**
  `--ink:#0E1116` (bg) · `--surface:#171B22` · `--line:#262C36` ·
  `--text:#E6E9EF` · `--muted:#8A93A2` ·
  `--cost:#E8B24A` (amber = spent) · `--value:#5BD0A6` (green = shipped) ·
  `--warn:#E5704B` (high-cost / low-output).
  **One semantic rule, applied everywhere: amber = cost, green = value.** The eye learns
  it once and reads every chart faster.
- **Type:** body `IBM Plex Sans`; every number / `$` in `IBM Plex Mono` with **tabular
  figures** (prevents column jitter — ui-ux `number-tabular`). One display weight for
  section headers only.
- **Signature element — the leverage line:** in the value-vs-cost scatter, a diagonal
  break-even reference line. Dots above (high value per token) glow green; dots below
  (tokens burned, little shipped) glow warn-red. This chart *is* the brand thesis and the
  single memorable element; everything else stays quiet.

### Layout (desktop; single-column reflow on mobile)

```
┌────────────────────────────────────────────────────────────┐
│  Claude Code · Token Economics      updated 13:19  ↻ Rescan  │
│  10.3B cache-read · 107.8M output · $38,008  ·  47 dirs      │
├───────────────────────────────┬────────────────────────────┤
│  LEVERAGE  (value vs cost)     │  COST OVER TIME            │
│   value▲      · ·  ◍ high-lev  │   ▁▂▃▅▇▆▃▂  stacked by dir │
│        │    ╱ break-even       │                            │
│        │  ╱ ◍ ◍                │  TOKEN-PURPOSE SPLIT       │
│        │╱◍   ● burn            │   ▇▇▇▇▇░░  cache/out/in    │
│        └──────────► cost       │                            │
├───────────────────────────────┴────────────────────────────┤
│  ALL DIRECTORIES                          sort: cost ▾       │
│  dir            tool   cost ▾   value (shipped)    AI note   │
│  100xprism      ◆CC   $1,204   12 commits·3 PRs   "token…"  │
│  hippokit       ◆CC   $9,880   84 commits         "MCP…"    │
│  antigravity-x  ◇AG      —     6 commits          "—"       │
└────────────────────────────────────────────────────────────┘
```

### Charts (all four, hand-rolled inline SVG — zero dependency, matches today's `bar()`)

1. **Leverage / value-vs-cost scatter** (hero) — each dir a point: cost x, value y, with
   break-even diagonal.
2. **Cost over time** — stacked area per day, by directory.
3. **Token-purpose split** — input / output / cache-read / cache-write.
4. **Cost by directory** — ranked bars (secondary, also drives the table sort).

Every chart: legend always visible; tooltips on hover **and** keyboard focus;
focusable data points; an `aria-label` / text summary of the chart's key insight;
`prefers-reduced-motion` honored. (ui-ux Charts §10 + Accessibility §1.)

### Removed

The registry-gated "Value — cost vs. what shipped" panel, the manual `100x-value`
registration requirement, and the `source: registry/auto` field. `value-report.py` /
`100x-value` become a thin CLI over the same automatic derivation (print a directory's
cost+value snapshot; no "register" step).

## Components & boundaries

- `scripts/adapters/claude_code.py` — `iter_usage()` over `~/.claude/projects`. (Refactor
  of existing reader.) Testable in isolation against a fixture transcript dir.
- `scripts/adapters/codex.py` — stub `iter_usage()`; documented, not wired in v1.
- `scripts/_value.py` (renamed/rewritten `_shipped.py`) — path resolution, git mining,
  fs-mtime fallback, value-snapshot cache (`value.json`). No CHANGELOG, no registry.
- `scripts/_summaries.py` — background AI-summary pass over the local `claude` CLI;
  cache read/write; pure-degradation when CLI absent.
- `scripts/token-dashboard.py` — consumes adapters + value layer; renders charts + the
  unified all-directories table; `/api/value`.
- `scripts/value-report.py` — thin CLI over `_value.py`.

## Testing

- Adapter: fixture `~/.claude/projects`-shaped dir → expected `iter_usage` tuples.
- Value layer: a throwaway git repo fixture (commits in/out of window) → expected
  commit/PR/file counts; a non-repo dir with known mtimes → expected fs-fallback counts;
  window-hash cache hit/miss.
- Summaries: CLI-present (mocked) and CLI-absent → graceful no-summary.
- Dashboard: `--print` snapshot includes all-dirs rows with `—` for cost-less dirs;
  `/api/value` shape. Existing 6 Python tests stay green; Claude-only output unchanged.

## Out of scope (YAGNI)

- Real Cursor / Antigravity cost adapters (no local data exists).
- Wiring the Codex adapter (build when Codex is actually used locally).
- Cross-machine aggregation; historical backfill beyond what logs already hold.

## Open questions

None blocking. Both prior decisions resolved: unified table with `—` for cost-less
dirs; leverage scatter as hero.

---

## Addendum (2026-06-22): Machine-wide marker-file discovery

**Problem:** Directories were sourced only from `~/.claude/projects` transcript dirs, and un-mangling those lossy names fails for ~⅔ of them (hyphenated real dirs). So many projects show no value, and projects worked on with *other* tools (or where transcripts were pruned) never appear at all.

**Fix:** Discover agentic project directories by walking the filesystem for **agent marker files** — tool-agnostic, authoritative real paths, no un-mangling.

- `_value.AGENT_MARKERS = ("CLAUDE.md","AGENTS.md","GEMINI.md",".cursorrules",".windsurfrules",".clinerules",".github/copilot-instructions.md")`
- `discover_project_dirs(root=$HOME, max_depth=6) -> {real_abs_dir: label}` — `os.walk` pruning `_SKIP_DIRS` + dotdirs + `Library`, depth-capped. A dir qualifies if it contains any marker.
- `cached_discover(ttl=1800)` — caches the walk in the value store (`discovered`, `discovered_at` epoch); re-walks only when stale or on manual Rescan. Keeps the expensive walk off most rebuild ticks.

**Directory set becomes the UNION** of transcript-derived dirs (carry token cost) and marker-discovered dirs (real path → git/fs value; `cost=None` if no token spend). Join on `project_label`: a discovered real path supplies the authoritative path for a token-spend label (fixing the unresolved-resolution gap) and also surfaces zero-token projects with `—` cost.

**Scope (decided):** scan under `$HOME`, prune heavy/hidden dirs, depth ≤6.
