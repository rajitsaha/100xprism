"""Shared helpers for 100xprism first-party hooks.

Every hook is a small python3 script invoked by Claude Code with a JSON event on
stdin. This module centralises the three things they all need: reading that event,
talking to git, and the gate-cache token contract that ties /gate to the
gate-on-commit hook.

The gate-cache token is a sha256 over (HEAD + tracked diff + porcelain status). It
deliberately captures the *full* tree state — not just HEAD — so a pass recorded by
/gate is invalidated the moment a tracked file changes, an untracked file appears,
or HEAD moves (commit / rebase / pull). That closes the "stale cache for a dirty
tree" hole called out in the power-up review.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

# Single global cache file (existing 100xprism contract). Holds the gate-state token
# of the last tree /gate passed on. The token itself encodes HEAD, so it is repo- and
# tree-specific; switching repos or dirtying the tree simply fails the match (closed).
CACHE_FILE = Path(os.path.expanduser("~/.100xprism/gate-cache"))


def read_event() -> dict:
    """Parse the hook event JSON from stdin. Returns {} on empty / malformed input
    so a misfire never crashes the user's commit."""
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def tool_input(event: dict) -> dict:
    ti = event.get("tool_input")
    return ti if isinstance(ti, dict) else {}


def event_cwd(event: dict) -> str:
    cwd = event.get("cwd")
    return cwd if isinstance(cwd, str) and cwd else os.getcwd()


def git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )


def repo_root(cwd: str) -> str | None:
    r = git(["rev-parse", "--show-toplevel"], cwd)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def gate_state_token(root: str) -> str:
    """Stable fingerprint of the working tree: HEAD + tracked diff + untracked status."""
    head = git(["rev-parse", "HEAD"], root).stdout.strip() or "no-head"
    diff = git(["diff", "HEAD"], root).stdout
    status = git(["status", "--porcelain"], root).stdout
    h = hashlib.sha256()
    for part in (head, diff, status):
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


def read_cache() -> str | None:
    try:
        return CACHE_FILE.read_text().strip() or None
    except OSError:
        return None


def write_cache(token: str) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(token + "\n")


def allow() -> int:
    """Defer to Claude Code's normal permission flow (no decision)."""
    return 0


def block(reason: str) -> int:
    """Block the tool call. stderr is surfaced to Claude as the reason."""
    sys.stderr.write(reason.rstrip() + "\n")
    return 2


def auto_approve(reason: str) -> int:
    """Emit a PreToolUse allow decision so the call runs without a prompt."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": reason,
        }
    }))
    return 0
