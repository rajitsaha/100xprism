"""Cursor activity adapter.

Cursor writes agent transcript JSONL under
~/.cursor/projects/*/agent-transcripts, both directly and in per-session
subdirectories. The observed JSONL format contains message roles/content but no
provider token counters or model id. This adapter therefore emits activity-only
summaries: project/session/message/date coverage is exact, while all token
buckets remain zero and must never be priced. Legacy transcript .txt files,
~/.cursor/chats, and state.vscdb are intentionally outside this adapter's scope.
"""
import glob
import json
import os
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _value

TOOL = "cursor"
HOME = os.path.expanduser("~")
SOURCE_DIR = os.path.join(HOME, ".cursor", "projects")
_MEM_CACHE = {}


def _empty():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def _text_size(value):
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_text_size(v) for v in value)
    if isinstance(value, dict):
        return sum(_text_size(v) for v in value.values())
    return 0


def _project_dir(path):
    parts = path.split(os.sep + "projects" + os.sep, 1)
    if len(parts) < 2 or not parts[1]:
        return None
    return parts[1].split(os.sep, 1)[0]


def _project(path, dir_index=None):
    project_dir = _project_dir(path)
    if not project_dir:
        return "unknown", None, "Cursor · unknown"
    mangled = "-" + project_dir.strip("-")
    real = (dir_index or {}).get(mangled) or _value.resolve_real_dir(mangled)
    label = (_value.project_label_for_path(real) if real
             else f"Cursor · {project_dir}")
    return project_dir, real, label


def _transcript_paths():
    """Return flat and nested JSONL transcripts, excluding other Cursor stores."""
    pattern = os.path.join(SOURCE_DIR, "*", "agent-transcripts", "**", "*.jsonl")
    return sorted(glob.glob(pattern, recursive=True))


def parse_file(path, dir_index=None):
    """Parse one transcript without retaining message content."""
    roles = Counter()
    chars = 0
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(row, dict):
                continue
            roles[str(row.get("role") or "unknown")] += 1
            message = row.get("message")
            chars += _text_size(message.get("content") if isinstance(message, dict) else message)
    st = os.stat(path)
    day = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
    project_dir, cwd, label = _project(path, dir_index)
    session_id = os.path.basename(path).rsplit(".", 1)[0]
    return {
        "totals": _empty(), "by_day": {}, "by_model": {}, "by_day_model": {},
        "comp": {}, "msgs": 0, "turns": 0, "first_fixed": 0,
        "session_id": session_id, "main_tokens": _empty(),
        "subagent_tokens": _empty(), "by_skill": {}, "skill_invocations": {},
        "skill_exact": [], "mtime": st.st_mtime, "size": st.st_size,
        "project": label, "projdir": project_dir, "cwd": cwd, "tool": TOOL,
        "activity_only": True,
        "activity": {"day": day, "messages": sum(roles.values()),
                     "user_messages": roles.get("user", 0),
                     "assistant_messages": roles.get("assistant", 0),
                     "artifacts": 0, "text_chars": chars,
                     "source": "agent-transcript-jsonl"},
    }


def scan(verbose=False, dir_index=None, progress=None):
    if not os.path.isdir(SOURCE_DIR):
        return []
    paths = _transcript_paths()
    if progress:
        progress(0, len(paths), 0, 0)
    rows = []
    live = set()
    for path in paths:
        try:
            st = os.stat(path)
            project_dir = _project_dir(path)
            indexed_real = ((dir_index or {}).get("-" + project_dir.strip("-"))
                            if project_dir else None)
            key = (path, st.st_mtime, st.st_size, indexed_real)
            live.add(key)
            row = _MEM_CACHE.get(key)
            if row is None:
                row = parse_file(path, dir_index)
                _MEM_CACHE[key] = row
            rows.append(row)
        except OSError:
            continue
    for key in list(_MEM_CACHE):
        if key[0].startswith(SOURCE_DIR + os.sep) and key not in live:
            del _MEM_CACHE[key]
    if verbose:
        print(f"cursor: scanned {len(rows)} activity transcripts (token counters unavailable)",
              file=sys.stderr)
    if progress:
        progress(len(paths), len(paths), len(rows), 0)
    return rows


def iter_dir_days(file_summaries):
    """Cursor exposes no exact tokens, so cost iteration is intentionally empty."""
    return iter(())
