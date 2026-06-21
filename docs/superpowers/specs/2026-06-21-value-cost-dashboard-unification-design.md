# Value × Cost — unified dashboard

**Date:** 2026-06-21
**Status:** Approved design, pre-implementation

## Problem

The README promises:

> `100x-value` — tokens measure *cost*; this measures *value*: the features
> actually shipped **for that spend**, read side by side.

The current `scripts/value-report.py` does **not** deliver this. It is a
CHANGELOG + git summarizer that reads **zero** token data — its own docstring
says "to read *alongside* token cost." Cost and value never touch: no shared
key, no per-release cost, no side-by-side. The "for that spend" half is
unimplemented.

Two further defects:

1. **Tag double-count.** `git_summary()` derives the "unreleased" boundary from
   `git describe --tags`. When the CHANGELOG moves ahead of git tags (latest tag
   `v2.3.1`, but CHANGELOG documents `v2.3.2`/`v2.3.3` as shipped), the already
   released commits show up *both* as "Shipped" (from CHANGELOG) and as
   "Unreleased work" (from git). Same work counted twice.
2. **Stale dashboard.** `100x-tokens` builds its data once at startup and never
   refreshes unless you click Rescan; a long-lived tab silently goes stale.

## Goals

- A single URL (`100x-tokens`, `127.0.0.1:8787`) shows **cost next to value**.
- Value + cost persisted in **one central, machine-wide store**.
- Fix the tag double-count so the ledger is honest.
- Dashboard auto-refreshes so it stops going stale.
- Update the README so the promise matches reality.

## Non-goals

- No exact per-feature cost. Attribution is date-windowed and **labelled an
  estimate**, like the existing composition meter.
- No browser/E2E tests for the new panel.
- No change to how transcripts are parsed or how token cost rates are set.

## Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Repo discovery | **Hybrid** — registry is source of truth; dashboard also auto-discovers git repos from transcript labels |
| Central store | `~/.100xprism/value.json` |
| Cost attribution | Shown per release, **labelled "estimate — attributed by date window"** |
| Freshness | **Auto-refresh every 5 min** (server rebuild thread + client poll) |

## Architecture

### Components

1. **`scripts/_shipped.py`** *(new shared helper)* — single source of truth for
   "what shipped" parsing. Exposes:
   - `parse_changelog(path, limit) -> [release]` (moved from `value-report.py`)
   - `git_summary(repo) -> {since, count, buckets}` with the **fixed boundary**
   - `repo_value(repo_path, versions) -> {name, label, releases, unreleased}` —
     the full per-repo value snapshot, including the precomputed dashboard
     `label` (join key)
   - `project_label_for_path(abs_path) -> str` — replicates the dashboard's
     path→label mangling so registry entries join correctly
   - Store I/O: `load_store()`, `save_repo(entry)`, `STORE_PATH`

2. **`scripts/value-report.py`** *(slimmed)* — CLI front end. Calls
   `_shipped.repo_value()`, prints the report (unchanged output), and writes the
   repo's snapshot to the central store (registry). Keeps `--versions`, repo arg.

3. **`scripts/token-dashboard.py`** *(extended)* —
   - `build()` gains a `by_project_day` aggregate: `{label: {day: token-dict}}`.
   - New `value_panel(data)` builds the cost↔value join from the store +
     `by_project_day`.
   - `/api/value` route returns the joined panel data; the page renders a
     **"Value — cost vs. what shipped"** section.
   - Background daemon thread rebuilds `Handler.data` every 300 s.
   - Client `setInterval` polls `/api/data` every 5 min and re-renders, with an
     "updated HH:MM" stamp.

### Data flow

```
100x-value (in repo)
  └─ _shipped.repo_value(cwd) ──► print report
                              └─► save_repo() ──► ~/.100xprism/value.json
                                                        │
100x-tokens (singleton server)                          │
  ├─ build() ──► by_project_day (cost per label × day)  │
  ├─ load_store() ◄─────────────────────────────────────┘
  ├─ auto-discover git repos from transcript labels (source:"auto",
  │    registry entries win on conflict)
  └─ value_panel(): for each repo release, sum its label's cost across
       [prev_release_date … release_date]  ──►  /api/value  ──►  panel
```

### The cost↔value join

- Map a repo's store `label` to the dashboard's `by_project_day` key (identical
  mangling, so they match).
- Releases are date-sorted (newest first). For release *i* with date `d_i` and
  predecessor date `d_{i-1}`, estimated cost = Σ cost of that label over days in
  `(d_{i-1}, d_i]`. The newest release's window opens at the previous release's
  date; **unreleased** = days after the newest release date through today.
- Releases with no parsable date are listed without a cost figure.
- The whole panel is captioned **"estimate — attributed by date window"**.

### Tag double-count fix

`git_summary()` boundary becomes the **most recent `chore(release):` commit**
(`git log --grep='^chore(release)' -n1 --pretty=%H`), with `git describe --tags`
as fallback when no such commit exists. Commits after that boundary are the only
ones reported as unreleased, so changelogged versions no longer reappear.

### Central store schema — `~/.100xprism/value.json`

```json
{
  "version": 1,
  "repos": {
    "/abs/path/to/repo": {
      "name": "repo",
      "label": "~/personal/github/repo",
      "scanned": "2026-06-21T12:00:00",
      "source": "registry",
      "releases": [
        {"version": "2.3.3", "date": "2026-06-21",
         "sections": {"Added": ["…"], "Changed": ["…"]}, "items": 3}
      ],
      "unreleased": {"count": 7, "buckets": {"feat": 2, "docs": 2, "other": 3}}
    }
  }
}
```

Writes are last-writer-wins per repo key. On dashboard rebuild, an `auto` entry
never overwrites a `registry` entry for the same path.

### Auto-refresh

- **Server:** daemon thread loops `Handler.data = build(verbose=False)` every
  300 s. `build()` is incremental (mtime/size cache), so the steady-state cost is
  near zero. Guard with a lock so a manual `/api/refresh` and the timer don't
  race.
- **Client:** `setInterval(load, 300000)` re-fetches `/api/data` (+ `/api/value`)
  and re-renders. Header shows `updated HH:MM:SS`. Manual Rescan button stays.

## Error handling

- Missing `~/.100xprism/` → created on first `save_repo()`. Missing/corrupt
  `value.json` → treated as empty store, dashboard renders without the panel
  (no crash).
- Repo with no CHANGELOG and no tags → `repo_value` returns empty releases;
  `100x-value` prints the existing "No CHANGELOG.md found" message.
- A label in the store with no matching `by_project_day` cost → release rows
  render with value but a blank `$` cell (no cost data for that window).
- Git/subprocess failure → `git_summary` returns `None` (existing behavior).

## Testing (`scripts/test_value.py`, `unittest`)

1. `parse_changelog` — multi-release fixture, section/bullet counts.
2. **Tag-lag regression** — CHANGELOG ahead of tags + a `chore(release)` commit
   ⇒ no released version appears as unreleased.
3. `chore(release)` boundary selection (and tag fallback when absent).
4. Date-window cost join — synthetic store + synthetic `by_project_day`
   ⇒ each release gets the expected windowed cost; unreleased gets the tail.
5. Store round-trip — `save_repo` then `load_store`; `auto` does not clobber
   `registry`.
6. `project_label_for_path` matches `token-dashboard.project_label` for the same
   path.

## Modules touched

| File | Change |
|---|---|
| `scripts/_shipped.py` | **new** — shared changelog/git/store helper |
| `scripts/value-report.py` | slim to CLI + registry write; use helper |
| `scripts/token-dashboard.py` | `by_project_day`, `/api/value`, value panel, refresh thread + client poll |
| `scripts/test_value.py` | **new** — unittest coverage above |
| `README.md` | rewrite "Token & value economics" to describe the unified dashboard |
| `shell/aliases.sh` | unchanged (aliases already point at both scripts) |
