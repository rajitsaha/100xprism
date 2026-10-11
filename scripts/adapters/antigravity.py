"""Google Antigravity activity adapter.

Antigravity persists protobuf conversations plus timestamped task artifacts.
The protobuf schema and provider token counters are not exposed locally, so this
collector records exact session/project/date/artifact coverage only. Token
buckets remain zero and are never priced.
"""
import glob
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _value

TOOL = "antigravity"
HOME = os.path.expanduser("~")
SOURCE_DIR = os.path.join(HOME, ".gemini", "antigravity")
WORKSPACE_DIR = os.path.join(
    HOME, "Library", "Application Support", "Antigravity", "User", "workspaceStorage")
_UUID_RE = re.compile(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_MAP_CACHE_KEY = None
_MAP_CACHE_VALUE = None


def _empty():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def _workspace_map(session_ids):
    """Map conversation ids to workspaces without retaining database content."""
    global _MAP_CACHE_KEY, _MAP_CACHE_VALUE
    wanted = {s.encode() for s in session_ids}
    workspace_files = glob.glob(os.path.join(WORKSPACE_DIR, "*", "workspace.json"))
    signature = []
    for path in workspace_files:
        db = os.path.join(os.path.dirname(path), "state.vscdb")
        try:
            st, dbst = os.stat(path), os.stat(db)
            signature.append((path, st.st_mtime, st.st_size, dbst.st_mtime, dbst.st_size))
        except OSError:
            continue
    cache_key = (tuple(sorted(session_ids)), tuple(sorted(signature)))
    if cache_key == _MAP_CACHE_KEY:
        return dict(_MAP_CACHE_VALUE)
    out = {}
    for ws_json in workspace_files:
        try:
            with open(ws_json, encoding="utf-8") as f:
                raw = json.load(f)
            folder = raw.get("folder", "")
            parsed = urlparse(folder)
            cwd = unquote(parsed.path) if parsed.scheme == "file" else None
            db = os.path.join(os.path.dirname(ws_json), "state.vscdb")
            if not cwd or not os.path.isfile(db):
                continue
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                for key, value in con.execute("SELECT key, value FROM ItemTable"):
                    blob = (key or "").encode() + b" " + (value if isinstance(value, bytes)
                                                           else str(value or "").encode())
                    for match in set(_UUID_RE.findall(blob)) & wanted:
                        out.setdefault(match.decode(), cwd)
            finally:
                con.close()
        except (OSError, json.JSONDecodeError, sqlite3.OperationalError) as exc:
            print(f"antigravity: skipped unreadable workspace {ws_json}: {exc}",
                  file=sys.stderr)
            continue
    _MAP_CACHE_KEY, _MAP_CACHE_VALUE = cache_key, dict(out)
    return out


def _artifact_info(session_id):
    paths = glob.glob(os.path.join(SOURCE_DIR, "brain", session_id, "*.metadata.json"))
    dates = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            value = raw.get("updatedAt") if isinstance(raw, dict) else None
            if value:
                dates.append(value[:10])
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            print(f"antigravity: skipped unreadable artifact {path}: {exc}",
                  file=sys.stderr)
            continue
    return len(paths), max(dates) if dates else None


def scan(verbose=False, progress=None):
    if not os.path.isdir(SOURCE_DIR):
        return []
    conversation_paths = glob.glob(os.path.join(SOURCE_DIR, "conversations", "*.pb"))
    brain_dirs = glob.glob(os.path.join(SOURCE_DIR, "brain", "*"))
    ids = {os.path.basename(p).rsplit(".", 1)[0] for p in conversation_paths}
    ids.update(os.path.basename(p) for p in brain_dirs if os.path.isdir(p))
    cwd_by_id = _workspace_map(ids)
    conversation_by_id = {os.path.basename(p).rsplit(".", 1)[0]: p
                          for p in conversation_paths}
    rows = []
    if progress:
        progress(0, len(ids), 0, 0)
    for session_id in sorted(ids):
        artifacts, artifact_day = _artifact_info(session_id)
        conv = conversation_by_id.get(session_id)
        try:
            st = os.stat(conv) if conv else os.stat(os.path.join(SOURCE_DIR, "brain", session_id))
        except OSError:
            continue
        day = artifact_day or datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
        cwd = cwd_by_id.get(session_id)
        label = _value.project_label_for_path(cwd) if cwd else "Antigravity · unmapped"
        rows.append({
            "totals": _empty(), "by_day": {}, "by_model": {}, "by_day_model": {},
            "comp": {}, "msgs": 0, "turns": 0, "first_fixed": 0,
            "session_id": session_id, "main_tokens": _empty(),
            "subagent_tokens": _empty(), "by_skill": {}, "skill_invocations": {},
            "skill_exact": [], "mtime": st.st_mtime, "size": st.st_size,
            "project": label, "projdir": _value.mangle_path(cwd) if cwd else session_id,
            "cwd": cwd, "tool": TOOL, "activity_only": True,
            "activity": {"day": day, "messages": None, "user_messages": None,
                         "assistant_messages": None, "artifacts": artifacts,
                         "text_chars": None, "source": "protobuf+artifact-metadata"},
        })
    if verbose:
        print(f"antigravity: scanned {len(rows)} activity sessions "
              f"(token counters unavailable)", file=sys.stderr)
    if progress:
        progress(len(ids), len(ids), len(rows), 0)
    return rows


def iter_dir_days(file_summaries):
    """Antigravity exposes no exact tokens, so cost iteration is intentionally empty."""
    return iter(())
