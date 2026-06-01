#!/usr/bin/env python3
"""PreToolUse(Bash) — block `git commit` / `git push` unless /gate passed.

Turns the headline promise "nothing ships without passing the gate" from honor-system
prose into enforcement. /gate records a pass for the current tree (hooks/gate-pass.py);
this hook recomputes that fingerprint at commit time and blocks if it doesn't match.

Exit 0 = allow (not a commit/push, not a git repo, or gate passed for this exact tree).
Exit 2 = block with an explanation Claude sees.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from hooklib import (  # noqa: E402
    allow, block, event_cwd, gate_state_token, git, read_cache, read_event, repo_root, tool_input,
)

# A `git ... commit` or `git ... push` invocation anywhere in the command line.
# Tolerates global flags (`git -C dir commit`, `git --no-pager push`).
_COMMIT_OR_PUSH = re.compile(r"\bgit\b[^&|;\n]*?\b(commit|push)\b")
# Read-only / safe git subcommands that happen to contain the word, e.g.
# `git log --grep=commit` or `git config push.default` — never gated.
_SAFE_PREFIX = re.compile(r"\bgit\b\s+(?:-\S+\s+)*(log|show|config|help|diff|status|remote|branch)\b")


def is_commit_or_push(cmd: str) -> bool:
    if not _COMMIT_OR_PUSH.search(cmd):
        return False
    # If the only git invocation is a clearly read-only one, don't gate.
    if _SAFE_PREFIX.search(cmd) and not re.search(r"\bgit\b\s+(?:-\S+\s+)*(commit|push)\b", cmd):
        return False
    return True


def main() -> int:
    event = read_event()
    cmd = tool_input(event).get("command", "")
    if not isinstance(cmd, str) or not is_commit_or_push(cmd):
        return allow()

    cwd = event_cwd(event)
    root = repo_root(cwd)
    if not root:
        return allow()  # not a git repo — nothing to gate

    # `git commit --amend` with no tree change, and pushes, are still subject to the
    # gate: the cache is keyed on HEAD+tree so a fresh pass is always required.
    token = gate_state_token(root)
    cached = read_cache()
    if cached and cached == token:
        return allow()

    head = git(["rev-parse", "--short", "HEAD"], root).stdout.strip() or "(no commits)"
    return block(
        "⛔ 100x-dev gate hook: the quality gate has not passed for the current tree.\n"
        f"   repo: {root}  @ {head}\n"
        "   Run /gate and let ALL gates pass, then retry the commit/push.\n"
        "   (/gate records a pass for the exact tree state; any later edit re-arms this block.)"
    )


if __name__ == "__main__":
    sys.exit(main())
