"""
Claude Code cost adapter — owns glob + incremental cache + parse for
~/.claude/projects/**/*.jsonl, plus session/subagent/skill attribution.

Transcript fields used here were verified against real local transcripts on
2026-07-09: `sessionId` (also duplicated as `session_id` on some lines — the
first non-empty one wins), `isSidechain` (bool — true marks a subagent-branch
message), `attributionSkill` / `attributionPlugin` (set natively by Claude Code
when a Skill tool is active — this is EXACT attribution, not a heuristic). Slash
commands that are not Skills (e.g. built-in `/model`) don't set attributionSkill,
so those are segmented via the `<command-name>/xyz</command-name>` marker in the
user turn as a fallback — attribution for that path is a boundary heuristic: once
a marker is seen, usage on subsequent lines is attributed to it until the next
marker or a real attributionSkill supersedes it (same honesty convention as the
existing character-count composition estimate below — never presented as exact).
"""
import glob
import json
import os
import re
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _value  # noqa: E402 — project_label / mangle_path

from . import Usage, _incremental  # noqa: E402

TOOL = "claude-code"
HOME = os.path.expanduser("~")
SOURCE_DIR = os.path.join(HOME, ".claude", "projects")
CACHE_FILE = os.path.join(HOME, ".claude", ".token-dashboard-cache.json")
PARSER_VERSION = 1
CACHE_VERSION = 5  # bump -> re-parse all transcripts (windowed attribution added)
FULL_SCAN_SECONDS = 1800
HOT_FILE_SECONDS = 86400
_MEM_PAYLOAD = None
_MEM_CACHE_KEY = None

project_label = _value.project_label


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


COMP_CATS = ["prompts", "model_output", "code_authored", "tool_calls",
             "files_read", "logs", "other_results"]
COMP_LABELS = {
    "prompts": "your prompts", "model_output": "model output (prose)",
    "code_authored": "code written (edits)", "tool_calls": "tool calls",
    "files_read": "code / files read", "logs": "command output / logs",
    "other_results": "other tool results",
}
_READ_TOOLS = {"Read", "Glob", "Grep", "LS", "NotebookRead"}
_SHELL_TOOLS = {"Bash", "BashOutput"}
_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
_COMMAND_RE = re.compile(r"<command-name>(/\S+)</command-name>")


def _classify(role, content, comp, tool_names):
    """Tally character counts per content-type category for one message."""
    if isinstance(content, str):
        comp["model_output" if role == "assistant" else "prompts"] += len(content)
        return
    if not isinstance(content, list):
        return
    for b in content:
        if isinstance(b, str):
            comp["model_output" if role == "assistant" else "prompts"] += len(b)
            continue
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "text":
            comp["model_output" if role == "assistant" else "prompts"] += len(b.get("text") or "")
        elif bt == "tool_use":
            name = b.get("name", "")
            tool_names[b.get("id", "")] = name
            sz = len(json.dumps(b.get("input", {}), ensure_ascii=False))
            comp["code_authored" if name in _EDIT_TOOLS else "tool_calls"] += sz
        elif bt == "tool_result":
            name = tool_names.pop(b.get("tool_use_id", ""), "")
            c = b.get("content", "")
            if isinstance(c, list):
                sz = sum(len(x.get("text") or "") for x in c if isinstance(x, dict))
            elif isinstance(c, str):
                sz = len(c)
            else:
                sz = len(json.dumps(c, ensure_ascii=False))
            if name in _READ_TOOLS:
                comp["files_read"] += sz
            elif name in _SHELL_TOOLS:
                comp["logs"] += sz
            else:
                comp["other_results"] += sz


def _extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _parse_records(records, state=None, summary=None):
    """Aggregate one Claude Code transcript into totals, breakdowns, and
    session/subagent/skill attribution. Returns a per-file summary dict."""
    totals = _empty()
    by_day = defaultdict(_empty)
    by_model = defaultdict(_empty)
    by_day_model = defaultdict(lambda: defaultdict(_empty))
    by_skill_model = defaultdict(lambda: defaultdict(_empty))
    by_skill_day_model = defaultdict(lambda: defaultdict(lambda: defaultdict(_empty)))
    skill_invocations_by_day = defaultdict(lambda: defaultdict(int))
    comp_by_day = defaultdict(lambda: defaultdict(int))
    main_subagent_by_day = defaultdict(lambda: {"main": _empty(), "subagent": _empty()})
    comp = defaultdict(int)
    tool_names = {}
    msgs = 0
    first_fixed = None
    turns = 0
    session_id = None
    main_tokens = _empty()
    subagent_tokens = _empty()
    by_skill = defaultdict(_empty)
    skill_invocations = defaultdict(int)
    skill_exact = set()
    first_fixed_day = None
    # Boundary-heuristic state: the last `<command-name>` marker seen carries
    # forward across lines (no explicit "end" marker exists), until a new
    # marker or a real attributionSkill supersedes it. Real attributionSkill
    # is NOT carried forward — it's trusted only on the line it actually
    # appears on, since Claude Code sets it natively per-message while active.
    current_marker_segment = None
    prev_attr_skill = None

    summary = summary or {}
    state = state or {}
    totals = _incremental.restore(totals, summary.get("totals", {}))
    by_day = _incremental.restore(by_day, summary.get("by_day", {}))
    by_model = _incremental.restore(by_model, summary.get("by_model", {}))
    by_day_model = _incremental.restore(by_day_model, summary.get("by_day_model", {}))
    by_skill_model = _incremental.restore(by_skill_model, summary.get("by_skill_model", {}))
    by_skill_day_model = _incremental.restore(by_skill_day_model, summary.get("by_skill_day_model", {}))
    skill_invocations_by_day = _incremental.restore(skill_invocations_by_day, summary.get("skill_invocations_by_day", {}))
    comp_by_day = _incremental.restore(comp_by_day, summary.get("comp_by_day", {}))
    main_subagent_by_day = _incremental.restore(main_subagent_by_day, summary.get("main_subagent_by_day", {}))
    comp = _incremental.restore(comp, summary.get("comp", {}))
    main_tokens = _incremental.restore(main_tokens, summary.get("main_tokens", {}))
    subagent_tokens = _incremental.restore(subagent_tokens, summary.get("subagent_tokens", {}))
    by_skill = _incremental.restore(by_skill, summary.get("by_skill", {}))
    skill_invocations = _incremental.restore(skill_invocations, summary.get("skill_invocations", {}))
    msgs = summary.get("msgs", 0)
    first_fixed = summary.get("first_fixed") or None
    turns = summary.get("turns", 0)
    session_id = summary.get("session_id", None)
    cwd = summary.get("cwd")
    first_fixed_day = summary.get("first_fixed_day", None)
    tool_names = state.get("tool_names", {})
    current_marker_segment = state.get("current_marker_segment", None)
    prev_attr_skill = state.get("prev_attr_skill", None)
    skill_exact = set(summary.get("skill_exact", []))
    agent_id = summary.get("agent_id") or state.get("agent_id")
    observed = _incremental.observations(summary)

    for o in records:
        _incremental.observe_activity(observed, o)
        if isinstance(o.get("cwd"), str):
            cwd = o["cwd"]
        agent_id = agent_id or o.get("agentId") or o.get("agent_id")
        if session_id is None:
            session_id = o.get("sessionId") or o.get("session_id")
        day = (o.get("timestamp") or "")[:10] or "unknown"
        m = o.get("message")
        if isinstance(m, dict):
            role = m.get("role") or o.get("type") or ""
            message_comp = defaultdict(int)
            _classify(role, m.get("content"), message_comp, tool_names)
            for category, chars in message_comp.items():
                comp[category] += chars
                comp_by_day[day][category] += chars
            text = _extract_text(m.get("content"))
            cmd = _COMMAND_RE.search(text) if text else None
            current_skill_marker = cmd.group(1) if cmd else None
        else:
            current_skill_marker = None

        if current_skill_marker:
            current_marker_segment = current_skill_marker

        exact_skill = o.get("attributionSkill")
        attr_skill = exact_skill or current_marker_segment
        new_skill_invocation = bool(attr_skill and attr_skill != prev_attr_skill)
        if attr_skill:
            if new_skill_invocation:
                skill_invocations[attr_skill] += 1
                skill_invocations_by_day[day][attr_skill] += 1
            if exact_skill:
                skill_exact.add(attr_skill)
        prev_attr_skill = attr_skill

        u = m.get("usage") if isinstance(m, dict) else None
        if not isinstance(u, dict):
            u = o.get("usage") if isinstance(o.get("usage"), dict) else None
        if not isinstance(u, dict):
            continue
        i = u.get("input_tokens", 0) or 0
        ot = u.get("output_tokens", 0) or 0
        cr = u.get("cache_read_input_tokens", 0) or 0
        cw = u.get("cache_creation_input_tokens", 0) or 0
        if any(key in u for key in ("input_tokens", "cache_read_input_tokens",
                                     "cache_creation_input_tokens")):
            _incremental.observe_context(observed, i + cr + cw)
        if not (i or ot or cr or cw):
            continue
        msgs += 1
        turns += 1
        _add(totals, i, ot, cr, cw)
        model = (m.get("model") if isinstance(m, dict) else None) or "unknown"
        _add(by_model[model], i, ot, cr, cw)
        _add(by_day[day], i, ot, cr, cw)
        _add(by_day_model[day][model], i, ot, cr, cw)
        if first_fixed is None and (i + cr + cw) > 0:
            first_fixed = i + cr + cw
            first_fixed_day = day
        if o.get("isSidechain"):
            _add(subagent_tokens, i, ot, cr, cw)
            _add(main_subagent_by_day[day]["subagent"], i, ot, cr, cw)
        else:
            _add(main_tokens, i, ot, cr, cw)
            _add(main_subagent_by_day[day]["main"], i, ot, cr, cw)
        if attr_skill:
            _add(by_skill[attr_skill], i, ot, cr, cw)
            _add(by_skill_model[attr_skill][model], i, ot, cr, cw)
            _add(by_skill_day_model[day][attr_skill][model], i, ot, cr, cw)

    aggregate = {
        "totals": totals,
        "by_day": dict(by_day),
        "by_model": dict(by_model),
        "by_day_model": {d: dict(models) for d, models in by_day_model.items()},
        "by_skill_model": {skill: dict(models) for skill, models in by_skill_model.items()},
        "by_skill_day_model": {
            day: {skill: dict(models) for skill, models in skills.items()}
            for day, skills in by_skill_day_model.items()
        },
        "skill_invocations_by_day": {day: dict(skills) for day, skills in skill_invocations_by_day.items()},
        "comp": {k: comp.get(k, 0) for k in COMP_CATS},
        "comp_by_day": {day: dict(categories) for day, categories in comp_by_day.items()},
        "msgs": msgs,
        "turns": turns,
        "first_fixed": first_fixed or 0,
        "session_id": session_id,
        "cwd": cwd,
        "main_tokens": main_tokens,
        "subagent_tokens": subagent_tokens,
        "first_fixed_day": first_fixed_day,
        "main_subagent_by_day": {day: dict(buckets) for day, buckets in main_subagent_by_day.items()},
        "by_skill": dict(by_skill),
        "skill_invocations": dict(skill_invocations),
        "skill_exact": sorted(skill_exact),
        "agent_id": agent_id,
    }
    aggregate.update(observed)
    return aggregate, {"tool_names": tool_names,
                       "current_marker_segment": current_marker_segment,
                       "prev_attr_skill": prev_attr_skill, "agent_id": agent_id}


def parse_file(path):
    """Parse a complete transcript with the legacy tolerant foreground behavior."""
    return _parse_records(_incremental.legacy_records(path), state=None)[0]


def parse_incremental(path, offset=0, state=None, summary=None, observer=None):
    """Parse only complete records after a durable binary checkpoint."""
    return _incremental.parse(path, _parse_records, offset, state, summary, observer=observer)


def _load_cache_payload():
    global _MEM_PAYLOAD, _MEM_CACHE_KEY
    try:
        st = os.stat(CACHE_FILE)
        key = (CACHE_FILE, st.st_mtime, st.st_size)
        if _MEM_PAYLOAD is not None and _MEM_CACHE_KEY == key:
            return _MEM_PAYLOAD
    except OSError:
        key = (CACHE_FILE, None, None)
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            c = json.load(f)
        if not isinstance(c, dict):
            raise ValueError("cache root is not an object")
        if c.get("version") == CACHE_VERSION:
            _MEM_PAYLOAD, _MEM_CACHE_KEY = c, key
            return c
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"claude-code: ignoring unreadable cache {CACHE_FILE}: {exc}",
              file=sys.stderr)
    return {"version": CACHE_VERSION, "files": {}, "project_mtimes": {},
            "full_scan_at": 0}


def load_cache():
    return _load_cache_payload().get("files", {})


def save_cache(files, project_mtimes=None, full_scan_at=0):
    global _MEM_PAYLOAD, _MEM_CACHE_KEY
    payload = {"version": CACHE_VERSION, "files": files,
               "project_mtimes": project_mtimes or {},
               "full_scan_at": full_scan_at}
    try:
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, CACHE_FILE)
        st = os.stat(CACHE_FILE)
        _MEM_PAYLOAD = payload
        _MEM_CACHE_KEY = (CACHE_FILE, st.st_mtime, st.st_size)
    except Exception as e:
        print(f"warning: could not write claude_code cache: {e}", file=sys.stderr)


def scan(verbose=False, dir_index=None, progress=None):
    """Glob + incrementally parse every Claude Code transcript. Returns a list of
    per-file summary dicts with mtime/size/project/projdir/tool added."""
    payload = _load_cache_payload()
    cache = payload.get("files", {})
    old_project_mtimes = payload.get("project_mtimes", {})
    last_full_scan = payload.get("full_scan_at", 0) or 0
    now = time.time()
    full_scan = not cache or now - last_full_scan >= FULL_SCAN_SECONDS
    paths = set() if full_scan else set(cache)
    cached_by_project = defaultdict(set)
    for cached_path in cache:
        cached_by_project[os.path.dirname(cached_path)].add(cached_path)
    project_mtimes = {}
    changed_projects = set()
    try:
        projects = [entry for entry in os.scandir(SOURCE_DIR) if entry.is_dir()]
    except OSError:
        projects = []
    for project in projects:
        try:
            mtime = project.stat().st_mtime
        except OSError:
            continue
        project_mtimes[project.path] = mtime
        if full_scan or old_project_mtimes.get(project.path) != mtime:
            changed_projects.add(project.path)
            current = set(glob.glob(os.path.join(project.path, "*.jsonl")))
            paths.difference_update(cached_by_project.get(project.path, ()))
            paths.update(current)
    new_cache = {}
    reparsed = 0
    processed = 0
    labels_by_dir = {}
    cache_changed = set(cache) != paths
    hot_cutoff = now - HOT_FILE_SECONDS
    if progress:
        progress(0, len(paths), 0, 0)
    for p in sorted(paths):
        prev = cache.get(p)
        needs_stat = (full_scan or prev is None or os.path.dirname(p) in changed_projects
                      or prev.get("mtime", 0) >= hot_cutoff)
        if not needs_stat:
            summary = dict(prev)
        else:
            try:
                st = os.stat(p)
            except OSError:
                cache_changed = True
                continue
            if prev and prev.get("mtime") == st.st_mtime and prev.get("size") == st.st_size:
                summary = dict(prev)
            else:
                summary = parse_file(p)
                summary["mtime"] = st.st_mtime
                summary["size"] = st.st_size
                reparsed += 1
                if verbose and reparsed % 200 == 0:
                    print(f"  parsed {reparsed} new/changed claude transcripts...", file=sys.stderr)
        # Labels are derived on every scan so old cached summaries are corrected
        # when a formerly ambiguous transcript directory is resolved.
        projdir = os.path.basename(os.path.dirname(p))
        if projdir not in labels_by_dir:
            labels_by_dir[projdir] = project_label(p, dir_index=dir_index)
        summary["project"] = labels_by_dir[projdir]
        summary["projdir"] = projdir
        summary["tool"] = TOOL
        new_cache[p] = summary
        processed += 1
        if progress and (processed == len(paths) or processed % 25 == 0):
            progress(processed, len(paths), reparsed, processed - reparsed)
    full_scan_at = now if full_scan else last_full_scan
    metadata_changed = project_mtimes != old_project_mtimes or full_scan
    if cache_changed or reparsed or metadata_changed:
        save_cache(new_cache, project_mtimes, full_scan_at)
    if verbose:
        mode = "full" if full_scan else "incremental"
        print(f"claude_code: scanned {len(paths)} transcripts ({reparsed} re-parsed, "
              f"{len(paths) - reparsed} cached; {mode})", file=sys.stderr)
    if progress and processed != len(paths):
        progress(len(paths), len(paths), reparsed, max(0, len(paths) - reparsed))
    return list(new_cache.values())


def iter_dir_days(file_summaries):
    """Yield one Usage per (transcript dir, day) from already-parsed summaries."""
    for s in file_summaries:
        projdir = s.get("projdir") or ""
        for day, d in (s.get("by_day") or {}).items():
            yield Usage(projdir, day, d.get("input", 0), d.get("output", 0),
                        d.get("cache_read", 0), d.get("cache_write", 0), TOOL)
