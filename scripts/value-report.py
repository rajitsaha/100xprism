#!/usr/bin/env python3
"""
value-report.py — what SHIPPED, to read alongside token cost.

The token dashboard measures *cost*. This measures *delivered value* — the part
no token tool can know, because it isn't in the transcripts. It summarizes what a
repo shipped from its CHANGELOG.md (Keep a Changelog format) and, when available,
recent git history bucketed by conventional-commit type.

Offline, no third-party dependencies.

Usage:
    python3 scripts/value-report.py                 # current repo (cwd)
    python3 scripts/value-report.py /path/to/repo   # a specific repo
    python3 scripts/value-report.py --versions 8    # how many releases to show
"""
import argparse
import os
import re
import subprocess
import sys

VERSION_RE = re.compile(r"^##\s*\[([^\]]+)\]\s*(?:[—-]\s*(.+))?$")
SECTION_RE = re.compile(r"^###\s+(.+)$")
BULLET_RE = re.compile(r"^[-*]\s+(.+)$")
CC_RE = re.compile(r"^([a-z]+)(?:\([^)]*\))?!?:")


def parse_changelog(path, limit):
    """Return [{version, date, sections:{name:[items]}}] for the newest releases."""
    releases = []
    cur = None
    cur_section = None
    try:
        lines = open(path, encoding="utf-8", errors="ignore").read().splitlines()
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


def _strip_md(s):
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return s.strip()


def git_summary(repo):
    """Commits since the latest tag, bucketed by conventional-commit type."""
    def git(*a):
        return subprocess.run(["git", "-C", repo, *a], capture_output=True,
                              text=True, timeout=10)
    try:
        if git("rev-parse", "--is-inside-work-tree").returncode != 0:
            return None
        tag = git("describe", "--tags", "--abbrev=0").stdout.strip()
        rng = f"{tag}..HEAD" if tag else "HEAD"
        out = git("log", rng, "--no-merges", "--pretty=%s").stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    subjects = [s for s in out.splitlines() if s]
    buckets = {}
    for s in subjects:
        m = CC_RE.match(s)
        buckets.setdefault(m.group(1) if m else "other", []).append(s)
    return {"since_tag": tag, "count": len(subjects), "buckets": buckets}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?", default=os.getcwd())
    ap.add_argument("--versions", type=int, default=5)
    args = ap.parse_args()

    repo = os.path.abspath(os.path.expanduser(args.repo))
    name = os.path.basename(repo.rstrip("/"))
    print(f"\nValue report — {name}  ({repo})")

    changelog = os.path.join(repo, "CHANGELOG.md")
    releases = parse_changelog(changelog, args.versions)
    if releases:
        print(f"\nShipped (last {len(releases)} release(s), from CHANGELOG.md):")
        for r in releases:
            head = f"  v{r['version']}" + (f"  ·  {r['date']}" if r["date"] else "")
            counts = "  ".join(f"{n} {s.lower()}" for s, n in
                               ((s, len(items)) for s, items in r["sections"].items()) if n)
            print(f"\n{head}" + (f"   [{counts}]" if counts else ""))
            for sec, items in r["sections"].items():
                for it in items[:3]:
                    line = _strip_md(it)
                    print(f"     • {line[:96] + ('…' if len(line) > 96 else '')}")
                if len(items) > 3:
                    print(f"     • …+{len(items) - 3} more in {sec}")
    else:
        print("\n  No CHANGELOG.md found (or empty).")

    g = git_summary(repo)
    if g and g["count"]:
        since = f"since {g['since_tag']}" if g["since_tag"] else "(no tags)"
        print(f"\nUnreleased work {since}: {g['count']} commit(s)")
        for typ, subs in sorted(g["buckets"].items(), key=lambda kv: -len(kv[1])):
            print(f"  {len(subs):>3}  {typ}")
    elif g is not None:
        print("\nUnreleased work: none since the latest tag.")
    print()


if __name__ == "__main__":
    main()
