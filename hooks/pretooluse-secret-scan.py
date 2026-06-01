#!/usr/bin/env python3
"""PreToolUse(Write|Edit) — block writes that contain obvious hard-coded credentials.

A fast, dependency-free regex/heuristic scan. It is intentionally tuned for *obvious*
secrets (provider key formats, private-key blocks, high-entropy assignments) and skips
clear placeholders so it doesn't cry wolf on `.env.example` style files. It is a
backstop, not a replacement for the /security gate.

Exit 0 = allow.  Exit 2 = block, listing what tripped.
Set HOOK_SECRET_SCAN=off to disable at runtime without uninstalling.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from hooklib import allow, block, read_event, tool_input  # noqa: E402

# (label, compiled pattern). Each pattern targets a credential format precise enough
# that a match is almost certainly a real secret, not prose.
_PROVIDER_PATTERNS = [
    ("AWS access key id",       re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("AWS secret access key",   re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+]{40}\b")),
    ("private key block",       re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("OpenAI API key",          re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token",            re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b")),
    ("GitHub fine-grained PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{59,}\b")),
    ("Slack token",             re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key",          re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe live secret key",  re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}\b")),
    ("Slack webhook",           re.compile(r"https://hooks\.slack\.com/services/T[A-Za-z0-9]+/B[A-Za-z0-9]+/[A-Za-z0-9]+")),
]

# Generic `name = "value"` secret assignment with a high-entropy literal.
_GENERIC_ASSIGN = re.compile(
    r"(?i)\b(api[_-]?key|secret(?:[_-]?key)?|access[_-]?token|auth[_-]?token|password|passwd|client[_-]?secret)\b"
    r"\s*[=:]\s*['\"]([^'\"]{16,})['\"]"
)

# Values that look like documentation, env-var indirection, or fillers — never secrets.
_PLACEHOLDER = re.compile(
    r"(?i)(example|changeme|placeholder|your[_-]?|xxxx|<[^>]+>|\$\{?[A-Z_]+\}?|"
    r"process\.env|os\.environ|getenv|secret:|env:|\bredacted\b|\*{4,}|^.{0,3}$)"
)


def _looks_placeholder(value: str) -> bool:
    if _PLACEHOLDER.search(value):
        return True
    # All one repeated character (e.g. "aaaaaaaaaaaaaaaa").
    return len(set(value)) <= 2


def scan(text: str) -> list[str]:
    findings: list[str] = []
    for label, pat in _PROVIDER_PATTERNS:
        if pat.search(text):
            findings.append(label)
    for m in _GENERIC_ASSIGN.finditer(text):
        if not _looks_placeholder(m.group(2)):
            findings.append(f"hard-coded {m.group(1).lower()}")
    # De-dupe while preserving order.
    seen: set[str] = set()
    return [f for f in findings if not (f in seen or seen.add(f))]


def main() -> int:
    if os.environ.get("HOOK_SECRET_SCAN", "").lower() == "off":
        return allow()

    event = read_event()
    ti = tool_input(event)
    # Write → content; Edit → new_string; MultiEdit → edits[].new_string.
    chunks = [ti.get("content", ""), ti.get("new_string", "")]
    for e in ti.get("edits", []) if isinstance(ti.get("edits"), list) else []:
        if isinstance(e, dict):
            chunks.append(e.get("new_string", ""))
    text = "\n".join(c for c in chunks if isinstance(c, str))
    if not text:
        return allow()

    findings = scan(text)
    if not findings:
        return allow()

    path = ti.get("file_path", "(file)")
    bullets = "\n".join(f"   • {f}" for f in findings)
    return block(
        f"⛔ 100x-dev secret-scan hook: refusing to write a likely credential into {path}\n"
        f"{bullets}\n"
        "   Move the secret to an env var / secret manager and reference it instead.\n"
        "   False positive? Set HOOK_SECRET_SCAN=off for this session."
    )


if __name__ == "__main__":
    sys.exit(main())
