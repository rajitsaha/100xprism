"""Codex cost adapter — STUB. Codex CLI writes ~/.codex/sessions/*.jsonl rollouts
(newer versions include token-count events). Not wired into v1; returns nothing
unless those sessions exist, and even then yields [] until the parser is written."""
import os
from . import Usage  # noqa: F401

TOOL = "codex"
SESSIONS = os.path.join(os.path.expanduser("~"), ".codex", "sessions")


def iter_usage():
    if not os.path.isdir(SESSIONS):
        return
    return  # TODO(codex): parse token-count events when Codex is used locally
    yield  # pragma: no cover  — makes this a generator
