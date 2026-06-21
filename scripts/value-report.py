#!/usr/bin/env python3
"""
value-report.py — what SHIPPED, to read alongside token cost.

The token dashboard measures *cost*. This measures *delivered value* — what a
repo shipped, from its CHANGELOG.md plus the unreleased commits on top of the
last release.

Running it also REGISTERS this repo in the central store (~/.100xprism/value.json)
so the `100x-tokens` dashboard can show this value side by side with what it
cost in tokens — one URL, cost next to value.

Offline, no third-party dependencies.

Usage:
    python3 scripts/value-report.py                 # current repo (cwd)
    python3 scripts/value-report.py /path/to/repo   # a specific repo
    python3 scripts/value-report.py --versions 8    # how many releases to show
    python3 scripts/value-report.py --no-register   # print only, don't touch the store
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _shipped  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?", default=os.getcwd())
    ap.add_argument("--versions", type=int, default=5)
    ap.add_argument("--no-register", action="store_true",
                    help="print only; do not write this repo to the central store")
    args = ap.parse_args()

    v = _shipped.repo_value(args.repo, versions=args.versions)
    repo, name = v["path"], v["name"]
    print(f"\nValue report — {name}  ({repo})")

    if v["releases"]:
        print(f"\nShipped (last {len(v['releases'])} release(s), from CHANGELOG.md):")
        for r in v["releases"]:
            head = f"  v{r['version']}" + (f"  ·  {r['date']}" if r["date"] else "")
            counts = "  ".join(f"{len(items)} {sec.lower()}"
                               for sec, items in r["sections"].items() if items)
            print(f"\n{head}" + (f"   [{counts}]" if counts else ""))
            for sec, items in r["sections"].items():
                for it in items[:3]:
                    line = _shipped._strip_md(it)
                    print(f"     • {line[:96] + ('…' if len(line) > 96 else '')}")
                if len(items) > 3:
                    print(f"     • …+{len(items) - 3} more in {sec}")
    else:
        print("\n  No CHANGELOG.md found (or empty).")

    un = v["unreleased"]
    if un is None:
        pass  # not a git repo
    elif un["count"]:
        print(f"\nUnreleased work since {un['since']}: {un['count']} commit(s)")
        for typ, n in sorted(un["buckets"].items(), key=lambda kv: -kv[1]):
            print(f"  {n:>3}  {typ}")
    else:
        print(f"\nUnreleased work: none since {un['since']}.")

    if not args.no_register:
        _shipped.save_repo(v)
        print(f"\n  registered → {_shipped.STORE_PATH} (shows in the 100x-tokens dashboard)")
    print()


if __name__ == "__main__":
    main()
