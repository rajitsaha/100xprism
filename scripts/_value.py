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


# ----------------------------------------------------------------- path resolution

def resolve_real_dir(mangled_dirname, root="/"):
    """Best-effort un-mangle of a ~/.claude/projects dir name to a real path.

    The mangling joins path parts with '-' and also turns every literal '-' in a
    segment into '-', so it is ambiguous. We walk the tokens left-to-right and at
    each step greedily extend the current segment with '-' as long as no child
    directory matches by '/'. Returns an absolute existing path, or None.
    """
    tokens = mangled_dirname.strip("-").split("-")
    if not tokens or tokens == [""]:
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
            extend = (
                (j + 1 < len(tokens) and os.path.isdir(os.path.join(cur, seg + "-" + tokens[j + 1])))
                or (j + 1 < len(tokens) and not os.path.isdir(cand))
            )
            if extend:
                j += 1
                seg = seg + "-" + tokens[j]
                continue
            break
        if matched is None:
            # No existing child at this level → can't resolve further.
            return None
        cur, i = matched[0], matched[1] + 1
    return cur if os.path.isdir(cur) else None


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
