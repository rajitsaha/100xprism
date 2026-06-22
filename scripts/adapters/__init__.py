"""Cost adapters: per-tool sources of (dir, day, token) usage.

Only `claude_code` produces real numbers today. `codex` is a documented stub for
when the Codex CLI is used locally. Cursor / Antigravity expose no local token data,
so they have no adapter — their directories still appear via the value layer.
"""
from collections import namedtuple

Usage = namedtuple("Usage", "dir day input output cache_read cache_write tool")

from . import claude_code, codex  # noqa: E402

ADAPTERS = [claude_code, codex]
