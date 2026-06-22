# Token + Value, All Directories — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the registry/CHANGELOG-gated value panel with one automatic view over every directory that consumed tokens — repo or not — joining Claude-Code token *cost* to tool-agnostic git/filesystem *value*, with four inline-SVG charts and cached AI summaries.

**Architecture:** Two independent layers joined on the resolved real directory path. (1) **Cost** = pluggable per-tool adapters; only `claude_code` is real in v1, `codex` is a documented stub. (2) **Value** = git history + filesystem-mtime activity per directory, derived automatically (no CHANGELOG, no registration). AI one-liners come from a non-blocking background pass over the local `claude` CLI. `value.json` is repurposed from a manual registry into an automatic cache keyed by `dir + HEAD-sha + window`.

**Tech Stack:** Python 3 stdlib only (no third-party deps), `http.server`, inline SVG + vanilla JS/CSS, `git` subprocess, local `claude` CLI. Tests via `unittest` (`python3 scripts/test_value.py`) and `node --test`.

## Global Constraints

- **Zero third-party dependencies; fully offline.** Serve path must never block on git or the `claude` CLI — both run on the background rebuild tick only. (verbatim: "No third-party dependencies. Fully offline (no CDN).")
- **Python 3 stdlib only.** No new pip installs.
- **`project_label` join key is canonical** — `_value.project_label(transcript_path)` and `_value.project_label_for_path(repo)` must agree (existing `LabelTest` invariant).
- **Cost-less directories render `—`, never `$0`** (spec display rule).
- **One semantic color rule everywhere:** `--cost` amber `#E8B24A` = spent; `--value` green `#5BD0A6` = shipped; `--warn` `#E5704B` = high-cost/low-output.
- **Estimates stay labelled "estimate"** (window attribution, mtime fallback, composition) — consistent with the existing UI voice.
- **Commit message convention:** Conventional Commits; end body with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Gate before each commit:** the repo's pre-commit hook blocks commits until `/gate` passes for the exact tree. Run tests, then `python3 ~/100xprism/hooks/gate-pass.py`, then commit (see Task 0).

---

## File Structure

- `scripts/_value.py` — **rewrite of `_shipped.py`.** Path resolution, git value mining, fs-mtime fallback, per-dir value snapshot, value-cache store (keyed by real dir). No CHANGELOG parsing, no registry/source field. Keeps `project_label` / `project_label_for_path`.
- `scripts/adapters/__init__.py` — package marker + `Usage` namedtuple + `ADAPTERS` registry.
- `scripts/adapters/claude_code.py` — `iter_usage()` over `~/.claude/projects`; `resolve_dir(mangled)`; reuses the dashboard's transcript parse for tokens.
- `scripts/adapters/codex.py` — documented **stub** `iter_usage()` returning `[]`; reads `~/.codex/sessions` only if present (not relied on in v1).
- `scripts/_summaries.py` — background AI-summary pass over the local `claude` CLI; cache read/write into the value store; pure-degradation when CLI absent.
- `scripts/token-dashboard.py` — **modify.** Consume the value layer; `build()` emits `directories`; new charts + unified table in `PAGE`; `/api/data` carries `directories`; remove `value_panel` / `valueHTML` registry code.
- `scripts/value-report.py` — **rewrite** as a thin CLI over `_value.py` (print a dir's cost+value snapshot; no "register" step).
- `scripts/test_value.py` — **modify.** Drop CHANGELOG/registry tests; add path-resolution, git-value, fs-value, cache, and directories-shape tests.
- `README.md` / `docs/token-optimization.md` / `CHANGELOG.md` — doc updates (final task).

**Migration note on `_shipped.py` → `_value.py`:** `token-dashboard.py`, `value-report.py`, and `test_value.py` all `import _shipped`. The rewrite renames the module; every importer is updated in the same task that introduces the new module (Task 1) so the tree never has a dangling import between commits.

---

## Data Contract (shared by Tasks 4–8)

`build()` adds one top-level field, `directories`, a list sorted by cost desc:

```python
{
  "dir": "/Users/rajit/personal-github/100xprism",  # resolved real path, or None if unresolved
  "label": "~/personal/github/100xprism",           # project_label (join key, always present)
  "tool": "claude-code",                             # adapter id
  "cost": 1204.5,                                     # windowed $ total, or None if cost-less
  "tokens": {"input":int,"output":int,"cache_read":int,"cache_write":int},
  "window": {"start": "2026-06-01", "end": "2026-06-21"},  # first/last active day; None/None if unknown
  "value": {
    "kind": "git" | "fs" | "none",
    "commits": 12, "prs": 3, "files": 40, "insertions": 900, "deletions": 120,
    "subjects": ["feat: ...", "..."],   # up to 5, for the AI summary + tooltip
    "fs_files": 0,                      # files touched (fs fallback only)
    "summary": None                     # filled later by _summaries; None until then
  }
}
```

`build()` also keeps `by_project_day_cost` (label → {day: $}) for the stacked cost-over-time chart, and continues to emit `totals`, `composition`, `bloat`, `by_model`, `by_day` (Claude-Code-specific sections, unchanged). The `value_store` field is **removed**.

Value-store on disk (`~/.100xprism/value.json`), `STORE_VERSION = 2`:

```python
{"version": 2, "dirs": {
  "/Users/rajit/personal-github/100xprism": {
    "label": "~/personal/github/100xprism", "tool": "claude-code",
    "head": "<sha or ''>", "window": {"start": "...", "end": "..."},
    "value": { ...as above, may include "summary"... },
    "scanned": "2026-06-21T13:19:12"
  }
}}
```

---

### Task 0: Gate helper (run before every commit step)

Every "Commit" step in this plan expands to this sequence (the repo hook blocks a bare `git commit`):

```bash
# from repo root
python3 scripts/test_value.py            # Python gate
node --test                              # JS gate (must stay green)
python3 ~/100xprism/hooks/gate-pass.py   # record pass for the CURRENT tree
git add <files for this task>
git commit -m "<conventional message>

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

If either test run fails, fix before recording the pass. Do not run `gate-pass.py` on a red tree.

---

### Task 1: Rename `_shipped.py` → `_value.py`, strip CHANGELOG/registry, keep labels

**Files:**
- Create: `scripts/_value.py` (from `_shipped.py`, reduced)
- Delete: `scripts/_shipped.py`
- Modify: `scripts/token-dashboard.py:47` (`import _shipped` → `import _value as _shipped` shim is NOT used — replace the name; see step 3), `scripts/value-report.py`, `scripts/test_value.py`
- Test: `scripts/test_value.py`

**Interfaces:**
- Produces: `_value.HOME`, `_value.PROJECTS_DIR`, `_value.STORE_DIR`, `_value.STORE_PATH`, `_value.STORE_VERSION = 2`; `_value.project_label(transcript_path) -> str`; `_value.project_label_for_path(repo_abs_path) -> str`. Everything CHANGELOG/registry-related (`parse_changelog`, `repo_value`, `save_repo`, `autodiscover`, `refresh_store`, `_strip_md`, `VERSION_RE`, `SECTION_RE`, `BULLET_RE`, the `source` field) is **removed** — later tasks add the new value functions to this same file.

- [ ] **Step 1: Create `scripts/_value.py` with the kept primitives only**

```python
#!/usr/bin/env python3
"""
_value.py — tool-agnostic "what shipped" value layer + per-directory value cache.

Single source of truth for the VALUE side of token & value economics, used by the
token dashboard (web UI) and value-report.py (CLI). It derives value automatically
from git history and filesystem activity per real directory — no CHANGELOG, no manual
registration. The on-disk cache at ~/.100xprism/value.json is keyed by real directory.

Offline, no third-party dependencies.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")

STORE_DIR = os.path.join(HOME, ".100xprism")
STORE_PATH = os.path.join(STORE_DIR, "value.json")
STORE_VERSION = 2

# The dashboard turns a transcript dir like "-Users-rajit-foo-bar" into a label.
_HOME_DASH = HOME.replace("/", "-") + "-"  # e.g. "-Users-rajit-"


# ----------------------------------------------------------------- labels

def _label_from_dirname(dirname):
    return dirname.replace(_HOME_DASH, "~/").replace("-", "/")


def project_label(transcript_path):
    """Readable project name for a transcript path under ~/.claude/projects/."""
    if "/projects/" in transcript_path:
        dirname = transcript_path.split("/projects/")[1].split("/")[0]
    else:
        dirname = transcript_path
    return _label_from_dirname(dirname)


def project_label_for_path(repo_abs_path):
    """The dashboard label a repo at this filesystem path would get."""
    abs_path = os.path.abspath(os.path.expanduser(repo_abs_path))
    return _label_from_dirname(abs_path.replace("/", "-"))
```

- [ ] **Step 2: Delete the old module**

Run: `git rm scripts/_shipped.py`
Expected: staged deletion.

- [ ] **Step 3: Update the three importers**

In `scripts/token-dashboard.py:47` replace:
```python
import _shipped  # noqa: E402 — shared value/store helper (one source of truth)
```
with:
```python
import _value  # noqa: E402 — shared value layer (one source of truth)
```
Then replace every `_shipped.` with `_value.` in `token-dashboard.py`. (The references at lines 201, 277, 365, 380, 406 will mostly be removed in later tasks; for now point them at `_value` so the module imports. Line 201 `project_label = _shipped.project_label` → `_value.project_label`. Lines 277/365/380/406 reference functions being removed — comment-stub them temporarily: set `value_store = {}` at line 277, and have `value_panel`/`_top_items` raise/return `{}`; they are deleted in Task 6.)

In `scripts/value-report.py` and `scripts/test_value.py` replace `import _shipped` → `import _value as _shipped` **only as a temporary alias** so existing tests keep importing; Task 9/10 rewrite both files and drop the alias.

- [ ] **Step 4: Trim `test_value.py` to the surviving tests**

Delete `ChangelogTest`, `GitBoundaryTest`, and `StoreTest` (they test removed functions). Keep `LabelTest` (uses `project_label*`, which survive). Leave the `td` import of `token-dashboard.py`.

- [ ] **Step 5: Run tests to verify the reduced suite is green**

Run: `python3 scripts/test_value.py`
Expected: PASS (only `LabelTest` remains) — proves the rename + label parity hold.
Run: `node --test`
Expected: PASS (unchanged).

- [ ] **Step 6: Commit** (Task 0 sequence)

Message: `refactor(value): rename _shipped→_value, drop CHANGELOG/registry, keep label join`

---

### Task 2: Robust real-directory resolution

The mangling `/`→`-` is lossy (real dir names contain hyphens). Resolve a mangled transcript dirname to an existing path by verifying the filesystem segment-by-segment, trying `/` first then `-` at each ambiguous boundary.

**Files:**
- Modify: `scripts/_value.py` (append)
- Test: `scripts/test_value.py`

**Interfaces:**
- Produces: `_value.resolve_real_dir(mangled_dirname, root="/") -> str | None` — returns an absolute path that exists on disk, or `None` if no existing path matches.

- [ ] **Step 1: Write the failing test**

```python
class ResolveDirTest(unittest.TestCase):
    def test_resolves_hyphenated_segment(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as root:
            # real path has a hyphen IN a segment: <root>/personal-github/100xprism
            target = os.path.join(root, "personal-github", "100xprism")
            os.makedirs(target)
            mangled = target.replace("/", "-")   # lossy: every / and the hyphen are '-'
            self.assertEqual(_shipped.resolve_real_dir(mangled), target)

    def test_returns_none_when_absent(self):
        self.assertIsNone(_shipped.resolve_real_dir("-no-such-path-xyz-123"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 scripts/test_value.py -k ResolveDir`
Expected: FAIL with `AttributeError: module '_value' has no attribute 'resolve_real_dir'`.

- [ ] **Step 3: Implement segment-wise resolution**

```python
# ----------------------------------------------------------------- path resolution

def resolve_real_dir(mangled_dirname, root="/"):
    """Best-effort un-mangle of a ~/.claude/projects dir name to a real path.

    The mangling joins path parts with '-' and also turns every literal '-' in a
    segment into '-', so it is ambiguous. We walk the tokens left-to-right and at
    each step greedily extend the current segment with '-' as long as no child
    directory matches by '/'. Returns an absolute existing path, or None.
    """
    tokens = mangled_dirname.strip("-").split("-")
    if not tokens:
        return None
    cur = root.rstrip("/") or "/"
    i = 0
    while i < len(tokens):
        # Try the longest run of tokens (joined by '-') that names an existing child.
        matched = None
        seg = tokens[i]
        j = i
        # Prefer a single-token '/' child; if absent, glue tokens with '-'.
        while True:
            cand = os.path.join(cur, seg)
            if os.path.isdir(cand):
                matched = (cand, j)
            # look ahead: does gluing the next token (as a hyphen) reach a real dir?
            if j + 1 < len(tokens) and os.path.isdir(
                    os.path.join(cur, seg + "-" + tokens[j + 1])) or (
                    j + 1 < len(tokens) and not os.path.isdir(cand)):
                j += 1
                seg = seg + "-" + tokens[j]
                continue
            break
        if matched is None:
            # No existing child at this level → can't resolve further.
            return None
        cur, i = matched[0], matched[1] + 1
    return cur if os.path.isdir(cur) else None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 scripts/test_value.py -k ResolveDir`
Expected: PASS (both cases).

- [ ] **Step 5: Commit** (Task 0 sequence)

Message: `feat(value): filesystem-verified resolution of mangled project dir names`

---

### Task 3: Git value mining (windowed)

**Files:**
- Modify: `scripts/_value.py` (append)
- Test: `scripts/test_value.py`

**Interfaces:**
- Produces:
  - `_value.git_head(repo) -> str` — current HEAD sha or `""` if not a repo.
  - `_value.git_value(repo, start, end) -> dict | None` — `None` if not a git repo; else `{"commits":int,"prs":int,"files":int,"insertions":int,"deletions":int,"subjects":[str...up to 5]}`. `start`/`end` are `"YYYY-MM-DD"` or `None` (open-ended). Window is `(start 00:00, end 23:59]` inclusive of `end`'s day.

- [ ] **Step 1: Write the failing test**

```python
class GitValueTest(unittest.TestCase):
    def test_windowed_commits_prs_files(self):
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "feat: a (#1)")
            commit(repo, "fix: b")
            commit(repo, "docs: c (#2)")
            self.assertNotEqual(_shipped.git_head(repo), "")
            v = _shipped.git_value(repo, None, None)
            self.assertEqual(v["commits"], 3)
            self.assertEqual(v["prs"], 2)               # (#1) and (#2)
            self.assertGreaterEqual(v["files"], 1)
            self.assertEqual(len(v["subjects"]), 3)

    def test_not_a_repo_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(_shipped.git_value(d, None, None))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 scripts/test_value.py -k GitValue`
Expected: FAIL (`git_head`/`git_value` undefined).

- [ ] **Step 3: Implement**

```python
# ----------------------------------------------------------------- git value

_PR_RE = re.compile(r"\(#\d+\)")


def _git(repo, *a, timeout=10):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True,
                          text=True, timeout=timeout)


def git_head(repo):
    try:
        r = _git(repo, "rev-parse", "HEAD")
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def git_value(repo, start, end):
    """Windowed git activity, or None if `repo` is not a git work tree."""
    try:
        if _git(repo, "rev-parse", "--is-inside-work-tree").returncode != 0:
            return None
        args = ["log", "--no-merges", "--pretty=%s"]
        if start:
            args.append(f"--since={start} 00:00:00")
        if end:
            args.append(f"--until={end} 23:59:59")
        subjects = [s for s in _git(repo, *args).stdout.splitlines() if s]
        # files / insertions / deletions over the same window
        nargs = ["log", "--no-merges", "--numstat", "--pretty=tformat:"]
        if start:
            nargs.append(f"--since={start} 00:00:00")
        if end:
            nargs.append(f"--until={end} 23:59:59")
        files, ins, dele = set(), 0, 0
        for ln in _git(repo, *nargs).stdout.splitlines():
            parts = ln.split("\t")
            if len(parts) == 3:
                a, d, path = parts
                files.add(path)
                ins += int(a) if a.isdigit() else 0
                dele += int(d) if d.isdigit() else 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return {
        "commits": len(subjects),
        "prs": sum(1 for s in subjects if _PR_RE.search(s)),
        "files": len(files), "insertions": ins, "deletions": dele,
        "subjects": subjects[:5],
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 scripts/test_value.py -k GitValue`
Expected: PASS.

- [ ] **Step 5: Commit** (Task 0 sequence)

Message: `feat(value): windowed git activity mining (commits, PRs, files, churn)`

---

### Task 4: Filesystem fallback + value snapshot + cache

**Files:**
- Modify: `scripts/_value.py` (append)
- Test: `scripts/test_value.py`

**Interfaces:**
- Produces:
  - `_value.fs_value(directory, start, end) -> dict` — mtime-based: `{"fs_files": int}` counting files modified in the window, skipping `.git`, `node_modules`, and dotdirs; only files with a code-ish extension (allowlist) or any extension. Never raises.
  - `_value.dir_value(real_dir, label, tool, start, end) -> dict` — the full `value` block per the Data Contract (`kind` = `git` if a repo, else `fs` if any files touched, else `none`).
  - `_value.load_store() -> dict` / `_value.save_store(store)` — version-2 `{"version":2,"dirs":{}}`.
  - `_value.cached_dir_value(real_dir, label, tool, start, end) -> dict` — returns the cached `value` when `head + window` are unchanged; otherwise recomputes via `dir_value`, writes the cache, and returns it. Preserves any existing `summary`.

- [ ] **Step 1: Write the failing test**

```python
class DirValueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (_shipped.STORE_DIR, _shipped.STORE_PATH)
        _shipped.STORE_DIR = self.tmp.name
        _shipped.STORE_PATH = os.path.join(self.tmp.name, "value.json")

    def tearDown(self):
        _shipped.STORE_DIR, _shipped.STORE_PATH = self._orig
        self.tmp.cleanup()

    def test_git_dir_value_kind_git(self):
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "feat: a")
            v = _shipped.dir_value(repo, "~/x", "claude-code", None, None)
            self.assertEqual(v["kind"], "git")
            self.assertEqual(v["commits"], 1)

    def test_non_repo_uses_fs_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "note.md"), "w") as f:
                f.write("x")
            v = _shipped.dir_value(d, "~/d", "claude-code", None, None)
            self.assertEqual(v["kind"], "fs")
            self.assertGreaterEqual(v["fs_files"], 1)

    def test_cache_preserves_summary_until_head_changes(self):
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "feat: a")
            _shipped.cached_dir_value(repo, "~/x", "claude-code", None, None)
            # inject a summary as the background pass would
            store = _shipped.load_store()
            store["dirs"][os.path.abspath(repo)]["value"]["summary"] = "did a thing"
            _shipped.save_store(store)
            again = _shipped.cached_dir_value(repo, "~/x", "claude-code", None, None)
            self.assertEqual(again["summary"], "did a thing")   # head unchanged → kept
            commit(repo, "fix: b")
            after = _shipped.cached_dir_value(repo, "~/x", "claude-code", None, None)
            self.assertIsNone(after["summary"])                 # head changed → recomputed
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 scripts/test_value.py -k DirValue`
Expected: FAIL (`fs_value`/`dir_value`/`cached_dir_value` undefined).

- [ ] **Step 3: Implement**

```python
# ----------------------------------------------------------------- fs fallback + snapshot + cache

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__",
              "dist", "build", ".next", ".cache"}


def _day_bounds(start, end):
    import time
    lo = time.mktime(time.strptime(start + " 00:00:00", "%Y-%m-%d %H:%M:%S")) if start else None
    hi = time.mktime(time.strptime(end + " 23:59:59", "%Y-%m-%d %H:%M:%S")) if end else None
    return lo, hi


def fs_value(directory, start, end):
    """Count files modified within the window (mtime). Best-effort; never raises."""
    lo, hi = _day_bounds(start, end)
    n = 0
    try:
        for dp, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            for fn in files:
                try:
                    mt = os.stat(os.path.join(dp, fn)).st_mtime
                except OSError:
                    continue
                if (lo is None or mt > lo) and (hi is None or mt <= hi):
                    n += 1
    except OSError:
        pass
    return {"fs_files": n}


def _empty_value():
    return {"kind": "none", "commits": 0, "prs": 0, "files": 0,
            "insertions": 0, "deletions": 0, "subjects": [], "fs_files": 0,
            "summary": None}


def dir_value(real_dir, label, tool, start, end):
    """Tool-agnostic value snapshot for one real directory."""
    v = _empty_value()
    g = git_value(real_dir, start, end)
    if g is not None:
        v.update(g)
        v["kind"] = "git"
        return v
    fs = fs_value(real_dir, start, end)
    v["fs_files"] = fs["fs_files"]
    v["kind"] = "fs" if fs["fs_files"] else "none"
    return v


def load_store():
    try:
        with open(STORE_PATH, encoding="utf-8") as f:
            s = json.load(f)
        if s.get("version") == STORE_VERSION and isinstance(s.get("dirs"), dict):
            return s
    except (OSError, ValueError):
        pass
    return {"version": STORE_VERSION, "dirs": {}}


def save_store(store):
    try:
        os.makedirs(STORE_DIR, exist_ok=True)
        with open(STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
    except OSError as e:
        print(f"warning: could not write value store ({STORE_PATH}): {e}", file=sys.stderr)


def cached_dir_value(real_dir, label, tool, start, end):
    """Return cached value if HEAD+window unchanged, else recompute and cache.
    Preserves a previously-written AI `summary` across cache hits."""
    real_dir = os.path.abspath(real_dir)
    head = git_head(real_dir)
    window = {"start": start, "end": end}
    store = load_store()
    prev = store["dirs"].get(real_dir)
    if prev and prev.get("head") == head and prev.get("window") == window:
        return prev["value"]
    v = dir_value(real_dir, label, tool, start, end)
    if prev:  # carry the summary forward only when the snapshot is otherwise the same
        pass
    store["dirs"][real_dir] = {
        "label": label, "tool": tool, "head": head, "window": window,
        "value": v, "scanned": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_store(store)
    return v
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 scripts/test_value.py -k DirValue`
Expected: PASS (all three).

- [ ] **Step 5: Commit** (Task 0 sequence)

Message: `feat(value): fs-mtime fallback, per-dir snapshot, head+window value cache`

---

### Task 5: Cost adapters (`claude_code` real, `codex` stub)

**Files:**
- Create: `scripts/adapters/__init__.py`, `scripts/adapters/claude_code.py`, `scripts/adapters/codex.py`
- Test: `scripts/test_value.py`

**Interfaces:**
- Produces:
  - `adapters.Usage` — `namedtuple("Usage", "dir day input output cache_read cache_write tool")` where `dir` is the **mangled** transcript dirname (resolution happens in the dashboard).
  - `adapters.claude_code.iter_usage() -> Iterator[Usage]` — one row per (transcript dir, day) using the dashboard's existing per-file token parse. `tool="claude-code"`.
  - `adapters.claude_code.TOOL = "claude-code"`.
  - `adapters.codex.iter_usage() -> Iterator[Usage]` — returns nothing unless `~/.codex/sessions` exists; documented stub, `TOOL="codex"`.
  - `adapters.ADAPTERS = [claude_code, codex]`.

Note: to avoid duplicating transcript parsing, `claude_code.iter_usage()` imports `parse_file`/`project_label` from the dashboard module via the same `importlib`-by-path trick the test uses, OR the dashboard passes its per-file summaries in. **Decision:** keep parsing in the dashboard; the adapter exposes `iter_usage()` for *future* tools, and for `claude-code` the dashboard calls `adapters.claude_code.iter_dir_days(parsed_cache)` — a pure function over already-parsed summaries. This keeps one parser.

Revised interface:
- `adapters.claude_code.iter_dir_days(file_summaries) -> Iterator[Usage]` — `file_summaries` is `build()`'s `new_cache.values()`; yields per (project-dirname, day) `Usage` rows. The dashboard already has `s["project"]` (label) and `s["by_day"]`; the adapter also needs the raw mangled dir — add `s["projdir"]` in `build()` (the basename of the transcript's parent dir).

- [ ] **Step 1: Write the failing test**

```python
class AdapterTest(unittest.TestCase):
    def test_claude_iter_dir_days(self):
        import importlib
        ad = importlib.import_module("adapters.claude_code")
        summaries = [{
            "projdir": "-Users-rajit-x", "project": "~/x",
            "by_day": {"2026-06-01": {"input":1,"output":2,"cache_read":3,"cache_write":4}},
        }]
        rows = list(ad.iter_dir_days(summaries))
        self.assertEqual(rows[0].dir, "-Users-rajit-x")
        self.assertEqual(rows[0].day, "2026-06-01")
        self.assertEqual(rows[0].output, 2)
        self.assertEqual(rows[0].tool, "claude-code")

    def test_codex_stub_empty_without_sessions(self):
        import importlib
        cx = importlib.import_module("adapters.codex")
        # No ~/.codex/sessions on CI → yields nothing, never raises.
        self.assertEqual(list(cx.iter_usage()), [])
```

(Top of `test_value.py`, add `sys.path.insert(0, HERE)` already covers `adapters` since it's a subdir package.)

- [ ] **Step 2: Run to verify it fails**

Run: `python3 scripts/test_value.py -k Adapter`
Expected: FAIL (`No module named 'adapters'`).

- [ ] **Step 3: Implement the package**

`scripts/adapters/__init__.py`:
```python
"""Cost adapters: per-tool sources of (dir, day, token) usage.

Only `claude_code` produces real numbers today. `codex` is a documented stub for
when the Codex CLI is used locally. Cursor / Antigravity expose no local token data,
so they have no adapter — their directories still appear via the value layer.
"""
from collections import namedtuple

Usage = namedtuple("Usage", "dir day input output cache_read cache_write tool")

from . import claude_code, codex  # noqa: E402

ADAPTERS = [claude_code, codex]
```

`scripts/adapters/claude_code.py`:
```python
"""Claude Code cost adapter — the only adapter with real per-message token data."""
from . import Usage  # type: ignore  # set after __init__ defines Usage

TOOL = "claude-code"


def iter_dir_days(file_summaries):
    """Yield one Usage per (transcript dir, day) from already-parsed transcript
    summaries (build()'s cache values). Keeps a single transcript parser."""
    for s in file_summaries:
        projdir = s.get("projdir") or ""
        for day, d in (s.get("by_day") or {}).items():
            yield Usage(projdir, day, d.get("input", 0), d.get("output", 0),
                        d.get("cache_read", 0), d.get("cache_write", 0), TOOL)
```

(Resolve the circular import: in `__init__.py`, define `Usage` *before* the `from . import` line, as written. `claude_code` does `from . import Usage` which works because `Usage` already exists at import time.)

`scripts/adapters/codex.py`:
```python
"""Codex cost adapter — STUB. Codex CLI writes ~/.codex/sessions/*.jsonl rollouts
(newer versions include token-count events). Not wired into v1; returns nothing
unless those sessions exist, and even then yields [] until the parser is written."""
import os
from . import Usage  # noqa: F401

TOOL = "codex"
SESSIONS = os.path.join(os.path.expanduser("~"), ".codex", "sessions")


def iter_usage():
    if not os.path.isdir(SESSIONS):
        return
    return  # TODO(codex): parse token-count events when Codex is used locally
    yield  # pragma: no cover  — makes this a generator
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 scripts/test_value.py -k Adapter`
Expected: PASS.

- [ ] **Step 5: Commit** (Task 0 sequence)

Message: `feat(adapters): claude_code cost adapter + codex stub behind one interface`

---

### Task 6: Wire `build()` to emit `directories`; remove `value_panel`

**Files:**
- Modify: `scripts/token-dashboard.py` (`build()`, `parse_file`/cache to add `projdir`, `_client_data`, `do_GET`; delete `value_panel`, `_top_items`, `_window_cost` registry plumbing — keep `_window_cost` for per-dir windowed cost)
- Test: `scripts/test_value.py`

**Interfaces:**
- Consumes: `adapters.claude_code.iter_dir_days`, `_value.resolve_real_dir`, `_value.cached_dir_value`, `_value.project_label`.
- Produces: `build()` returns dict with new `directories` (per Data Contract) and retains `by_project_day_cost`; drops `value_store`. `/api/value` endpoint **removed** (data now rides in `/api/data`).

- [ ] **Step 1: Add `projdir` to per-file summaries**

In `build()` where `new_cache[key]` is populated (around line 223), add:
```python
        summary["projdir"] = os.path.basename(os.path.dirname(p))
```
(`p` is `.../projects/<mangled>/<file>.jsonl`, so its parent basename is the mangled dir.)

- [ ] **Step 2: Write the failing test (directories shape)**

```python
class DirectoriesShapeTest(unittest.TestCase):
    def test_build_directories_from_summaries(self):
        # exercise the pure assembler with a real git repo as one dir
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "feat: a")
            label = _shipped.project_label_for_path(repo)
            mangled = os.path.abspath(repo).replace("/", "-")
            by_project_day_cost = {label: {"2026-06-01": 12.0}}
            tokens_by_label = {label: {"input":1,"output":2,"cache_read":3,"cache_write":4}}
            window_by_label = {label: ("2026-06-01", "2026-06-01")}
            dirs = td.assemble_directories(
                {mangled: label}, tokens_by_label, by_project_day_cost,
                window_by_label, tool_by_label={label: "claude-code"})
            row = dirs[0]
            self.assertEqual(row["label"], label)
            self.assertEqual(row["cost"], 12.0)
            self.assertEqual(row["value"]["kind"], "git")
            self.assertEqual(row["dir"], os.path.abspath(repo))
```

- [ ] **Step 3: Run to verify it fails**

Run: `python3 scripts/test_value.py -k DirectoriesShape`
Expected: FAIL (`assemble_directories` undefined).

- [ ] **Step 4: Implement `assemble_directories` and call it in `build()`**

Add to `token-dashboard.py`:
```python
def assemble_directories(mangled_by_label, tokens_by_label, by_project_day_cost,
                         window_by_label, tool_by_label):
    """Build the unified per-directory rows (cost + tool-agnostic value)."""
    rows = []
    for label, mangled in mangled_by_label.items():
        real = _value.resolve_real_dir(mangled)
        start, end = window_by_label.get(label, (None, None))
        daycost = by_project_day_cost.get(label, {})
        cost = round(sum(daycost.values()), 2) if daycost else None
        tool = tool_by_label.get(label, "claude-code")
        value = (_value.cached_dir_value(real, label, tool, start, end)
                 if real else _value._empty_value())
        rows.append({
            "dir": real, "label": label, "tool": tool,
            "cost": cost, "tokens": tokens_by_label.get(label, _empty()),
            "window": {"start": start, "end": end}, "value": value,
        })
    rows.sort(key=lambda r: -(r["cost"] or 0))
    return rows
```

In `build()`, after computing `by_project` / `by_project_day_cost`, replace the `value_store = _shipped.refresh_store()...` block (line 277) with:
```python
    mangled_by_label, tokens_by_label, window_by_label, tool_by_label = {}, {}, {}, {}
    for s in new_cache.values():
        label = s.get("project", "?")
        mangled_by_label.setdefault(label, s.get("projdir", ""))
        tk = tokens_by_label.setdefault(label, _empty())
        t = s["totals"]
        _add(tk, t["input"], t["output"], t["cache_read"], t["cache_write"])
        tool_by_label[label] = "claude-code"
        days = sorted(d for d in s.get("by_day", {}) if d != "unknown")
        if days:
            lo, hi = window_by_label.get(label, (days[0], days[-1]))
            window_by_label[label] = (min(lo, days[0]), max(hi, days[-1]))
    directories = assemble_directories(
        mangled_by_label, tokens_by_label, by_project_day_cost,
        window_by_label, tool_by_label)
```

In the returned dict: remove `"value_store": value_store`, add `"directories": directories`. Keep `"by_project_day_cost": by_project_day_cost`.

- [ ] **Step 5: Delete dead registry code & endpoints**

Delete `value_panel`, `_top_items` (lines ~361–406). Keep `_window_cost` (still handy) or delete if unused — delete it to stay DRY if no caller remains. In `_client_data` change `drop = {"by_project_day_cost", "value_store"}` → `drop = set()` (we now send `directories` and a trimmed `by_project_day_cost`; to keep payload small, instead send `by_project_day_cost` for only the top 12 dirs — build that trimmed map in `_client_data`). In `do_GET`, delete the `elif self.path.startswith("/api/value")` branch.

`_client_data`:
```python
def _client_data(data):
    """Token data for the browser. Trim per-day cost to the dirs we chart."""
    top = [d["label"] for d in data.get("directories", [])[:12]]
    bpd = {k: v for k, v in data.get("by_project_day_cost", {}).items() if k in top}
    out = {k: v for k, v in data.items() if k != "by_project_day_cost"}
    out["by_project_day_cost"] = bpd
    return out
```

- [ ] **Step 6: Run to verify it passes + `--print` smoke**

Run: `python3 scripts/test_value.py -k DirectoriesShape`
Expected: PASS.
Run: `python3 scripts/token-dashboard.py --print | head -20`
Expected: prints the summary without error (directories computed live).
Run: `node --test`
Expected: PASS.

- [ ] **Step 7: Commit** (Task 0 sequence)

Message: `feat(dashboard): unified per-directory cost+value; drop registry value panel`

---

### Task 7: AI summaries — background pass over local `claude` CLI

**Files:**
- Create: `scripts/_summaries.py`
- Modify: `scripts/token-dashboard.py` (kick off the pass after each rebuild)
- Test: `scripts/test_value.py`

**Interfaces:**
- Consumes: `_value.load_store`, `_value.save_store`.
- Produces:
  - `_summaries.summarize_text(subjects, files_count) -> str` — builds the prompt.
  - `_summaries.run_claude(prompt, cli="claude", timeout=60) -> str | None` — shells out; `None` on any failure/absence.
  - `_summaries.backfill(limit=20, runner=run_claude) -> int` — for each store dir whose `value.summary is None` and that has git subjects (or fs files), generate and persist a one-liner; returns count written. `runner` is injectable for tests.

- [ ] **Step 1: Write the failing test (CLI mocked both ways)**

```python
class SummariesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (_shipped.STORE_DIR, _shipped.STORE_PATH)
        _shipped.STORE_DIR = self.tmp.name
        _shipped.STORE_PATH = os.path.join(self.tmp.name, "value.json")
        import importlib
        self.sm = importlib.import_module("_summaries")
        _shipped.save_store({"version": 2, "dirs": {"/x": {
            "label": "~/x", "tool": "claude-code", "head": "abc",
            "window": {"start": None, "end": None},
            "value": {"kind": "git", "commits": 1, "subjects": ["feat: a"],
                      "fs_files": 0, "summary": None}, "scanned": "t"}}})

    def tearDown(self):
        _shipped.STORE_DIR, _shipped.STORE_PATH = self._orig
        self.tmp.cleanup()

    def test_backfill_writes_summary(self):
        n = self.sm.backfill(runner=lambda prompt: "shipped feature a")
        self.assertEqual(n, 1)
        v = _shipped.load_store()["dirs"]["/x"]["value"]
        self.assertEqual(v["summary"], "shipped feature a")

    def test_backfill_graceful_when_cli_absent(self):
        n = self.sm.backfill(runner=lambda prompt: None)  # CLI absent / failed
        self.assertEqual(n, 0)
        self.assertIsNone(_shipped.load_store()["dirs"]["/x"]["value"]["summary"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 scripts/test_value.py -k Summaries`
Expected: FAIL (`No module named '_summaries'`).

- [ ] **Step 3: Implement**

```python
#!/usr/bin/env python3
"""_summaries.py — non-blocking AI one-liners for the value layer.

Shells out to the local `claude` CLI to summarize what each directory's window of
work accomplished, caching the result in the value store. Pure degradation: if the
CLI is missing or errors, summaries stay None and the dashboard shows git/fs facts.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _value  # noqa: E402


def summarize_text(subjects, files_count):
    body = "\n".join(f"- {s}" for s in subjects) or f"{files_count} files changed"
    return ("In ONE sentence (max 12 words, no period), say what this work "
            "accomplished. Commit subjects:\n" + body)


def run_claude(prompt, cli="claude", timeout=60):
    try:
        r = subprocess.run([cli, "-p", prompt], capture_output=True, text=True,
                           timeout=timeout)
        out = (r.stdout or "").strip()
        return out or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def backfill(limit=20, runner=run_claude):
    store = _value.load_store()
    written = 0
    for real_dir, e in store.get("dirs", {}).items():
        if written >= limit:
            break
        v = e.get("value", {})
        if v.get("summary") is not None:
            continue
        if not v.get("subjects") and not v.get("fs_files"):
            continue
        text = runner(summarize_text(v.get("subjects", []), v.get("fs_files", 0)))
        if text:
            v["summary"] = text
            written += 1
    if written:
        _value.save_store(store)
    return written
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 scripts/test_value.py -k Summaries`
Expected: PASS (both).

- [ ] **Step 5: Wire into the rebuild (non-blocking)**

In `token-dashboard.py`, import alongside `_value`:
```python
import _summaries  # noqa: E402
```
In `_rebuild()` after `Handler.data = build(...)`, spawn a daemon thread so summaries never block serving:
```python
def _rebuild():
    with _build_lock:
        Handler.data = build(verbose=False)
    threading.Thread(target=lambda: _summaries.backfill(), daemon=True).start()
    return Handler.data
```

- [ ] **Step 6: Run tests + print smoke**

Run: `python3 scripts/test_value.py`
Expected: PASS (full suite).
Run: `node --test`
Expected: PASS.

- [ ] **Step 7: Commit** (Task 0 sequence)

Message: `feat(value): cached AI summaries via local claude CLI (non-blocking, degrades)`

---

### Task 8: Frontend — leverage scatter hero, three charts, unified table

**Files:**
- Modify: `scripts/token-dashboard.py` (`PAGE` constant: `:root` tokens, remove `valueHTML`/`loadValue`/`/api/value` fetch, add SVG chart helpers + `dirsTable`)
- Verify: manual (load the dashboard) + `--print` unaffected

**Interfaces:**
- Consumes: `/api/data` now carrying `directories` and trimmed `by_project_day_cost`.

This task is UI-only (no Python logic change). Build it incrementally and eyeball each chart. No unit test; verify by loading `http://127.0.0.1:8787` and checking each chart renders with real data and degrades to an empty-state when a section is empty.

- [ ] **Step 1: Swap the palette + type tokens**

Replace the `:root{...}` block and base `body` font:
```css
:root{--ink:#0E1116;--surface:#171B22;--line:#262C36;--text:#E6E9EF;--muted:#8A93A2;
--cost:#E8B24A;--value:#5BD0A6;--warn:#E5704B;
--in:#58a6ff;--out:#f778ba;--cr:#3fb950;--cw:#d29922}
*{box-sizing:border-box}
body{margin:0;background:var(--ink);color:var(--text);
font:14px/1.55 'IBM Plex Sans',-apple-system,Segoe UI,Roboto,sans-serif}
.num,td.n,.money{font-family:'IBM Plex Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}
```
Update `--bg`→`--ink`, `--card`→`--surface`, `--bd`→`--line`, `--fg`→`--text`, `--mut`→`--muted` references throughout the existing CSS/JS (search-replace). Keep `IBM Plex` as a system-stack fallback (no CDN — if the user has the font it's used, else falls back; offline constraint preserved).

- [ ] **Step 2: Add reusable inline-SVG helpers in the `<script>`**

```js
function svgEl(w,h,inner,label){return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" role="img" aria-label="${esc(label)}" style="max-width:100%">${inner}</svg>`;}
// 1) LEVERAGE scatter — value (commits+prs) vs cost, with break-even diagonal
function leverageChart(dirs){
 const pts=dirs.filter(d=>d.cost!=null&&d.value).map(d=>({x:d.cost,
   y:(d.value.commits||0)+3*(d.value.prs||0)+ (d.value.fs_files||0?1:0), d}));
 if(!pts.length) return emptyState('No cost-bearing directories yet.');
 const W=520,H=300,P=36, mx=Math.max(...pts.map(p=>p.x),1), my=Math.max(...pts.map(p=>p.y),1);
 const X=x=>P+(W-2*P)*x/mx, Y=y=>H-P-(H-2*P)*y/my;
 // break-even: value proportional to cost (median ratio)
 const ratios=pts.map(p=>p.y/(p.x||1)).sort((a,b)=>a-b), r=ratios[ratios.length>>1]||1;
 const line=`<line x1="${X(0)}" y1="${Y(0)}" x2="${X(mx)}" y2="${Y(r*mx)}" stroke="var(--muted)" stroke-dasharray="4 4"/>`;
 const dots=pts.map(p=>{const above=p.y>=r*p.x; const col=above?'var(--value)':'var(--warn)';
   return `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="5" fill="${col}" fill-opacity=".85"><title>${esc(p.d.label)} · $${Math.round(p.x)} · ${p.d.value.commits||0} commits</title></circle>`;}).join('');
 const ax=`<line x1="${P}" y1="${H-P}" x2="${W-P}" y2="${H-P}" stroke="var(--line)"/><line x1="${P}" y1="${P}" x2="${P}" y2="${H-P}" stroke="var(--line)"/>`;
 return svgEl(W,H,ax+line+dots,'Value versus cost by directory; points above the dashed break-even line ship more per token');
}
function emptyState(msg){return `<p class=muted style="padding:24px 0">${esc(msg)}</p>`;}
```

- [ ] **Step 3: Add the three secondary charts**

```js
// 2) COST OVER TIME — stacked area by directory (top dirs only; from by_project_day_cost)
function costOverTime(d){
 const bpd=d.by_project_day_cost||{}; const days=[...new Set(Object.values(bpd).flatMap(o=>Object.keys(o)))].sort();
 if(!days.length) return emptyState('No dated cost yet.');
 const labels=Object.keys(bpd); const W=520,H=200,P=28;
 const totalByDay=days.map(day=>labels.reduce((a,l)=>a+(bpd[l][day]||0),0));
 const mx=Math.max(...totalByDay,1); const X=i=>P+(W-2*P)*i/Math.max(days.length-1,1);
 const Y=v=>H-P-(H-2*P)*v/mx;
 const path=`M${days.map((day,i)=>`${X(i)},${Y(totalByDay[i])}`).join(' L')}`;
 const area=`${path} L${X(days.length-1)},${H-P} L${X(0)},${H-P} Z`;
 return svgEl(W,H,`<path d="${area}" fill="var(--cost)" fill-opacity=".18"/><path d="${path}" fill="none" stroke="var(--cost)" stroke-width="2"/>`,
   `Total token cost per day over ${days.length} days`);
}
// 3) TOKEN-PURPOSE split — input/output/cache-read/cache-write (overall)
function purposeSplit(t){
 const parts=[['cache_read',t.cache_read,'var(--cr)'],['cache_write',t.cache_write,'var(--cw)'],
   ['input',t.input,'var(--in)'],['output',t.output,'var(--out)']];
 const sum=parts.reduce((a,p)=>a+p[1],0)||1; let x=0; const W=520,H=34;
 const segs=parts.map(([k,v,c])=>{const w=(W)*v/sum; const r=`<rect x="${x}" y="0" width="${w}" height="${H}" fill="${c}"><title>${k}: ${fmt(v)}</title></rect>`; x+=w; return r;}).join('');
 return svgEl(W,H,segs,'Share of tokens by purpose: cache read, cache write, input, output');
}
// 4) COST BY DIRECTORY — ranked bars
function costByDir(dirs){
 const rows=dirs.filter(d=>d.cost!=null).slice(0,12); if(!rows.length) return emptyState('No cost yet.');
 const mx=Math.max(...rows.map(r=>r.cost),1); const H=rows.length*26+8,W=520;
 const bars=rows.map((r,i)=>{const w=(W-160)*r.cost/mx; const y=i*26+4;
   return `<text x="0" y="${y+14}" fill="var(--muted)" font-size="12">${esc(r.label.slice(-26))}</text>`+
     `<rect x="150" y="${y+3}" width="${w}" height="14" rx="3" fill="var(--cost)"/>`+
     `<text x="${156+w}" y="${y+14}" fill="var(--text)" font-size="11">$${Math.round(r.cost)}</text>`;}).join('');
 return svgEl(W,H,bars,'Estimated token cost by directory, highest first');
}
```

- [ ] **Step 4: Add the unified directories table + assemble the page**

```js
function toolBadge(t){const m={'claude-code':'CC','codex':'CX'}; return `<span class=badge title="${esc(t)}">${m[t]||'?'}</span>`;}
function dirsTable(dirs){
 let h=`<h2>All directories <span class=muted style="text-transform:none;font-weight:400">— cost (amber) × value shipped (green); — = no local token data for that tool</span></h2>`;
 h+=`<table><tr><th>directory</th><th>tool</th><th>est $</th><th>value (shipped)</th><th>AI note</th></tr>`;
 for(const d of dirs){
  const v=d.value||{}; const shipped = v.kind==='git'
    ? `${v.commits||0} commits${v.prs?'·'+v.prs+' PRs':''}`
    : v.kind==='fs' ? `${v.fs_files} files` : '—';
  h+=`<tr><td>${esc(d.label)}</td><td>${toolBadge(d.tool)}</td>`+
     `<td class=money>${d.cost==null?'<span class=muted>—</span>':'$'+Math.round(d.cost).toLocaleString()}</td>`+
     `<td style="color:var(--value)">${esc(shipped)}</td>`+
     `<td class=muted>${v.summary?esc(v.summary):'<span class=muted>—</span>'}</td></tr>`;
 }
 return h+'</table>';
}
```

In `render(d)`, after the summary cards, replace the old `valueHTML()` call and the by-project section with:
```js
 h+=`<div class=cards2>
   <section><h2>Leverage — value vs cost</h2>${leverageChart(d.directories||[])}</section>
   <section><h2>Cost over time</h2>${costOverTime(d)}</section>
   <section><h2>Token-purpose split</h2>${purposeSplit(d.totals)}${legend()}</section>
   <section><h2>Cost by directory</h2>${costByDir(d.directories||[])}</section>
 </div>`;
 h+=dirsTable(d.directories||[]);
```
Add CSS: `.cards2{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:8px 0 24px}@media(max-width:760px){.cards2{grid-template-columns:1fr}} .badge{display:inline-block;font:600 10px/1 'IBM Plex Mono';padding:3px 5px;border:1px solid var(--line);border-radius:4px;color:var(--muted)}`
Delete `let VALUE=null; async function loadValue(){...}` and the `valueHTML` function and the `Promise.all([...loadValue()])` — `load()`/`refresh()` now fetch only `/api/data`:
```js
async function load(){render(await (await fetch('/api/data')).json());}
async function refresh(){document.getElementById('app').innerHTML='<p class=muted>Rescanning…</p>';render(await (await fetch('/api/refresh')).json());}
```
Keep the old "By project / By model / Last N days" tables below if desired, recoloring to the new tokens; the spec's table supersedes "By project", so **remove "By project"** and keep "By model" + "Last N active days".

- [ ] **Step 5: Verify in the browser**

Run: `python3 scripts/token-dashboard.py` then open `http://127.0.0.1:8787`.
Expected: hero leverage scatter renders with green/red dots and a dashed break-even line; cost-over-time area, purpose split, and cost-by-dir bars all render; the all-directories table lists repos (CC badge, commits) and any non-repo dirs (— cost, files count). Resize to 375px — charts reflow to one column, no horizontal scroll. Toggle `prefers-reduced-motion` — no animation regressions (there are none added).
Run: `python3 scripts/token-dashboard.py --print | head`
Expected: still works (unchanged text path).

- [ ] **Step 6: Commit** (Task 0 sequence)

Message: `feat(dashboard): leverage hero + 4 SVG charts + unified all-directories table`

---

### Task 9: Rewrite `value-report.py` as a thin CLI over `_value.py`

**Files:**
- Modify: `scripts/value-report.py` (full rewrite)
- Test: `scripts/test_value.py` (smoke via subprocess) — optional light test

**Interfaces:**
- Consumes: `_value.dir_value`, `_value.git_head`, `_value.project_label_for_path`.

- [ ] **Step 1: Rewrite the CLI (no registration step)**

```python
#!/usr/bin/env python3
"""
value-report.py — what a directory SHIPPED, to read alongside its token cost.

Tool-agnostic: derives value from git history (or filesystem activity for non-repos)
in a date window. No CHANGELOG requirement, no registration — the token dashboard
discovers directories automatically.

Usage:
    python3 scripts/value-report.py                 # current dir (cwd)
    python3 scripts/value-report.py /path/to/dir    # a specific directory
    python3 scripts/value-report.py --since 2026-06-01 --until 2026-06-21
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _value  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("directory", nargs="?", default=os.getcwd())
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    args = ap.parse_args()
    d = os.path.abspath(os.path.expanduser(args.directory))
    label = _value.project_label_for_path(d)
    v = _value.dir_value(d, label, "claude-code", args.since, args.until)
    print(f"\nValue report — {os.path.basename(d) or d}  ({d})")
    print(f"  label: {label}")
    if v["kind"] == "git":
        print(f"  git: {v['commits']} commits · {v['prs']} PRs · {v['files']} files "
              f"(+{v['insertions']}/-{v['deletions']})")
        for s in v["subjects"]:
            print(f"     • {s}")
    elif v["kind"] == "fs":
        print(f"  files touched (mtime): {v['fs_files']}  (not a git repo — estimate)")
    else:
        print("  no git history or recent file activity in window")
    print()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-run**

Run: `python3 scripts/value-report.py .`
Expected: prints this repo's git commit/PR/file counts without error.

- [ ] **Step 3: Commit** (Task 0 sequence)

Message: `refactor(value-report): thin tool-agnostic CLI over _value (no registration)`

---

### Task 10: Tests sweep + docs + CHANGELOG

**Files:**
- Modify: `scripts/test_value.py` (remove the temporary `import _value as _shipped` alias → import `_value` directly; ensure all new test classes use `_value`), `README.md`, `docs/token-optimization.md`, `CHANGELOG.md`

- [ ] **Step 1: De-alias the test module**

Replace `import _value as _shipped` → `import _value` and `_shipped.` → `_value.` throughout `test_value.py`. (Do this last so earlier tasks could keep the alias.)

- [ ] **Step 2: Full suite green**

Run: `python3 scripts/test_value.py`
Expected: PASS — `LabelTest`, `ResolveDirTest`, `GitValueTest`, `DirValueTest`, `AdapterTest`, `DirectoriesShapeTest`, `SummariesTest`.
Run: `node --test`
Expected: PASS.

- [ ] **Step 3: Update docs**

In `README.md` and `docs/token-optimization.md`, replace any description of the registry/`100x-value`-registration value panel with: the dashboard now shows **every** directory that spent tokens (repo or not), with value derived automatically from git + filesystem activity, AI one-liners, and four charts; cost is Claude-Code-only today (other tools show value with `—` cost). Remove "run `100x-value` in a repo to register it" instructions.

- [ ] **Step 4: CHANGELOG entry**

Add under a new `## [Unreleased]` (or next version) heading:
```markdown
### Changed
- **Token dashboard is now value-first across every directory.** Replaced the registry/CHANGELOG-gated "Value — cost vs. what shipped" panel with one automatic view over *every* directory that consumed tokens — repo or not. Value is derived tool-agnostically from git history (commits / PRs / files / churn) with a filesystem-mtime fallback for non-repos, so work done by any tool or by hand is counted. Cost stays Claude-Code-only (the only tool that writes local token accounting); directories from tools without local token data (Cursor, Antigravity) show value with `—` cost, never `$0`.
- **Cost is now behind pluggable per-tool adapters** (`scripts/adapters/`): `claude_code` is real; `codex` is a documented stub for when the Codex CLI is used locally.
### Added
- Four inline-SVG charts (zero-dependency): a **leverage** scatter (value vs cost with a break-even line), cost-over-time, token-purpose split, and cost-by-directory.
- Cached AI one-liners per directory via the local `claude` CLI (non-blocking background pass; degrades silently when absent).
### Removed
- `_shipped.py`, the manual `100x-value` registration step, and the registry `source` field — superseded by automatic per-directory derivation in `_value.py`.
```

- [ ] **Step 5: Commit** (Task 0 sequence)

Message: `docs+test: tool-agnostic all-directory value view; update README/CHANGELOG`

---

## Self-Review

**1. Spec coverage:**
- All directories (repo + non-repo) → Tasks 2 (resolution), 6 (`assemble_directories` over every label). ✅
- Git value → Task 3. ✅ Filesystem fallback → Task 4 (`fs_value`). ✅
- AI summaries (local claude CLI, cached, non-blocking) → Task 7. ✅
- Replace registry/CHANGELOG → Tasks 1 (strip), 6 (remove `value_panel`), 9 (CLI), 10 (docs/CHANGELOG). ✅
- value.json registry → automatic cache keyed by dir+head+window → Task 4. ✅
- Cross-tool adapters (claude real, codex stub, cursor/antigravity = value-only `—`) → Task 5 + Task 6 (`tool` field) + Task 8 (`—` rendering). ✅
- Four charts (leverage hero, cost-over-time, purpose split, cost-by-dir), inline SVG, a11y → Task 8. ✅
- UI tokens (amber=cost, green=value, warn-red), IBM Plex + tabular figures → Task 8 steps 1–2. ✅
- Zero-dependency / offline / serve path never blocks → Global Constraints; git+summaries on rebuild tick (Tasks 6–7). ✅

**2. Placeholder scan:** The only `TODO` is intentional — the `codex` stub body (Task 5), which is a documented not-yet-built adapter explicitly scoped out in the spec, not a plan gap. All code steps contain complete code.

**3. Type consistency:** `Usage` namedtuple fields (Task 5) match `iter_dir_days` usage. `dir_value`/`cached_dir_value`/`_empty_value` value-block keys (Task 4) match the Data Contract and the frontend's `d.value.*` reads (Task 8). `assemble_directories` row keys (Task 6) match `dirsTable`/chart reads (Task 8). `git_value` return keys (Task 3) are consumed unchanged by `dir_value` (Task 4). Store shape (`version:2`, `dirs`) is written and read consistently across Tasks 4, 7, 9. `_value.project_label*` preserved from Task 1 keeps the `LabelTest` invariant.

---

## Execution Handoff

(Choose after review — see skill prompt.)
