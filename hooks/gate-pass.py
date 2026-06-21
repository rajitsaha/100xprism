#!/usr/bin/env python3
"""Record (or print) the gate-state token for the current working tree.

Called by the /gate skill as its final step once ALL gates report PASSED, and by
/commit's Phase-0 cache check. Writes the token (sha256 of HEAD + tracked diff +
untracked status) to the single-file cache ~/.100xprism/gate-cache so the gate-on-commit
hook (pretooluse-gate.py) will allow the next commit/push — and only until the tree
changes.

Usage:
  python3 gate-pass.py [repo_dir]            # compute token for repo_dir (default: .) and record it
  python3 gate-pass.py --print [repo_dir]    # print the token only; do not write the cache
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from hooklib import gate_state_token, repo_root, write_cache  # noqa: E402


def main(argv: list[str]) -> int:
    args = argv[1:]
    print_only = "--print" in args
    positional = [a for a in args if not a.startswith("--")]
    start = positional[0] if positional else "."

    root = repo_root(start)
    if not root:
        sys.stderr.write("gate-pass: not inside a git repository — nothing recorded.\n")
        return 1

    token = gate_state_token(root)
    if print_only:
        print(token)
        return 0

    write_cache(token)
    print(f"✓ gate pass recorded for {root}")
    print("  (valid until the working tree changes or HEAD moves)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
