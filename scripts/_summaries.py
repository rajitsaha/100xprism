#!/usr/bin/env python3
"""_summaries.py — non-blocking AI one-liners for the value layer.

Shells out to the local `claude` CLI to summarize what each directory's window of
work accomplished, caching the result in the value store. Pure degradation: if the
CLI is missing or errors, summaries stay None and the dashboard shows git/fs facts.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _value  # noqa: E402


def summarize_text(subjects, files_count):
    body = "\n".join(f"- {s}" for s in subjects) or f"{files_count} files changed"
    return ("In ONE sentence (max 12 words, no period), say what this work "
            "accomplished. Commit subjects:\n" + body)


def run_claude(prompt, cli="claude", timeout=60):
    try:
        r = subprocess.run([cli, "-p", prompt], capture_output=True, text=True,
                           timeout=timeout)
        out = (r.stdout or "").strip()
        return (out or None) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def backfill(limit=20, runner=run_claude):
    store = _value.load_store()
    written = 0
    for real_dir, e in store.get("dirs", {}).items():
        if written >= limit:
            break
        v = e.get("value", {})
        if v.get("summary") is not None:
            continue
        if not v.get("subjects") and not v.get("fs_files"):
            continue
        text = runner(summarize_text(v.get("subjects", []), v.get("fs_files", 0)))
        if text:
            v["summary"] = text
            written += 1
    if written:
        _value.save_store(store)
    return written
