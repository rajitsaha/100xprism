"""
Pi cost adapter — parses ~/.pi/agent/sessions/**/*.jsonl (best-effort).

Pi session JSONL shape varies by version. This adapter extracts usage when
present; otherwise yields empty token buckets (never invents prices). Missing
directory → scan() returns [].
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _value  # noqa: E402

from . import Usage, _incremental  # noqa: E402

TOOL = "pi"
HOME = os.path.expanduser("~")
SOURCE_DIR = os.path.join(HOME, ".pi", "agent", "sessions")
CACHE_FILE = os.path.join(HOME, ".claude", ".token-dashboard-pi-cache.json")
PARSER_VERSION = 1
CACHE_VERSION = 1


def _empty():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def _add(dst, i, o, cr, cw):
    dst["input"] += i
    dst["output"] += o
    dst["cache_read"] += cr
    dst["cache_write"] += cw


def _usage_from_obj(o: dict):
    for key in ("usage", "token_usage", "tokens"):
        u = o.get(key)
        if isinstance(u, dict):
            inp = int(u.get("input") or u.get("input_tokens") or u.get("prompt_tokens") or 0)
            out = int(u.get("output") or u.get("output_tokens") or u.get("completion_tokens") or 0)
            cr = int(
                u.get("cache_read")
                or u.get("cacheRead")
                or u.get("cache_read_input_tokens")
                or u.get("cached_tokens")
                or 0
            )
            cw = int(
                u.get("cache_write")
                or u.get("cacheWrite")
                or u.get("cache_creation_input_tokens")
                or 0
            )
            if inp or out or cr or cw:
                return inp, out, cr, cw
    inp = int(o.get("input_tokens") or o.get("prompt_tokens") or 0)
    out = int(o.get("output_tokens") or o.get("completion_tokens") or 0)
    cr = int(o.get("cache_read_input_tokens") or o.get("cached_tokens") or 0)
    cw = int(o.get("cache_creation_input_tokens") or 0)
    if inp or out or cr or cw:
        return inp, out, cr, cw
    return None


def _model_from_obj(o: dict) -> str:
    for key in ("model", "modelId", "model_id"):
        v = o.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    m = o.get("message")
    if isinstance(m, dict):
        v = m.get("model")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "unknown"


def _has_context_input(o):
    keys = {"input", "input_tokens", "prompt_tokens", "cache_read", "cacheRead",
            "cache_read_input_tokens", "cached_tokens", "cache_write", "cacheWrite",
            "cache_creation_input_tokens"}
    if any(key in o for key in keys):
        return True
    return any(isinstance(o.get(key), dict) and any(k in o[key] for k in keys)
               for key in ("usage", "token_usage", "tokens"))


def _day_from_obj(o: dict, fallback: str) -> str:
    ts = o.get("timestamp") or o.get("time") or o.get("ts")
    if isinstance(ts, (int, float)):
        try:
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except (OSError, ValueError, OverflowError):
            return fallback
    if isinstance(ts, str) and len(ts) >= 10 and ts[4] == "-":
        return ts[:10]
    return fallback


def _parse_records(records, state=None, summary=None):
    totals = _empty()
    by_day = defaultdict(_empty)
    by_model = defaultdict(_empty)
    by_day_model = defaultdict(lambda: defaultdict(_empty))
    main_subagent_by_day = defaultdict(lambda: {"main": _empty(), "subagent": _empty()})
    session_id = None
    cwd = None
    msgs = 0
    fallback_day = state.get("fallback_day", "unknown") if state else "unknown"

    summary = summary or {}
    state = state or {}
    totals = _incremental.restore(totals, summary.get("totals", {}))
    by_day = _incremental.restore(by_day, summary.get("by_day", {}))
    by_model = _incremental.restore(by_model, summary.get("by_model", {}))
    by_day_model = _incremental.restore(by_day_model, summary.get("by_day_model", {}))
    main_subagent_by_day = _incremental.restore(main_subagent_by_day, summary.get("main_subagent_by_day", {}))
    session_id = summary.get("session_id", None)
    cwd = summary.get("cwd", None)
    msgs = summary.get("msgs", 0)
    observed = _incremental.observations(summary)

    for o in records:
        _incremental.observe_activity(observed, o)
        session_id = session_id or o.get("sessionId") or o.get("session_id") or o.get("id")
        meta = o.get("meta") if isinstance(o.get("meta"), dict) else {}
        cwd = cwd or o.get("cwd") or meta.get("cwd")
        day = _day_from_obj(o, fallback_day)

        usage_object = o
        usage = _usage_from_obj(usage_object)
        if usage is None and isinstance(o.get("message"), dict):
            usage_object = o["message"]
            usage = _usage_from_obj(usage_object)
        if usage is None:
            continue
        i, out, cr, cw = usage
        if _has_context_input(usage_object):
            _incremental.observe_context(observed, i + cr + cw)
        model = _model_from_obj(o)
        msgs += 1
        _add(totals, i, out, cr, cw)
        _add(by_day[day], i, out, cr, cw)
        _add(by_model[model], i, out, cr, cw)
        _add(by_day_model[day][model], i, out, cr, cw)
        _add(main_subagent_by_day[day]["main"], i, out, cr, cw)

    aggregate = {
        "totals": totals,
        "by_day": dict(by_day),
        "by_model": dict(by_model),
        "by_day_model": {d: dict(m) for d, m in by_day_model.items()},
        "main_subagent_by_day": {
            d: {"main": v["main"], "subagent": v["subagent"]}
            for d, v in main_subagent_by_day.items()
        },
        "session_id": session_id,
        "cwd": cwd,
        "msgs": msgs,
        "comp": {},
        "by_skill": {},
        "skill_invocations": {},
        "tool": TOOL,
    }
    aggregate.update(observed)
    return aggregate, {"fallback_day": fallback_day}


def parse_file(path):
    """Parse a complete transcript with the legacy tolerant foreground behavior."""
    day = datetime.fromtimestamp(os.stat(path).st_mtime).strftime("%Y-%m-%d")
    return _parse_records(_incremental.legacy_records(path), state={"fallback_day": day})[0]


def parse_incremental(path, offset=0, state=None, summary=None, observer=None):
    """Parse only complete records after a durable binary checkpoint."""
    if state is None:
        state = {"fallback_day": datetime.fromtimestamp(os.stat(path).st_mtime).strftime("%Y-%m-%d")}
    return _incremental.parse(path, _parse_records, offset, state, summary, observer=observer)


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            c = json.load(f)
        if c.get("version") == CACHE_VERSION:
            return c.get("files", {})
    except Exception:
        pass
    return {}


def save_cache(files):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump({"version": CACHE_VERSION, "files": files}, f)
    except Exception as e:
        print(f"warning: could not write pi cache: {e}", file=sys.stderr)


def scan(verbose=False, progress=None):
    if not os.path.isdir(SOURCE_DIR):
        return []
    cache = load_cache()
    paths = glob.glob(os.path.join(SOURCE_DIR, "**", "*.jsonl"), recursive=True)
    new_cache = {}
    reparsed = 0
    processed = 0
    if progress:
        progress(0, len(paths), 0, 0)
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        prev = cache.get(p)
        if prev and prev.get("mtime") == st.st_mtime and prev.get("size") == st.st_size:
            new_cache[p] = prev
            processed += 1
            if progress and (processed == len(paths) or processed % 25 == 0):
                progress(processed, len(paths), reparsed, processed - reparsed)
            continue
        summary = parse_file(p)
        summary["mtime"] = st.st_mtime
        summary["size"] = st.st_size
        cwd = summary.get("cwd")
        summary["project"] = _value.project_label_for_path(cwd) if cwd else "pi"
        summary["projdir"] = (
            _value.mangle_path(os.path.abspath(os.path.expanduser(cwd))) if cwd else "unknown"
        )
        summary["tool"] = TOOL
        new_cache[p] = summary
        reparsed += 1
        processed += 1
        if progress and (processed == len(paths) or processed % 25 == 0):
            progress(processed, len(paths), reparsed, processed - reparsed)
    save_cache(new_cache)
    if verbose:
        print(
            f"pi: scanned {len(paths)} sessions ({reparsed} re-parsed, "
            f"{len(paths) - reparsed} cached)",
            file=sys.stderr,
        )
    if progress and processed != len(paths):
        progress(len(paths), len(paths), reparsed, max(0, len(paths) - reparsed))
    return list(new_cache.values())


def iter_dir_days(file_summaries):
    for s in file_summaries:
        projdir = s.get("projdir") or ""
        for day, d in (s.get("by_day") or {}).items():
            yield Usage(
                projdir, day,
                d.get("input", 0), d.get("output", 0),
                d.get("cache_read", 0), d.get("cache_write", 0),
                TOOL,
            )
