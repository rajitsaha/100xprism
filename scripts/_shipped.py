#!/usr/bin/env python3
"""
_shipped.py — shared "what shipped" + central value store.

Single source of truth for the value side of token & value economics, used by
both `value-report.py` (the CLI) and `token-dashboard.py` (the web UI), so the
two never drift.

It knows three things:

  1. What a repo SHIPPED — parsed from CHANGELOG.md (Keep a Changelog) and recent
     git history, bucketed by conventional-commit type. The "unreleased" boundary
     is the most recent `chore(release):` commit (tag as fallback), so versions
     already documented in the CHANGELOG don't double-count as unreleased.

  2. The dashboard's project LABEL for a repo path — the same path→label mangling
     the token dashboard applies to transcript dirs, so a repo registered by the
     CLI joins to its token cost in the dashboard.

  3. The central store at ~/.100xprism/value.json — one machine-wide ledger of
     every repo's value snapshot, written by the CLI (source "registry") and the
     dashboard's auto-discovery (source "auto"; registry always wins).

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
STORE_VERSION = 1

VERSION_RE = re.compile(r"^##\s*\[([^\]]+)\]\s*(?:[—-]\s*(.+))?$")
SECTION_RE = re.compile(r"^###\s+(.+)$")
BULLET_RE = re.compile(r"^[-*]\s+(.+)$")
CC_RE = re.compile(r"^([a-z]+)(?:\([^)]*\))?!?:")

# The dashboard turns a transcript dir like "-Users-rajit-foo-bar" into a label.
# Mirror that transform here so registry entries share the dashboard's join key.
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


# ----------------------------------------------------------------- changelog

def _strip_md(s):
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return s.strip()


def parse_changelog(path, limit):
    """Return [{version, date, sections:{name:[items]}}] for the newest releases."""
    releases = []
    cur = None
    cur_section = None
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
    except OSError:
        return releases
    for line in lines:
        mv = VERSION_RE.match(line)
        if mv:
            if cur:
                releases.append(cur)
                if len(releases) >= limit:
                    return releases
            cur = {"version": mv.group(1), "date": (mv.group(2) or "").strip(), "sections": {}}
            cur_section = None
            continue
        if cur is None:
            continue
        ms = SECTION_RE.match(line)
        if ms:
            cur_section = ms.group(1).strip()
            cur["sections"].setdefault(cur_section, [])
            continue
        mb = BULLET_RE.match(line)
        if mb:
            sec = cur_section or "Notes"
            cur["sections"].setdefault(sec, []).append(mb.group(1).strip())
    if cur and len(releases) < limit:
        releases.append(cur)
    return releases


# ----------------------------------------------------------------- git

def git_summary(repo):
    """Commits not yet released, bucketed by conventional-commit type.

    The boundary is the most recent `chore(release):` commit — that matches how
    releases are actually cut, so it stays correct even when git tags lag the
    CHANGELOG. Falls back to the latest tag, then to the whole history.
    """
    def git(*a):
        return subprocess.run(["git", "-C", repo, *a], capture_output=True,
                              text=True, timeout=10)
    try:
        if git("rev-parse", "--is-inside-work-tree").returncode != 0:
            return None
        rel = git("log", "--grep=^chore(release)", "-n", "1", "--pretty=%H").stdout.strip()
        if rel:
            boundary, since = rel, "last release"
        else:
            tag = git("describe", "--tags", "--abbrev=0").stdout.strip()
            boundary, since = tag, (tag or "(no releases)")
        rng = f"{boundary}..HEAD" if boundary else "HEAD"
        out = git("log", rng, "--no-merges", "--pretty=%s").stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    subjects = [s for s in out.splitlines() if s]
    buckets = {}
    for s in subjects:
        m = CC_RE.match(s)
        buckets.setdefault(m.group(1) if m else "other", []).append(s)
    return {"since": since, "count": len(subjects), "buckets": buckets}


# ----------------------------------------------------------------- value snapshot

def repo_value(repo_path, versions=5, source="registry"):
    """Full value snapshot for one repo: releases (CHANGELOG) + unreleased (git)."""
    repo = os.path.abspath(os.path.expanduser(repo_path))
    name = os.path.basename(repo.rstrip("/")) or repo
    releases = parse_changelog(os.path.join(repo, "CHANGELOG.md"), versions)
    g = git_summary(repo)
    unreleased = None
    if g is not None:
        unreleased = {"since": g["since"], "count": g["count"],
                      "buckets": {k: len(v) for k, v in g["buckets"].items()}}
    return {
        "path": repo,
        "name": name,
        "label": project_label_for_path(repo),
        "source": source,
        "scanned": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "releases": [
            {"version": r["version"], "date": r["date"], "sections": r["sections"],
             "items": sum(len(v) for v in r["sections"].values())}
            for r in releases
        ],
        "unreleased": unreleased,
    }


# ----------------------------------------------------------------- store I/O

def load_store():
    try:
        with open(STORE_PATH, encoding="utf-8") as f:
            s = json.load(f)
        if s.get("version") == STORE_VERSION and isinstance(s.get("repos"), dict):
            return s
    except (OSError, ValueError):
        pass
    return {"version": STORE_VERSION, "repos": {}}


def save_store(store):
    try:
        os.makedirs(STORE_DIR, exist_ok=True)
        with open(STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
    except OSError as e:
        print(f"warning: could not write value store ({STORE_PATH}): {e}", file=sys.stderr)


def save_repo(entry):
    """Upsert one repo (keyed by its path). A registry entry is never overwritten
    by an auto entry."""
    store = load_store()
    path = entry["path"]
    existing = store["repos"].get(path)
    if existing and existing.get("source") == "registry" and entry.get("source") == "auto":
        return store
    store["repos"][path] = {k: v for k, v in entry.items() if k != "path"}
    save_store(store)
    return store


def autodiscover(known_paths):
    """Best-effort: transcript dirs whose `-`→`/` path is a git repo with a
    CHANGELOG. Hyphenated path segments can't be recovered (the mangling is
    lossy), so the registry remains the source of truth — this only adds the
    repos it can prove exist on disk."""
    import glob
    found = []
    for d in glob.glob(os.path.join(PROJECTS_DIR, "*")):
        if not os.path.isdir(d):
            continue
        cand = "/" + os.path.basename(d).lstrip("-").replace("-", "/")
        if cand in known_paths or cand in found:
            continue
        if os.path.isdir(os.path.join(cand, ".git")) and \
           os.path.exists(os.path.join(cand, "CHANGELOG.md")):
            found.append(cand)
    return found


def refresh_store():
    """Re-scan every known repo (keeping its source) and add newly discoverable
    git repos, then persist once. Returns the fresh store."""
    store = load_store()
    known = dict(store.get("repos", {}))
    repos = {}
    for path, e in known.items():
        if os.path.isdir(path):
            v = repo_value(path, source=e.get("source", "registry"))
            repos[path] = {k: val for k, val in v.items() if k != "path"}
        else:
            repos[path] = e  # path gone — keep the last snapshot rather than drop it
    for path in autodiscover(set(known)):
        if path not in repos:
            v = repo_value(path, source="auto")
            repos[path] = {k: val for k, val in v.items() if k != "path"}
    store["repos"] = repos
    save_store(store)
    return store
