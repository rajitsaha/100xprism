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
