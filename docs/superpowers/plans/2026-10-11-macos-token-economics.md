# macOS Token Economics Implementation Plan

> **For agentic workers:** Use test-driven development and focused parallel ownership. Track completion here; the approved spec is binding.

**Goal:** Continuously collect local economics on macOS and open a durable, authenticated dashboard immediately.

**Architecture:** Standard-library Python owns incremental parsing, SQLite WAL, collection and HTTP serving. Node owns CLI routing and macOS installation; a stable launcher resolves an atomic runtime-generation symlink.

**Tech Stack:** Python 3, SQLite/FTS5, Node 18+, macOS launchd; no third-party runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-10-10-macos-token-economics-design.md`

## Global Constraints

- Keep unrelated existing changes; never edit rendered skills or bump versions.
- Background collection is macOS first, one-shot every 60 seconds; dashboard exits after 600 idle seconds.
- Default search off; index only prompts and assistant prose, with an independent byte offset.
- Historical summaries survive missing source files; normal unchanged scans open no transcript contents.
- Integrity maintenance is separately due daily and reads at most 32 MiB per run.
- Persist all snapshot inputs and their revision transactionally; publish only complete snapshots.
- Register artifacts before creation, validate ownership and contain paths; history deletion requires `--purge`.
- Scheduled work makes no network calls. Authenticated search, exact Host/Origin checks, CSRF and CSP protect HTTP.
- Unsupported platforms preserve foreground reports and show the specified coming-soon banner.

## Review Focus

- Interrupted append, rewrite and snapshot publication must retain the prior valid economics.
- Symlink escapes and foreign manifests/plists must never authorize deletion or overwrite.
- Search disable must scrub retained generations and remain disabled on failure.
- Duplicate tabs and CLI reopening must keep authentication valid without logging credentials.
- Upgrade interruption must leave one runnable generation; dirty clones must remain untouched.

### Task 1: Incremental adapters

**Files:** `scripts/adapters/_incremental.py`, tool adapters, `scripts/test_incremental_adapters.py`.

**Produces:** Each JSONL adapter exposes `PARSER_VERSION`, `parse_incremental(path, offset=0, state=None, summary=None)` returning `{summary, state, offset, bytes_read}`; `_incremental.extract_content(tool, path, offset=0, limit_bytes=1048576)` returns `{rows, offset, bytes_read}` with role/text/timestamp/sequence. Strict malformed complete records raise, incomplete EOF remains uncommitted. Existing `parse_file` stays compatible.

- [x] Write and run failing continuation, cumulative-counter, attribution and content-exclusion fixture tests.
- [x] Implement record reducers, saved minimal state and newline-safe binary reads.
- [x] Run incremental and existing adapter tests; record outcome.

### Task 2: Store and collector

**Files:** `scripts/_token_store.py`, `scripts/token-collector.py`, Python store/collector tests.

**Consumes:** Task 1 parser contracts; dashboard `build(verbose=False, summaries=rows, value_context=(discovered, dir_index), offline=True)` aggregates durable rows without source scanning.

**Produces:** `TokenStore(db_path)` with `latest_snapshot()`, `status()`, `search(query, tool=None, project=None, role=None, date_from=None, date_to=None, limit=20)`, `close()`. CLI collector accepts `--db`, `--root`, `--verify-history`, `--enable-content-search`, `--disable-content-search`, `--validate`, `--backup-from`. Default DB is `~/.100xprism/token-economics/current/token-economics.sqlite3`.

- [x] Write/run failing WAL, migration, revision recovery, unchanged-I/O, deletion, malformed-file, lock and integrity tests.
- [x] Implement store transactions, history, content maintenance, checkpoints and block hashes.
- [x] Implement one-shot inventory, bounded content backfill, lock recovery, durable value cache and complete snapshots.
- [x] Run store/collector tests and record outcome.

### Task 3: Lifecycle and CLI

**Files:** `lib/token-economics.js`, `lib/dashboard.js`, `lib/tokens.js`, install/update/uninstall/cleanup integrations, lifecycle tests, npm lifecycle script.

**Consumes:** Collector CLI; authenticated server runtime file `<root>/dashboard-runtime.json` containing `{pid, port, token, script}` with mode 0600.

- [x] Write/run failing generation-cutover, interpreter/plist, ownership, npm-upgrade and platform tests.
- [x] Implement stable launcher, generated manifest, atomic generations, backup migration and update refusal for dirty clones.
- [x] Integrate install/update/uninstall/purge/search maintenance and token commands. Register the runtime credential before launch.
- [x] Run lifecycle/cleanup tests and record outcome.

### Task 4: Dashboard and integration

**Files:** `scripts/token-dashboard.py`, `scripts/_token_http.py`, dashboard tests, `docs/USAGE.md`, `package.json` test list.

- [x] Write/run failing saved-snapshot, authentication, CSRF, CSP, search escaping and idle-exit tests.
- [x] Refactor aggregation to accept durable summaries and persisted value context; suppress scheduled GitHub access.
- [x] Serve snapshots and live coverage independently, trigger collector asynchronously, expose cache savings and opt-in search.
- [x] Verify all task interfaces together, run `npm run check`, review final changes and document usage.

## Verification

- Final `npm run check`: 280 JavaScript tests and 207 Python tests passed; context-footprint, metadata and Claude adapter smoke checks passed.
- Real Python lifecycle smoke in a temporary home: install, upgrade, enable/disable content search across retained generations, uninstall and explicit purge passed.
- Real browser smoke with synthetic data: fragment-to-cookie login, tabs/time windows, refresh, theme, Cursor filter, escaped malicious search text, context growth and per-model cache economics passed; final page had no console errors.
- Independent review findings were covered by regressions: project enrichment, stale duplicate authority, inclusive dates, immutable print fallback, resolved interpreter, lock-time search inventory and interrupted registered manifest temp recovery.
- Direct macOS shell install/update defers collector setup without Node and preserves existing collectors; the regression executes both shell paths with an empty PATH.
- Native launchd bootstrap, login, reboot and scheduling were not exercised. Launchctl calls used stubs; no user LaunchAgent was installed during verification.
- Existing unrelated working-tree changes were preserved. The package version remains unchanged; publication is separate from build verification.
