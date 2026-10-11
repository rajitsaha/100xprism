"""
Codex cost adapter — parses ~/.codex/sessions/**/rollout-*.jsonl.

Verified against a real Codex CLI (v0.142.5) rollout on 2026-07-09: each file is
one session, containing `session_meta` (session_id, cwd), `turn_context`
(per-turn `model`), and `event_msg`/`token_count` events whose
`info.total_token_usage` is a CUMULATIVE running total for the session (non-
decreasing across consecutive events in the sample file — but that was a
single 6-event sample, not a documented API guarantee). Per-event token counts
are therefore deltas between consecutive readings, not the readings
themselves — EXCEPT when a reading's counter is lower than the previous one
(e.g. a context-compaction event resetting `total_token_usage` mid-session),
in which case that reading is treated as a fresh baseline and its own
cumulative values are credited directly, rather than computing a negative
delta against the stale pre-reset baseline (which would silently discard real
usage via clamping).

Codex/OpenAI's cache accounting folds cached tokens INTO `input_tokens` (unlike
Claude Code's separate cache_read, which is additional to input) — so `input`
here is computed as `input_tokens - cached_input_tokens` to line up with our
(input, cache_read) split. Codex exposes no cache-write count; cache_write is
always 0 for this adapter — a documented limitation, not a bug.
"""
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _value  # noqa: E402

from . import Usage, _incremental  # noqa: E402

TOOL = "codex"
HOME = os.path.expanduser("~")
SOURCE_DIR = os.path.join(HOME, ".codex", "sessions")
CACHE_FILE = os.path.join(HOME, ".claude", ".token-dashboard-codex-cache.json")
PARSER_VERSION = 1
CACHE_VERSION = 1


def _empty():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def _add(dst, i, o, cr, cw):
    dst["input"] += i
    dst["output"] += o
    dst["cache_read"] += cr
    dst["cache_write"] += cw


def _lines(path):
    with open(path, errors="ignore") as fh:
        yield from fh


def _parse_records(records, state=None, summary=None):
    """Aggregate one Codex rollout into the same summary shape claude_code.parse_file
    produces (minus fields Codex has no data for: composition, first_fixed,
    subagent/skill attribution)."""
    totals = _empty()
    by_day = defaultdict(_empty)
    by_model = defaultdict(_empty)
    by_day_model = defaultdict(lambda: defaultdict(_empty))
    main_subagent_by_day = defaultdict(lambda: {"main": _empty(), "subagent": _empty()})
    session_id = None
    cwd = None
    current_model = "unknown"
    prev_cum = None
    msgs = 0

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
    current_model = state.get("current_model", "unknown")
    prev_cum = state.get("prev_cum", None)
    observed = _incremental.observations(summary)

    for o in records:
        _incremental.observe_activity(observed, o)
        t = o.get("type")
        if t == "session_meta":
            payload = o.get("payload") or {}
            session_id = payload.get("session_id") or payload.get("id")
            cwd = payload.get("cwd")
            continue
        if t == "turn_context":
            model = (o.get("payload") or {}).get("model")
            if model:
                current_model = model
            continue
        if t != "event_msg":
            continue
        payload = o.get("payload") or {}
        if payload.get("type") != "token_count":
            continue
        last = (payload.get("info") or {}).get("last_token_usage")
        if isinstance(last, dict):
            _incremental.observe_context(observed, last.get("input_tokens"))
        cum = (payload.get("info") or {}).get("total_token_usage")
        if not isinstance(cum, dict):
            continue
        is_reset = prev_cum is not None and (
            cum.get("input_tokens", 0) < prev_cum.get("input_tokens", 0)
            or cum.get("cached_input_tokens", 0) < prev_cum.get("cached_input_tokens", 0)
            or cum.get("output_tokens", 0) < prev_cum.get("output_tokens", 0)
        )
        if prev_cum is None or is_reset:
            # No prior baseline, or the cumulative counter went DOWN (e.g. a
            # context-compaction event reset total_token_usage mid-session).
            # A negative delta against a stale pre-reset baseline is
            # nonsensical and would be silently clamped to 0, discarding real
            # usage — so treat this reading as a fresh baseline instead and
            # credit its own cumulative values directly.
            d_in_total = cum.get("input_tokens", 0)
            d_cr = cum.get("cached_input_tokens", 0)
            d_out = cum.get("output_tokens", 0)
        else:
            d_in_total = cum.get("input_tokens", 0) - prev_cum.get("input_tokens", 0)
            d_cr = cum.get("cached_input_tokens", 0) - prev_cum.get("cached_input_tokens", 0)
            d_out = cum.get("output_tokens", 0) - prev_cum.get("output_tokens", 0)
        prev_cum = cum
        if not is_reset and d_in_total <= 0 and d_out <= 0 and d_cr <= 0:
            continue  # duplicate/unchanged event (e.g. an end-of-session repeat)
        d_cr = max(d_cr, 0)
        d_in = max(d_in_total - d_cr, 0)
        d_out = max(d_out, 0)
        msgs += 1
        _add(totals, d_in, d_out, d_cr, 0)
        _add(by_model[current_model], d_in, d_out, d_cr, 0)
        day = (o.get("timestamp") or "")[:10] or "unknown"
        _add(by_day[day], d_in, d_out, d_cr, 0)
        _add(by_day_model[day][current_model], d_in, d_out, d_cr, 0)
        _add(main_subagent_by_day[day]["main"], d_in, d_out, d_cr, 0)

    aggregate = {
        "totals": totals,
        "by_day": dict(by_day),
        "by_model": dict(by_model),
        "by_day_model": {d: dict(models) for d, models in by_day_model.items()},
        "main_subagent_by_day": {day: dict(buckets) for day, buckets in main_subagent_by_day.items()},
        "comp": {},
        "msgs": msgs,
        "turns": msgs,
        "first_fixed": 0,
        "session_id": session_id,
        "main_tokens": dict(totals),
        "subagent_tokens": _empty(),
        "by_skill": {},
        "skill_invocations": {},
        "skill_exact": [],
        "cwd": cwd,
    }
    aggregate.update(observed)
    return aggregate, {"current_model": current_model, "prev_cum": prev_cum}


def parse_file(path):
    """Parse a complete transcript with the legacy tolerant foreground behavior."""
    return _parse_records(_incremental.legacy_records(path), state=None)[0]


def parse_incremental(path, offset=0, state=None, summary=None, observer=None):
    """Parse only complete records after a durable binary checkpoint."""
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
        with open(CACHE_FILE, "w") as f:
            json.dump({"version": CACHE_VERSION, "files": files}, f)
    except Exception as e:
        print(f"warning: could not write codex cache: {e}", file=sys.stderr)


def _project_label(cwd):
    return _value.project_label_for_path(cwd) if cwd else "unknown"


def scan(verbose=False, progress=None):
    """Glob + incrementally parse every Codex rollout. Returns [] if Codex has
    never been used locally (no ~/.codex/sessions dir)."""
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
        summary["project"] = _project_label(cwd)
        summary["projdir"] = (_value.mangle_path(os.path.abspath(os.path.expanduser(cwd)))
                               if cwd else "unknown")
        summary["tool"] = TOOL
        new_cache[p] = summary
        reparsed += 1
        processed += 1
        if progress and (processed == len(paths) or processed % 25 == 0):
            progress(processed, len(paths), reparsed, processed - reparsed)
    save_cache(new_cache)
    if verbose:
        print(f"codex: scanned {len(paths)} rollouts ({reparsed} re-parsed, "
              f"{len(paths) - reparsed} cached)", file=sys.stderr)
    if progress and processed != len(paths):
        progress(len(paths), len(paths), reparsed, max(0, len(paths) - reparsed))
    return list(new_cache.values())


def iter_dir_days(file_summaries):
    for s in file_summaries:
        projdir = s.get("projdir") or ""
        for day, d in (s.get("by_day") or {}).items():
            yield Usage(projdir, day, d.get("input", 0), d.get("output", 0),
                        d.get("cache_read", 0), d.get("cache_write", 0), TOOL)
