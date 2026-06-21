#!/usr/bin/env python3
"""PreToolUse(Bash) — the subagents permission router, as a real artifact.

The subagents skill long described "route permission requests to a model to auto-approve
safe ones" but shipped nothing. This is that hook.

Tier 1 (always, offline): a deterministic allowlist auto-approves obviously read-only
commands (ls, cat, git status, grep, …) so safe calls don't prompt.
Tier 2 (optional): when HOOK_ROUTER_MODEL is set and the `claude` CLI is available,
ambiguous commands are classified by a cheap model (default Haiku 4.5); a clear "safe"
verdict auto-approves, anything else defers to the human. Routing only ever *grants*
permission it is confident about — it never blocks, so the normal prompt remains the
safe default for everything it's unsure about.

Exit 0 with an allow decision = auto-approved. Exit 0 with no output = defer to the
normal permission prompt. This hook never blocks.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from hooklib import allow, auto_approve, read_event, tool_input  # noqa: E402

# Binaries with no write/network side effects. git is handled separately (subcommand-aware).
_SAFE_BINS = {
    "ls", "pwd", "cat", "head", "tail", "wc", "echo", "grep", "egrep", "fgrep", "rg",
    "find", "which", "type", "file", "stat", "date", "whoami", "id", "uname", "hostname",
    "env", "printenv", "tree", "du", "df", "basename", "dirname", "realpath", "readlink",
    "true", "sort", "uniq", "cut", "column", "diff", "tldr", "man",
}
_SAFE_GIT_SUBCMDS = {
    "status", "log", "diff", "show", "branch", "remote", "rev-parse", "describe",
    "ls-files", "ls-tree", "blame", "shortlog", "tag", "config", "cat-file", "reflog",
}
# Constructs that can mutate state or chain into something risky → never auto-approve.
_DANGEROUS = re.compile(r"(>>|>|<|\$\(|`|\bsudo\b|\brm\b|\bmv\b|\bcp\b|\bdd\b|\bchmod\b|\bchown\b|\bkill\b|\bcurl\b|\bwget\b)")
_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\|")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")


def is_deterministically_safe(cmd: str) -> bool:
    if not cmd.strip() or _DANGEROUS.search(cmd):
        return False
    for segment in _SEGMENT_SPLIT.split(cmd):
        tokens = segment.split()
        # Drop leading `VAR=value` env prefixes.
        while tokens and _ENV_ASSIGN.match(tokens[0]):
            tokens = tokens[1:]
        if not tokens:
            continue
        binary = Path(tokens[0]).name
        if binary == "git":
            sub = next((t for t in tokens[1:] if not t.startswith("-")), "")
            if sub not in _SAFE_GIT_SUBCMDS:
                return False
        elif binary not in _SAFE_BINS:
            return False
    return True


def model_says_safe(cmd: str) -> bool:
    """Optional Tier 2: ask a cheap model. Only a confident YES grants permission."""
    model = os.environ.get("HOOK_ROUTER_MODEL", "").strip()
    if not model or not shutil.which("claude"):
        return False
    prompt = (
        "You are a permission gate for a shell command. Reply with exactly one word: "
        "SAFE if the command is read-only with no destructive, network, or credential "
        "side effects, otherwise UNSAFE.\n\nCommand:\n" + cmd
    )
    try:
        r = subprocess.run(
            ["claude", "-p", "--model", model, prompt],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and r.stdout.strip().upper().startswith("SAFE")


def main() -> int:
    cmd = tool_input(read_event()).get("command", "")
    if not isinstance(cmd, str) or not cmd:
        return allow()
    if is_deterministically_safe(cmd):
        return auto_approve("read-only command auto-approved by 100xprism permission router")
    if model_says_safe(cmd):
        return auto_approve(f"classified safe by {os.environ['HOOK_ROUTER_MODEL']} via permission router")
    return allow()  # defer to the normal prompt — never block


if __name__ == "__main__":
    sys.exit(main())
