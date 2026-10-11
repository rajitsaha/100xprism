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
3. `~/.100xprism/token-economics/current/token-economics.sqlite3` is the source of
   truth for source-file checkpoints, durable session summaries, scan coverage,
   optional search content, and complete dashboard snapshots. `current` is an atomic
   symlink to one owned versioned generation.

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

Store metadata includes a monotonic `snapshot_input_revision` and
`snapshot_revision`. Every durable mutation represented in the snapshot increments
`snapshot_input_revision`, including session summaries, source presence and coverage,
errors, content-backfill coverage, and value-cache refreshes. A published snapshot
records the revision it represents. Transient live scan state is queried separately and
does not enter a completed snapshot. This makes interrupted snapshot publication
recoverable without reopening transcript content.

All snapshot-relevant Git/value inputs and their format version live in the generation
database and update in the same SQLite transaction as `snapshot_input_revision`. The
pre-existing `~/.100xprism/value.json` may be read once as a backward-compatible import
hint, but the collector never writes it and snapshot correctness never depends on it.

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
- `tail_anchor` hashes the final 64 KiB immediately before `parsed_offset`; it cheaply
  rejects a false append when bytes near the prior boundary changed.
- `source_file_blocks` holds BLAKE2 hashes for immutable, complete 1 MiB blocks. The
  current partial block has a separate digest and length. Before an append, the
  collector rehashes at most that existing partial block and compares it; after enough
  bytes arrive to complete the block, it promotes the resulting digest to immutable
  and starts a new partial block.
- `integrity_cursor` identifies the next complete or partial historical block to verify
  during a separately due integrity-maintenance pass.
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
search with BM25 ranking. Searchable text has an independent `content_offset` and
`content_parser_version`; it never reuses the economics offset.

`100xprism tokens --enable-content-search` records explicit consent, creates the FTS
tables, and schedules a byte-zero content-only backfill. That backfill is bounded per
collector run and never mutates or double-counts economic summaries. The UI reports
backfill coverage separately until every present file reaches its content EOF.

`100xprism tokens --disable-content-search` deletes content rows and FTS tables with
`PRAGMA secure_delete=ON`, checkpoints and truncates the WAL, runs a full `VACUUM`,
closes the database, and removes empty WAL/SHM sidecars. Economic summaries remain
intact. This removes searchable text from the live SQLite database and sidecars; it
cannot erase copies retained by APFS snapshots, Time Machine, or other backups, which
the confirmation message states explicitly.

Enable/disable are exclusive maintenance operations, not dashboard HTTP handlers. The
CLI boots out the LaunchAgent, stops the owned dashboard server, acquires the collector
lock, waits for SQLite readers to drain, and then opens the database exclusively. On
disable it writes the search preference off before deleting content, so a failed cleanup
cannot expose the index through a restarted server. It reports success only after the
FTS tables are absent, the WAL checkpoint/truncate and full vacuum succeed, the database
is closed, and no non-empty sidecars remain. A `finally` path reloads the LaunchAgent;
failure keeps search disabled and prints the exact recovery command.

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

A smaller size, changed file identity, failed 64 KiB tail anchor, failed current-partial
digest, failed historical block verification, or offset beyond EOF causes a single-file
rebuild from byte zero. Its old summary and content rows remain visible until the
replacement parse commits. Other sessions are unaffected.

Initial scans hash each complete 1 MiB block and the remaining partial block while they
are already being parsed. A normal append verifies only the 64 KiB anchor plus the prior
partial block (at most 1 MiB), then promotes every newly completed immutable block and
stores the new partial digest. Appends below, across, and above a block boundary never
compare a growing partial digest to an immutable complete-block digest.

Historical integrity verification is not part of the one-minute unchanged fast path.
Once per day, or via `100xprism tokens --verify-history`, a maintenance run reads at
most 32 MiB of rotating complete blocks and stored partial blocks across all sources
and advances `integrity_cursor`. Partial blocks participate even when their files never
append again. This eventually detects an arbitrary older same-inode rewrite while
keeping each pass bounded. The UI distinguishes `incrementally current` from `integrity
verified` and shows the last complete verification cycle.

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

A process lock under `~/.100xprism/token-economics/collector.lock` prevents scheduled,
manual, and install-triggered collectors from overlapping. The lock is held only for
the one-shot collection. SQLite transactions are short and scoped per file; snapshot
publication is a separate atomic transaction. A terminated collector therefore leaves
the last complete snapshot and every previously committed file checkpoint readable.

At startup, the collector marks an unterminated prior `scan_run` abandoned, clears any
expired database lease after confirming that the process lock is available, and
restores each affected location to its last committed `current` or `degraded` state.
After inventory, it compares `snapshot_input_revision` with `snapshot_revision`. If they
differ—even when no transcript changed—the run rebuilds and publishes the snapshot
from durable summaries only. It does not reopen transcript content. This repairs a
crash between any snapshot-input commit and snapshot publication.

## macOS lifecycle

The owned file is
`~/Library/LaunchAgents/com.100xprism.token-economics.plist`. It contains an explicit
generated marker and invokes a small owned launcher at
`~/.100xprism/token-economics/launcher.py`, never the transient npm package directory.
The launcher contains no schema logic: it resolves the single atomic `current` symlink
and executes that generation's collector with that generation's database.

Installation resolves `python3` to an absolute executable, verifies it can import the
required standard-library modules, and writes that exact path into `ProgramArguments`.
The plist never relies on an interactive-shell `PATH`. A missing interpreter leaves the
previous owned plist unloaded and prints a recovery command rather than installing a
job that will fail after login. Tests cover Homebrew paths, paths containing spaces, and
a missing interpreter.

The LaunchAgent uses `RunAtLoad`, `StartInterval=60`, background process type,
low-priority I/O, and a positive nice value. It does not use `KeepAlive`. `launchd`
does not overlap invocations of the same job.

- `100xprism install` creates the owned launcher and first versioned generation from the
  stable clone, atomically points `current` at it, writes or refreshes the plist,
  bootstraps it into the current GUI domain, and triggers the first collection.
- `100xprism update` first refuses a live `~/100xprism` checkout with tracked or
  untracked changes; it never removes ignored files. It stages and validates a new
  immutable runtime generation on the same filesystem without changing `current`.
  After stopping the dashboard and acquiring the collector lock, it uses SQLite's
  backup API to copy the current database into the candidate generation, runs every
  required sequential migration there, checkpoints and closes it, verifies no
  candidate WAL/SHM files remain, and runs integrity and schema checks with the new
  collector. Cutover is one atomic rename of a prepared symlink over `current`; the
  plist and launcher never move or unload. A kill before that rename leaves the old
  complete generation active; a kill after it leaves the new complete generation
  active. Releasing the lock and kickstarting the still-loaded job completes update.
  The preceding generation is retained until the new collector completes successfully,
  so rollback is the same atomic symlink operation. The stable clone is then advanced
  with a non-destructive fast-forward; failure leaves its files and the active runtime
  intact and is reported separately.
- npm `preuninstall` does not boot out the owned LaunchAgent because the job does
  not execute code from the transient npm package. npm `postinstall` validates and
  kickstarts an already installed owned plist after an upgrade; it does not opt a new
  user into token economics or clone a repository. Plain npm removal and interrupted
  upgrades therefore leave scheduled collection running.
- `100xprism uninstall` boots out and removes the owned plist. It retains the database
  so reinstalling restores history.
- Installation writes `~/.100xprism/token-economics-manifest.json` with a generated
  marker and dynamically registers every artifact before creation: launcher, current
  symlink, each generation and its database/sidecars/runtime,
  update journal, dashboard credential file, lock, logs, and diagnostics. Generation
  roots also carry an ownership marker. Cleanup validates the manifest and marker,
  requires every target to remain under the dedicated token-economics root, and permits
  WAL/SHM deletion only beside a validated owned database.
- Search disable scrubs content from every retained generation or recovery database,
  not only `current`, before reporting success. Successful update removes obsolete
  generations only after a successful collector run and only through the manifest;
  it never removes a dirty user checkout. Dashboard exit removes its credential file.
- `100xprism uninstall --purge` removes every registered generation database and
  WAL/SHM file, launcher, symlink, runtime credential, update artifact, process lock,
  log, and bounded diagnostic only through that validated manifest. The JS and Python
  cleanup ownership rules and cleanup-contract tests remain in parity. Purge removes
  the validated manifest itself last only after every registered target succeeds; a
  partial cleanup retains it so the remaining owned artifacts can be recovered safely.
- `100xprism tokens` ensures the owned job is loaded, triggers a non-overlapping
  collection, starts the on-demand server if necessary, and opens the browser unless
  `--no-open` was supplied.

On Windows and Linux, existing foreground `tokens`, `tokens --print`, `tokens --json`,
and `tokens --tool` behavior remains available. The CLI adds a concise banner:
`Background token economics is coming soon for this platform.` LaunchAgent and SQLite
background-lifecycle flags make no changes on those platforms. This preserves the
current reporting contract while accurately scoping the coming-soon declaration.

## Resource budgets

- Collector resident memory is zero between scheduled runs.
- Ordinary unchanged one-minute runs perform metadata inventory only and do not open
  transcript content. They rebuild a dashboard snapshot from SQLite only when a durable
  revision shows that an earlier publication was interrupted. The separately due daily
  integrity pass reads at most 32 MiB of historical block data without reparsing it.
- A collector processes one changed file at a time and does not retain raw transcript
  content after committing it.
- The dashboard server is the only resident Python process while the UI is open. It
  exits after ten minutes without browser/API traffic.
- Git/value recomputation is limited to affected project paths plus a bounded periodic
  refresh; unchanged repositories use the existing value cache. Scheduled collectors
  exclude GitHub insights and never invoke `gh` or another network client. The existing
  optional `github.enabled` dashboard integration remains an explicit, interactive-only
  exception and is labeled before it accesses GitHub.
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

Search endpoints return session metadata and structured excerpt segments of the form
`{text, matched}` rather than FTS-generated HTML. The client escapes every `text` value
with the existing HTML escaper and wraps only server-generated `matched` segments in a
fixed highlight element. It never inserts transcript text as HTML. Responses include a
restrictive Content Security Policy using a per-response script nonce and disallow
object, frame, and remote connection sources. Queries are parameterized and bounded by
result count.

The server rejects every request whose `Host` is not the exact launched loopback host
and port. Each dashboard launch generates a random 256-bit bootstrap token and writes
the port, PID, and token to an owned mode-`0600` runtime file. The CLI reads that file
when reopening an existing server and places the token in the URL fragment, which
browsers do not send in HTTP requests. Startup JavaScript exchanges it once for an
`HttpOnly`, `SameSite=Strict` process-lifetime session cookie and clears the fragment.
Search and every mutating API require that cookie. Mutating requests also require an
exact loopback `Origin` and a per-page CSRF header. The runtime file is never served,
logged, or stored in SQLite and is removed on clean exit or when its PID is stale. This
threat model accepts that another process running as the same OS user can already read
the user's transcripts and database. Tests cover reload, duplicate tabs, reopening an
existing dashboard, hostile Host headers, missing/wrong credentials, cross-origin
mutation attempts, stale runtime files, and DNS-rebinding-shaped requests.

## Privacy and security

- Transcript collection, SQLite storage, search, and scheduled snapshot building are
  local-only and perform no network calls. The pre-existing opt-in GitHub insights
  integration is interactive-only and remains governed by `github.enabled`.
- The HTTP server binds only to `127.0.0.1`.
- The server validates Host and Origin and protects search/mutation APIs with an
  unguessable per-launch credential; loopback binding alone is not treated as
  authorization.
- Content search is opt-in and its scope is stated before activation.
- Disabling content search removes text from the live database and sidecars without
  deleting economic history, while honestly warning about filesystem backups.
- Generated plist deletion requires the exact label and ownership marker. Purge of all
  other token-economics artifacts requires the validated owned manifest.
- Database SQL uses parameters, and FTS queries are validated and bounded.
- Search excerpts are structured data, escaped before rendering, and covered by CSP.
- Logs contain paths and counts only, not transcript content.

## Testing strategy

### Store and migration tests

- Create, reopen, and migrate a database transactionally.
- Reject an unsupported future schema without modifying it.
- Verify WAL mode, permissions, snapshot rollback, scan-history cap, and content removal
  from the main database plus WAL/SHM sidecars after FTS disable.
- Verify value-cache data and `snapshot_input_revision` commit or roll back together;
  the legacy JSON cache is import-only and is never rewritten.
- Enable search after economics history already exists and verify a content-only
  backfill reaches old sessions without changing token totals.

### Incremental parser tests

- A first scan parses a fixture fully.
- A second ordinary unchanged scan reads no transcript content.
- Appending complete records parses only bytes after the committed offset.
- An incomplete trailing JSONL record remains uncommitted until completed.
- Truncation, replacement, tail-anchor mismatch, rotating block-hash mismatch, and
  parser-version change rebuild only the affected file.
- Verify rewrites before, within, and after the current historical integrity block and
  advance the bounded daily integrity cursor without reparsing unchanged records.
- Verify a same-size, same-mtime rewrite in an unchanged sub-1-MiB partial block is
  detected by bounded integrity maintenance.
- Verify partial-block appends below, across, and above 1 MiB boundaries without false
  rebuilds, and cap a due integrity pass at 32 MiB.
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
- A forced termination after a file commit but before snapshot publication leaves the
  prior snapshot readable; the next unchanged run republishes from durable summaries.
- Repeat that termination test after absence/deletion state, degraded coverage,
  content-backfill progress, and value-cache commits.
- Unchanged inventory does not invoke aggregation when snapshot-input and snapshot
  revisions match.
- A killed collector's stale scan run, lease, and `scanning` location state are
  reconciled by the next run.

### Lifecycle tests

- Install writes an owned macOS plist and triggers collection.
- Update stages and validates code before cutover, migrates from supported skipped
  versions, atomically switches `current`, and kickstarts the job.
- Termination before and after every preparation and symlink transition leaves either
  the old or new complete generation runnable through the stable launcher, including
  after reboot.
- Candidate failures during migration commit and checkpoint, including a non-empty WAL,
  never expose candidate frames to the active database; rollback verifies the prior
  generation's integrity and schema before switching.
- A candidate value-cache format change remains inside its generation; atomic rollback
  restores the preceding database and value format together.
- Dirty tracked and untracked clone updates abort without changes, and ignored files
  remain present.
- npm upgrade kickstarts only a previously installed job; plain npm removal and a
  failed or interrupted npm upgrade do not stop collection.
- Ordinary uninstall removes the agent but retains history.
- Purge removes the agent, database, sidecars, locks, and logs.
- Hand-written or foreign LaunchAgent files are never deleted.
- Purge refuses an absent, malformed, escaped, or marker-less ownership manifest.
- Successful update, failed rollback, interrupted update, search disable, and purge
  clean every dynamically registered runtime, database, sidecar, journal, and credential
  artifact according to their retention contract.
- Windows and Linux preserve foreground reporting, show the background-service
  coming-soon banner, and do not install lifecycle artifacts.

### Dashboard tests

- A saved snapshot renders before collection completes.
- Live status is distinct from completed totals.
- Cache metrics and counterfactual savings use the correct model rates.
- FTS is unavailable while disabled and returns filtered, bounded excerpts while
  enabled.
- Search disable drains readers, pauses collection, verifies vacuum/checkpoint cleanup,
  invalidates the old server token, and resumes collection in a `finally` path.
- Malicious prompt HTML remains inert in structured highlighted excerpts under CSP.
- DNS-rebinding-shaped Host headers and unauthenticated search requests are rejected.
- Reload, duplicate-tab, and CLI reopen flows obtain a valid process-lifetime session
  without exposing the bootstrap credential in HTTP requests or logs.
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
