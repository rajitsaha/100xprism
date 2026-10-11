# macOS Incremental Token Economics — Design

**Date:** 2026-10-10
**Status:** Approved in brainstorming, pending written-spec review
**Platform:** macOS first; Windows and Linux display a coming-soon message

## Problem

`100xprism tokens` currently discovers and parses local agent transcripts only after
the dashboard process starts. A first run can examine hundreds of Claude Code and
Codex files, then spend substantially longer joining those sessions to repository
outcomes. Until the whole build completes, the browser has no durable dataset to
show. JSON adapter caches avoid some reparsing, but they do not provide one coherent
history, a durable scan checkpoint, or a searchable session index.

The desired product behavior is different:

- collection happens automatically in the background;
- every collection is incremental, down to the last completely parsed record;
- SQLite preserves historical economics and the scan state of every source location;
- the dashboard opens from the last valid snapshot immediately;
- users can see measured tokens, cached tokens, cache effectiveness, and scan coverage;
- optional local full-text search finds session content without a vector database;
- idle CPU and memory remain negligible.

## Goals

1. Make token economics continuously available on macOS without keeping a Python
   collector resident.
2. Parse only newly appended transcript records during normal operation.
3. Preserve historical session summaries when source transcripts disappear.
4. Make scan coverage explicit: each source location is fully scanned, incrementally
   current, scanning, degraded, or not yet scanned.
5. Serve the last complete dashboard snapshot immediately while a newer collection is
   running.
6. Add privacy-conscious, opt-in session-content search using SQLite FTS5.
7. Keep installation, update, npm upgrade, uninstall, and purge behavior reversible
   and ownership-proven.

## Non-goals

- Windows or Linux background-service support in this release.
- Docker, a database server, a vector database, embeddings, or network services.
- Cross-machine synchronization.
- Storing raw tool results, terminal logs, files read by an agent, or secrets-prone
  payloads in the search index.
- Reconstructing data that no longer exists before the first successful scan.
- Exact provider billing. Costs remain list-price estimates based on locally reported
  counters.

## Architecture

The feature has two short-lived processes and one local database:

1. A macOS LaunchAgent invokes a one-shot collector once per minute and at login. The
   collector exits after every run. It is not a permanently resident daemon.
2. `100xprism tokens` starts the existing localhost dashboard server on demand. The
   server reads SQLite immediately, triggers a collection if one is not active, and
   exits after ten minutes without browser or API requests.
3. `~/.100xprism/token-economics.sqlite3` is the source of truth for source-file
   checkpoints, durable session summaries, scan coverage, optional search content,
   and complete dashboard snapshots.

The collector and server are separate so collection consumes zero resident memory
between scheduled runs. SQLite uses WAL mode, allowing the server to read the last
committed data while the collector writes a new transaction.

### Components

- `scripts/_token_store.py` owns schema creation, migrations, transactions, scan
  leases, per-file checkpoints, summaries, FTS state, and snapshots. No UI or parser
  logic lives here.
- `scripts/token-collector.py` inventories configured source locations, selects files
  requiring work, invokes tool-specific incremental parsers, records scan progress,
  and publishes the next complete snapshot.
- `scripts/adapters/*.py` expose incremental parser entry points in addition to their
  current compatibility scan API. Each parser consumes complete new records plus its
  saved parser state and returns a summary delta, new state, and committed byte offset.
- `scripts/token-dashboard.py` serves SQLite-backed data and the browser UI. It does
  not scan transcript contents on HTTP request paths.
- `lib/dashboard.js` manages the owned LaunchAgent, starts the on-demand dashboard,
  triggers an immediate collection, and provides unsupported-platform messaging.

## SQLite data model

The database directory is created with mode `0700`; the database and sidecar files are
user-only. Schema migrations run transactionally and are keyed by `PRAGMA
user_version`.

### `source_locations`

One row per tool-owned root, for example `~/.claude/projects` or
`~/.codex/sessions`.

- `id`, `tool`, and canonical `root_path` identify the source.
- `status` is `never_scanned`, `scanning`, `current`, or `degraded`.
- `last_scan_started_at` and `last_scan_finished_at` describe the latest attempt.
- `last_full_scan_at` advances only after a complete inventory and successful handling
  of every changed file.
- `file_count`, `present_file_count`, and `last_error` support UI coverage reporting.

The uniqueness key is `(tool, root_path)`.

### `source_files`

One row per discovered transcript or activity file.

- `location_id`, canonical `path`, device/inode identity, size, and nanosecond mtime
  determine whether a file is unchanged, appended, replaced, or truncated.
- `parsed_offset` is the byte position immediately after the last committed complete
  record.
- `prefix_digest` detects a rewrite that retains or increases file size.
- `parser_version` invalidates only summaries owned by a parser whose semantics
  changed.
- `parser_state_json` stores minimal continuation state, such as the current Codex
  model and cumulative counters or Claude attribution boundaries.
- `session_key` joins the file to its durable summary.
- `present`, `last_seen_run_id`, and `last_error` preserve history without pretending a
  missing source is still live.

The uniqueness key is `(location_id, path)`.

### `session_summaries`

One durable aggregate per tool/session identity. It stores the normalized economics
shape already consumed by the dashboard: token categories, models, days, projects,
skills, composition, subagent split, message counts, and first/last activity. The
aggregate is JSON because the shape differs by tool and evolves more quickly than the
stable indexing columns.

Deleting or losing a source file marks its summary as historical; it does not subtract
past economics. Rebuilding a replaced file atomically replaces only that session's
aggregate.

### `scan_runs`

Records operational facts for recent runs: status, timestamps, locations and files
inspected, files parsed, bytes parsed, sessions updated, and the terminal error. The
collector retains the newest 1,000 rows and removes older operational rows. Economic
session history is not subject to this cap.

### `dashboard_snapshots`

Stores the latest two complete dashboard JSON payloads with creation time and source
scan ID. A new row is committed only after collection and aggregation succeed. Keeping
the previous row makes rollback and corruption recovery straightforward.

### `session_content` and `session_content_fts`

Content search is disabled by default. When enabled, `session_content` stores only
human prompt text and assistant prose with session, role, timestamp, and sequence.
Tool results, terminal output, files read, and structured tool payloads are excluded.
An FTS5 virtual table indexes that content for keyword, phrase, prefix, and Boolean
search with BM25 ranking and highlighted excerpts.

`100xprism tokens --disable-content-search` drops both content tables and runs an
incremental vacuum when appropriate. Economic summaries remain intact.

## Incremental collection protocol

### Location inventory

Every run inventories file metadata under each supported source root. Metadata checks
do not open unchanged files for content parsing. A source location becomes `current`
only when the complete inventory finishes and every changed file either commits or is
explicitly recorded as an error. Any file error leaves the prior valid summary in
place and marks the location `degraded`; `last_full_scan_at` does not advance.

### Append-only JSONL

Claude Code, Codex, and Pi transcripts normally append records.

1. Seek to `parsed_offset` in binary mode.
2. Process only newline-terminated records. An incomplete trailing record is left for
   the next run, and its bytes are not included in the committed offset.
3. Apply records to the saved parser state and session aggregate in memory for that
   file only.
4. Commit the aggregate, parser state, offset, and optional FTS rows in one SQLite
   transaction.

Claude continuation state includes command/skill attribution boundaries and tool-use
name mappings needed by later tool results. Codex continuation state includes the
current model and the last cumulative token counters. Parser-version changes rebuild
only files owned by that parser.

### Rewrite and truncation handling

A smaller size, changed file identity, failed prefix digest, or offset beyond EOF
causes a single-file rebuild from byte zero. Its old summary and content rows remain
visible until the replacement parse commits. Other sessions are unaffected.

### Non-append-only sources

Cursor and Antigravity activity formats are reparsed only when their metadata changes.
Their replacement summary commits atomically. They continue to report activity without
inventing unavailable token counters.

### Deleted and moved files

Files not seen in a completed inventory are marked absent. Their summaries remain
historical. If a transcript reappears with the same stable session identity, the
collector updates the existing session rather than double counting it. Ambiguous moves
are treated as a new file and deduplicated by tool/session identity after parsing.

### Concurrency and failure safety

A process lock under `~/.100xprism/token-collector.lock` prevents scheduled, manual,
and install-triggered collectors from overlapping. The lock is held only for the
one-shot collection. SQLite transactions are short and scoped per file; snapshot
publication is a separate atomic transaction. A terminated collector therefore leaves
the last complete snapshot and every previously committed file checkpoint readable.

## macOS lifecycle

The owned file is
`~/Library/LaunchAgents/com.100xprism.token-economics.plist`. It contains an explicit
generated marker and points to the stable `~/100xprism` clone, never the transient npm
package directory.

The LaunchAgent uses `RunAtLoad`, `StartInterval=60`, background process type,
low-priority I/O, and a positive nice value. It does not use `KeepAlive`. `launchd`
does not overlap invocations of the same job.

- `100xprism install` bootstraps the stable clone, writes or refreshes the plist,
  bootstraps it into the current GUI domain, and triggers the first collection.
- `100xprism update` pauses the job, updates code, runs migrations through the new
  collector, rewrites the plist, and restarts it even when the repository was already
  current.
- npm `preuninstall` during an upgrade boots out the running job but preserves its
  plist and database. npm `postinstall` restarts an existing owned plist; it does not
  opt a new user into token economics or clone a repository.
- `100xprism uninstall` boots out and removes the owned plist. It retains the database
  so reinstalling restores history.
- `100xprism uninstall --purge` also removes the SQLite database, WAL/SHM files,
  process lock, and bounded diagnostics.
- `100xprism tokens` ensures the owned job is loaded, triggers a non-overlapping
  collection, starts the on-demand server if necessary, and opens the browser unless
  `--no-open` was supplied.

On Windows and Linux, token-economics commands print: `Token economics is coming soon
for this platform.` Existing non-dashboard commands continue to work.

## Resource budgets

- Collector resident memory is zero between scheduled runs.
- Unchanged runs perform metadata inventory only and do not rebuild dashboard
  aggregates.
- A collector processes one changed file at a time and does not retain raw transcript
  content after committing it.
- The dashboard server is the only resident Python process while the UI is open. It
  exits after ten minutes without browser/API traffic.
- Git/value recomputation is limited to affected project paths plus a bounded periodic
  refresh; unchanged repositories use the existing value cache.
- Collector logs rotate at 1 MiB with two backups. Scan diagnostics in SQLite are
  capped independently.

These are architectural budgets rather than machine-specific RSS promises. Tests pin
the absence of unnecessary parsing and resident collector behavior; they do not assert
an unstable operating-system memory number.

## Dashboard and API

The browser renders the latest complete snapshot on its first request. A compact live
status banner reads current scan state separately, so partial work never masquerades as
completed totals.

New coverage and economics surfaces include:

- per-location state, file coverage, last full scan, changed files, bytes parsed, and
  latest error;
- input, output, cache-read, and cache-write tokens;
- cache-hit ratio, cached-context share, and estimated cache savings;
- usage and cost by session, repository, tool, model, day, skill, and main versus
  subagent;
- largest sessions and sessions with unusually high context growth;
- opt-in session search with filters for tool, repository, role, and date.

The no-cache counterfactual prices cache-read tokens at the corresponding model's
ordinary input rate and labels the result an estimate. It does not claim provider-bill
reconciliation.

Search endpoints return session metadata and short highlighted excerpts, never entire
transcripts by default. Queries are parameterized and bounded by result count.

## Privacy and security

- All data remains local; the feature performs no network calls.
- The HTTP server binds only to `127.0.0.1`.
- Content search is opt-in and its scope is stated before activation.
- Disabling content search deletes persisted searchable text without deleting economic
  history.
- Generated plist deletion requires the exact label and ownership marker.
- Database SQL uses parameters, and FTS queries are validated and bounded.
- Logs contain paths and counts only, not transcript content.

## Testing strategy

### Store and migration tests

- Create, reopen, and migrate a database transactionally.
- Reject an unsupported future schema without modifying it.
- Verify WAL mode, permissions, snapshot rollback, scan-history cap, and FTS removal.

### Incremental parser tests

- A first scan parses a fixture fully.
- A second unchanged scan reads no transcript content.
- Appending complete records parses only bytes after the committed offset.
- An incomplete trailing JSONL record remains uncommitted until completed.
- Truncation, replacement, prefix mismatch, and parser-version change rebuild only the
  affected file.
- Claude attribution and Codex cumulative counters remain correct across scan
  boundaries.
- Deleted transcripts retain historical totals and reappearing session identities do
  not double count.

### Collector tests

- All configured locations expose explicit coverage state.
- One malformed changed file preserves its previous summary and marks the location
  degraded.
- The collector lock makes a concurrent invocation exit successfully without work.
- A failed snapshot build leaves the previous snapshot readable.
- Unchanged inventory does not invoke aggregation.

### Lifecycle tests

- Install writes an owned macOS plist and triggers collection.
- Update stops, migrates, rewrites, and restarts the job.
- npm upgrade pauses and resumes only a previously installed job.
- Ordinary uninstall removes the agent but retains history.
- Purge removes the agent, database, sidecars, locks, and logs.
- Hand-written or foreign LaunchAgent files are never deleted.
- Windows and Linux return the coming-soon message without mutating state.

### Dashboard tests

- A saved snapshot renders before collection completes.
- Live status is distinct from completed totals.
- Cache metrics and counterfactual savings use the correct model rates.
- FTS is unavailable while disabled and returns filtered, bounded excerpts while
  enabled.
- Ten minutes without API activity shuts down the on-demand server; active polling
  keeps it alive.

## Rollout and compatibility

The first collector imports existing adapter JSON caches only as an optimization hint;
SQLite remains authoritative after a successful file parse. Existing caches are not
deleted in this release, making rollback safe. The old direct `--print` report can read
the SQLite snapshot and trigger a one-shot collection when no snapshot exists.

The schema is versioned from its first release. Adapter parser versions are independent
so a format fix does not force a global rebuild.

## Success criteria

- After one completed scan, `100xprism tokens` shows historical data immediately.
- With no transcript changes, scheduled collection performs no content parsing.
- Appending one transcript record advances only that file's offset and aggregate.
- Every source location reports an honest full-scan or degraded state.
- Cached-token totals and estimated savings are visible and traceable by session/model.
- Content search works locally when enabled and leaves no indexed text after disable.
- No collector process remains resident between launchd intervals.
- Install/update restart collection; uninstall stops it; purge removes its data.

