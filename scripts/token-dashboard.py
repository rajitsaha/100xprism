#!/usr/bin/env python3
"""
token-dashboard.py — local, offline token-usage dashboard for Claude Code.

Reads ~/.claude/projects/**/*.jsonl (the transcripts Claude Code writes), and
serves a small web UI that breaks usage down by the four token "purposes"
(input / output / cache-read / cache-write), by project, by day, and by model.
It also shows a "startup bloat" meter: the fixed context (system prompt + tool/
skill/agent descriptions + SessionStart injections) re-sent on every turn, and a
"content composition" ESTIMATE (code written / files read / logs / model prose /
prompts) derived char-by-char from the transcripts — see the caveat below.

Machine-global + singleton: it reads the global ~/.claude/projects dir, so ONE
instance covers every session and every repo/directory on the machine. An explicit
launch replaces a prior owned instance; --ensure-daemon keeps a healthy one running.

Composition caveat: the API bills tokens per TURN as aggregates, not per content
block — so the composition view is an *estimate* of where text volume goes
(chars ÷ 4), not billed truth. Treat it as directional.

No third-party dependencies. Fully offline (no CDN). Uses an on-disk cache
keyed by file path + mtime + size, so only new/changed transcripts are re-parsed.

Usage:
    python3 scripts/token-dashboard.py            # serve on http://127.0.0.1:8787
    python3 scripts/token-dashboard.py --port 9000
    python3 scripts/token-dashboard.py --print    # print a text summary, no server
    python3 scripts/token-dashboard.py --no-open  # don't auto-open the browser

Pricing for the cost estimate is per-model (see pricing.py's RATES table);
unmatched model ids fall back to Opus-tier rates and are flagged in fallback_pct.
"""
import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from collections import defaultdict
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _value  # noqa: E402 — shared value layer (one source of truth)
import _summaries  # noqa: E402
import pricing  # noqa: E402
import adapters.claude_code as claude_code  # noqa: E402
import adapters.codex as codex  # noqa: E402
from adapters.registry import collectors as usage_collectors  # noqa: E402
import run_manifest  # noqa: E402
import _budget  # noqa: E402
import _suggest  # noqa: E402
import _config  # noqa: E402

HOME = os.path.expanduser("~")
REFRESH_SECONDS = 30  # auto-rebuild cadence; mtime/size cache makes a no-op pass cheap
PID_FILE = os.path.join(HOME, ".100xprism", "token-dashboard.pid")
GITHUB_CACHE_FILE = os.path.join(HOME, ".100xprism", "github-pr-insights.json")
GITHUB_CACHE_SECONDS = 1800
GITHUB_CACHE_VERSION = 3


def _empty():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def _add(dst, i, o, cr, cw):
    dst["input"] += i
    dst["output"] += o
    dst["cache_read"] += cr
    dst["cache_write"] += cw


def _delivery_economics(total_cost, directories):
    """Separate metered spend, observable delivery, and unmeasured value.

    Git activity is evidence that work moved through an engineering workflow; it
    is not a business-value score. Keeping these layers explicit prevents a
    delivery unit cost from being presented as ROI.
    """
    attributed_spend = 0.0
    outcomes = {
        "commits": 0, "prs": 0, "releases": 0,
        "files": 0, "insertions": 0, "deletions": 0,
    }
    for row in directories:
        value = row.get("value") or {}
        if row.get("cost") is None or value.get("kind") != "git":
            continue
        attributed_spend += row["cost"]
        for key in outcomes:
            outcomes[key] += (len(value.get(key, [])) if key == "releases"
                              else value.get(key, 0))

    total_cost = float(total_cost or 0)
    coverage = 100 * attributed_spend / total_cost if total_cost else 0.0
    unit_costs = {
        "per_commit": round(attributed_spend / outcomes["commits"], 2)
        if outcomes["commits"] else None,
        "per_pr": round(attributed_spend / outcomes["prs"], 2)
        if outcomes["prs"] else None,
        "per_release": round(attributed_spend / outcomes["releases"], 2)
        if outcomes["releases"] else None,
    }
    return {
        "spend": {
            "total": round(total_cost, 2),
            "attributed": round(attributed_spend, 2),
            "unattributed": round(max(0, total_cost - attributed_spend), 2),
            "coverage_pct": round(coverage, 1),
        },
        "outcomes": outcomes,
        "delivery_unit_cost": unit_costs,
        "business_value": {
            "status": "not_measured",
            "label": "Not measured",
            "reason": "Git delivery signals do not establish business or human value.",
        },
    }


def _delivery_by_day(directories):
    """Aggregate git outcomes and attributed token spend by calendar day."""
    empty = {"commits": 0, "prs": 0, "releases": 0, "files": 0,
             "insertions": 0, "deletions": 0, "attributed_spend": 0.0}
    by_day = defaultdict(lambda: dict(empty))
    for row in directories:
        value = row.get("value") or {}
        if value.get("kind") != "git":
            continue
        for day, cost in (row.get("day_cost") or {}).items():
            by_day[day]["attributed_spend"] += cost
        for day, outcomes in (value.get("by_day") or {}).items():
            target = by_day[day]
            for key in ("commits", "prs", "files", "insertions", "deletions"):
                target[key] += outcomes.get(key, 0) or 0
            target["releases"] += len(outcomes.get("releases") or [])
    return {day: {key: round(value, 4) if key == "attributed_spend" else value
                  for key, value in totals.items()}
            for day, totals in by_day.items()}


COMP_CATS = claude_code.COMP_CATS
COMP_LABELS = claude_code.COMP_LABELS

project_label = _value.project_label


def _partial_preview(summaries):
    """Small, cheap facts that can be shown before the full dataset is built."""
    totals = _empty()
    by_model = defaultdict(_empty)
    sources = defaultdict(int)
    metered_sessions = 0
    activity_sessions = 0
    messages = 0
    for summary in summaries:
        sources[summary.get("tool", "unknown")] += 1
        values = summary.get("totals") or {}
        _add(totals, values.get("input", 0), values.get("output", 0),
             values.get("cache_read", 0), values.get("cache_write", 0))
        for model, model_values in (summary.get("by_model") or {}).items():
            _add(by_model[model], model_values.get("input", 0),
                 model_values.get("output", 0), model_values.get("cache_read", 0),
                 model_values.get("cache_write", 0))
        if summary.get("activity_only"):
            activity_sessions += 1
        elif summary.get("turns"):
            metered_sessions += 1
        messages += summary.get("msgs", 0) or 0
    return {
        "records": len(summaries),
        "metered_sessions": metered_sessions,
        "activity_sessions": activity_sessions,
        "messages": messages,
        "tokens": sum(totals.values()),
        "estimated_cost": round(pricing.cost_by_model(by_model)[0], 2),
        "sources": dict(sorted(sources.items())),
    }


def build(verbose=True, progress=None, summaries=None, value_context=None,
          offline=False, value_store=None):
    """Scan every adapter (incremental via each adapter's own cache) and return
    the full dataset."""
    if progress:
        progress({"stage": "discovering", "tool": None, "current": 0, "total": 0,
                  "reparsed": 0, "cached": 0, "message": "Discovering local repositories"})
    discovered, dir_index = value_context if value_context is not None else _value.cached_scan()
    all_summaries = list(summaries) if summaries is not None else []
    completed_sources = []
    for collector in (usage_collectors() if summaries is None else []):
        kwargs = {"dir_index": dir_index} if collector.tool in ("claude-code", "cursor") else {}
        console_bucket = [-1]

        def report_scan(current, total, reparsed, cached, tool=collector.tool):
            if progress:
                progress({
                    "stage": "scanning", "tool": tool, "current": current,
                    "total": total, "reparsed": reparsed, "cached": cached,
                    "completed_sources": list(completed_sources),
                    "preview": _partial_preview(all_summaries),
                    "message": f"Scanning {tool} transcripts",
                })
            bucket = current // 100 if current else 0
            if verbose and (current == 0 or current == total or bucket > console_bucket[0]):
                console_bucket[0] = bucket
                print(f"  {tool}: {current}/{total} files "
                      f"({reparsed} parsed, {cached} cached)", file=sys.stderr)

        try:
            rows = collector.scan(verbose=verbose, progress=report_scan, **kwargs)
            all_summaries.extend(rows)
            completed_sources.append(collector.tool)
            if progress:
                progress({
                    "stage": "scanning", "tool": collector.tool,
                    "current": len(rows), "total": len(rows), "reparsed": 0,
                    "cached": 0, "completed_sources": list(completed_sources),
                    "preview": _partial_preview(all_summaries),
                    "message": f"Finished {collector.tool}; continuing with remaining sources",
                })
        except Exception as exc:
            print(f"warning: {collector.tool} collector failed: {exc}", file=sys.stderr)

    if progress:
        progress({
            "stage": "aggregating", "tool": None, "current": 0, "total": 1,
            "reparsed": 0, "cached": 0,
            "completed_sources": list(completed_sources),
            "preview": _partial_preview(all_summaries),
            "message": f"Building cross-tool views from {len(all_summaries)} source records",
        })

    totals = _empty()
    by_project = defaultdict(_empty)
    by_day = defaultdict(_empty)
    by_project_day_model = defaultdict(lambda: defaultdict(lambda: defaultdict(_empty)))
    by_day_model_global = defaultdict(lambda: defaultdict(_empty))
    by_tool_model_day = defaultdict(lambda: defaultdict(lambda: defaultdict(_empty)))
    by_model = defaultdict(_empty)
    by_model_day_tokens = defaultdict(lambda: defaultdict(_empty))
    comp_chars = defaultdict(int)
    comp_chars_by_day = defaultdict(lambda: defaultdict(int))
    sessions = 0
    fixed_samples = []
    total_msgs = 0
    by_session = []
    by_skill_agg = defaultdict(lambda: {"cost": 0.0, "invocations": 0, "exact": False})
    skill_by_day = defaultdict(lambda: defaultdict(lambda: {
        "cost": 0.0, "invocations": 0, "exact": False,
        "tools": set(), "projects": set(), "models": defaultdict(float),
    }))
    skill_dimensions = defaultdict(lambda: {
        "tools": set(), "projects": set(), "models": defaultdict(float),
    })
    bloat_by_day = defaultdict(list)
    main_cost_total = 0.0
    subagent_cost_total = 0.0
    main_subagent_cost_by_day = defaultdict(lambda: {"main": 0.0, "subagent": 0.0})

    for s in all_summaries:
        t = s["totals"]
        _add(totals, t["input"], t["output"], t["cache_read"], t["cache_write"])
        proj = s.get("project", "?")
        _add(by_project[proj], t["input"], t["output"], t["cache_read"], t["cache_write"])
        for day, d in s.get("by_day", {}).items():
            _add(by_day[day], d["input"], d["output"], d["cache_read"], d["cache_write"])
        for day, models in s.get("by_day_model", {}).items():
            for model, d in models.items():
                _add(by_project_day_model[proj][day][model], d["input"], d["output"], d["cache_read"], d["cache_write"])
                _add(by_day_model_global[day][model], d["input"], d["output"], d["cache_read"], d["cache_write"])
                _add(by_tool_model_day[s.get("tool", "unknown")][model][day],
                     d["input"], d["output"], d["cache_read"], d["cache_write"])
        for mdl, d in s.get("by_model", {}).items():
            _add(by_model[mdl], d["input"], d["output"], d["cache_read"], d["cache_write"])
        for day, models in s.get("by_day_model", {}).items():
            for model, d in models.items():
                _add(by_model_day_tokens[day][model], d["input"], d["output"], d["cache_read"], d["cache_write"])
        for cat, n in s.get("comp", {}).items():
            comp_chars[cat] += n
        for day, categories in s.get("comp_by_day", {}).items():
            for category, n in categories.items():
                comp_chars_by_day[day][category] += n
        if s.get("turns"):
            sessions += 1
            total_msgs += s.get("msgs", 0)
        if s.get("first_fixed"):
            fixed_samples.append(s["first_fixed"])
            if s.get("first_fixed_day"):
                bloat_by_day[s["first_fixed_day"]].append(s["first_fixed"])

        file_cost, _ = pricing.cost_by_model(s.get("by_model", {}))
        total_file_tokens = sum(t.values())
        if s.get("session_id") and total_file_tokens:
            by_session.append({
                "session_id": s["session_id"], "project": proj, "tool": s.get("tool"),
                "cost": round(file_cost, 4), "msgs": s.get("msgs", 0), "mtime": s.get("mtime", 0),
                "models": sorted(s.get("by_model", {}).keys()),
                "tokens": t, "cache": cache_metrics(s.get("by_model", {})),
                "context_growth": s.get("context_growth"),
                "first_activity": s.get("first_activity"), "last_activity": s.get("last_activity"),
                "historical": s.get("historical", False),
                "model_costs": {model: round(pricing.cost_of(tok, model), 4)
                                for model, tok in s.get("by_model", {}).items()},
                "day_cost": {
                    day: round(pricing.cost_by_model(models)[0], 4)
                    for day, models in s.get("by_day_model", {}).items()
                    if day != "unknown"
                },
            })
        if total_file_tokens:
            main_frac = sum(s.get("main_tokens", _empty()).values()) / total_file_tokens
            sub_frac = sum(s.get("subagent_tokens", _empty()).values()) / total_file_tokens
            main_cost_total += file_cost * main_frac
            subagent_cost_total += file_cost * sub_frac
        for day, buckets in s.get("main_subagent_by_day", {}).items():
            day_model_cost = pricing.cost_by_model(s.get("by_day_model", {}).get(day, {}))[0]
            day_tokens = sum(sum(bucket.values()) for bucket in buckets.values())
            if day_tokens:
                for role, bucket in buckets.items():
                    main_subagent_cost_by_day[day][role] += day_model_cost * sum(bucket.values()) / day_tokens
        for skill, tok in s.get("by_skill", {}).items():
            entry = by_skill_agg[skill]
            if skill in s.get("skill_exact", []):
                entry["exact"] = True
            # Prefer model-specific allocation below.  The older aggregate
            # fraction is retained only for summaries produced before the
            # skill/model dimensions were added.
            if skill not in s.get("by_skill_model", {}):
                frac = sum(tok.values()) / total_file_tokens if total_file_tokens else 0
                entry["cost"] += file_cost * frac
        for skill, n in s.get("skill_invocations", {}).items():
            by_skill_agg[skill]["invocations"] += n
        for skill, models in s.get("by_skill_model", {}).items():
            dimensions = skill_dimensions[skill]
            dimensions["tools"].add(s.get("tool") or "unknown")
            dimensions["projects"].add(s.get("project") or "?")
            for model, tokens in models.items():
                model_total = sum(s.get("by_model", {}).get(model, {}).values())
                model_cost = pricing.cost_of(s.get("by_model", {}).get(model, {}), model)
                allocated = (model_cost * sum(tokens.values()) / model_total
                             if model_total else 0)
                dimensions["models"][model] += allocated
                by_skill_agg[skill]["cost"] += allocated
        for day, skills in s.get("by_skill_day_model", {}).items():
            for skill, models in skills.items():
                day_entry = skill_by_day[day][skill]
                day_entry["tools"].add(s.get("tool") or "unknown")
                day_entry["projects"].add(s.get("project") or "?")
                if skill in s.get("skill_exact", []):
                    day_entry["exact"] = True
                for model, tokens in models.items():
                    model_total = sum(s.get("by_day_model", {}).get(day, {}).get(model, {}).values())
                    model_cost = pricing.cost_of(s.get("by_day_model", {}).get(day, {}).get(model, {}), model)
                    allocated = (model_cost * sum(tokens.values()) / model_total
                                 if model_total else pricing.cost_of(tokens, model))
                    day_entry["cost"] += allocated
                    day_entry["models"][model] += allocated
        for day, skills in s.get("skill_invocations_by_day", {}).items():
            for skill, n in skills.items():
                skill_by_day[day][skill]["invocations"] += n
        for skill in s.get("skill_exact", []):
            skill_dimensions[skill]["exact"] = True

    fixed_samples.sort()
    n = len(fixed_samples)
    median_fixed = fixed_samples[n // 2] if n else 0
    avg_fixed = sum(fixed_samples) / n if n else 0

    total_cost, fallback_tokens = pricing.cost_by_model(by_model)
    total_tokens = sum(totals.values())
    fallback_pct = round(100 * fallback_tokens / total_tokens, 1) if total_tokens else 0.0
    cost_by_purpose = {k: round(v, 4) for k, v in pricing.cost_breakdown(by_model).items()}

    by_day_model_cost = {
        day: {model: round(pricing.cost_of(tok, model), 4)
              for model, tok in models.items()}
        for day, models in by_day_model_global.items() if day != "unknown"
    }

    by_day_purpose_cost = {
        day: {purpose: round(cost, 4)
              for purpose, cost in pricing.cost_breakdown(models).items()}
        for day, models in by_day_model_global.items() if day != "unknown"
    }

    by_project_day_cost = {
        lbl: {day: round(pricing.cost_by_model(models)[0], 4) for day, models in days.items()}
        for lbl, days in by_project_day_model.items()
    }

    tool_model_day_cost = {
        tool: {
            model: {day: round(pricing.cost_of(tok, model), 4)
                    for day, tok in days.items() if day != "unknown"}
            for model, days in models.items()
        }
        for tool, models in by_tool_model_day.items()
    }
    tool_model_day_tokens = {
        tool: {
            model: {day: sum(tok.values()) for day, tok in days.items() if day != "unknown"}
            for model, days in models.items()
        }
        for tool, models in by_tool_model_day.items()
    }

    mangled_by_label, tokens_by_label, window_by_label, tool_by_label = {}, {}, {}, defaultdict(set)
    for s in all_summaries:
        label = s.get("project", "?")
        mangled = s.get("projdir", "")
        mangled_by_label.setdefault(mangled, label)
        tk = tokens_by_label.setdefault(label, _empty())
        t = s["totals"]
        _add(tk, t["input"], t["output"], t["cache_read"], t["cache_write"])
        tool_by_label[label].add(s.get("tool", "claude-code"))
        days = sorted(d for d in s.get("by_day", {}) if d != "unknown")
        activity_day = (s.get("activity") or {}).get("day")
        if activity_day:
            days.append(activity_day)
            days.sort()
        if days:
            lo, hi = window_by_label.get(label, (days[0], days[-1]))
            window_by_label[label] = (min(lo, days[0]), max(hi, days[-1]))
    tool_by_label = {lbl: "+".join(sorted(tools)) for lbl, tools in tool_by_label.items()}

    realdir_by_label = {}
    for real_dir, label in discovered.items():
        realdir_by_label.setdefault(label, real_dir)
    directories = assemble_directories(
        mangled_by_label, tokens_by_label, by_project_day_cost,
        window_by_label, tool_by_label,
        discovered=discovered, realdir_by_label=realdir_by_label,
        dir_index=dir_index, value_store=value_store)
    delivery_by_day = _delivery_by_day(directories)

    comp_tokens = {k: comp_chars.get(k, 0) // 4 for k in COMP_CATS}
    comp_sum = sum(comp_tokens.values()) or 1
    composition = sorted(
        ([COMP_LABELS[k], comp_tokens[k], round(100 * comp_tokens[k] / comp_sum, 1)]
         for k in COMP_CATS if comp_tokens[k]),
        key=lambda r: -r[1],
    )
    composition_by_day = {
        day: {category: chars // 4 for category, chars in categories.items()}
        for day, categories in comp_chars_by_day.items()
    }

    skill_rows = []
    for skill, entry in by_skill_agg.items():
        dimensions = skill_dimensions[skill]
        skill_rows.append({
            "skill": skill,
            "cost": round(entry["cost"], 4),
            "invocations": entry["invocations"],
            "exact": entry["exact"],
            "tools": sorted(dimensions["tools"]),
            "projects": sorted(dimensions["projects"]),
            "models": [
                {"model": model, "cost": round(cost, 4)}
                for model, cost in sorted(dimensions["models"].items(), key=lambda item: -item[1])
            ],
        })
    skill_rows.sort(key=lambda row: -row["cost"])

    by_session = sorted(by_session, key=lambda r: -r["cost"])
    by_skill = skill_rows

    today_str = datetime.now().strftime("%Y-%m-%d")
    today_cost = pricing.cost_by_model(by_day_model_global.get(today_str, {}))[0]
    week_start = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
    week_cost = sum(
        pricing.cost_by_model(models)[0]
        for day, models in by_day_model_global.items() if day >= week_start
    )
    budget = _budget.budget_summary(today_cost, week_cost)

    month_start = (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d")
    month_cost = sum(
        pricing.cost_by_model(models)[0]
        for day, models in by_day_model_global.items() if day >= month_start
    )

    dated_days = sorted(day for day in by_day_model_global if day != "unknown")
    source_counts = defaultdict(int)
    source_counts_by_day = defaultdict(lambda: defaultdict(int))
    activity_sources = defaultdict(lambda: {"sessions": 0, "messages": 0,
                                             "artifacts": 0, "projects": set()})
    activity_by_day = defaultdict(lambda: defaultdict(int))
    activity_sessions = []
    for s in all_summaries:
        if sum(s.get("totals", {}).values()):
            tool = s.get("tool", "unknown")
            source_counts[tool] += 1
            for day in s.get("by_day", {}):
                if day != "unknown":
                    source_counts_by_day[day][tool] += 1
        if s.get("activity_only"):
            tool = s.get("tool", "unknown")
            activity = s.get("activity") or {}
            entry = activity_sources[tool]
            if activity.get("day"):
                activity_by_day[activity["day"]][tool] += 1
            entry["sessions"] += 1
            entry["messages"] += activity.get("messages") or 0
            entry["artifacts"] += activity.get("artifacts") or 0
            entry["projects"].add(s.get("project", "unknown"))
            activity_sessions.append({
                "session_id": s.get("session_id"), "project": s.get("project"),
                "tool": tool, "day": activity.get("day"),
                "messages": activity.get("messages"),
                "artifacts": activity.get("artifacts"),
                "source": activity.get("source"),
            })
    activity_summary = {
        tool: {"sessions": value["sessions"], "messages": value["messages"],
               "artifacts": value["artifacts"], "projects": len(value["projects"])}
        for tool, value in sorted(activity_sources.items())
    }
    activity_sessions.sort(key=lambda r: r.get("day") or "", reverse=True)
    value_sources = defaultdict(int)
    for row in directories:
        value = row.get("value") or {}
        value_sources[value.get("kind", "none")] += 1
    delivery_economics = _delivery_economics(total_cost, directories)
    matched_cost = delivery_economics["spend"]["attributed"]
    outcome_totals = delivery_economics["outcomes"]
    outcome_coverage = delivery_economics["spend"]["coverage_pct"]
    economics = {
        "matched_cost": round(matched_cost, 2),
        "cost_per_commit": delivery_economics["delivery_unit_cost"]["per_commit"],
        "cost_per_pr": delivery_economics["delivery_unit_cost"]["per_pr"],
        "cost_per_release": delivery_economics["delivery_unit_cost"]["per_release"],
        **outcome_totals,
    }
    github = (_empty_github_summary([], False, "GitHub insights are interactive-only", False)
              if offline else _github_insights(directories))

    handoff_runs = [] if offline else _build_handoff_runs(all_summaries)

    dataset = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "transcripts": len(all_summaries),
        "sessions": sessions,
        "messages": total_msgs,
        "totals": totals,
        "cache": cache_metrics(by_model),
        "cache_by_model": {model: cache_metrics({model: tokens}) for model, tokens in by_model.items()},
        "total_cost": round(total_cost, 2),
        "cost_by_purpose": cost_by_purpose,
        "fallback_pct": fallback_pct,
        "pricing": {"as_of": pricing.PRICING_AS_OF, "sources": pricing.PRICING_SOURCES},
        "period_cost": {"today": round(today_cost, 2), "week": round(week_cost, 2),
                        "month": round(month_cost, 2), "lifetime": round(total_cost, 2)},
        "economics": economics,
        "delivery_economics": delivery_economics,
        "delivery_by_day": delivery_by_day,
        "data_quality": {
            "usage_sources": dict(sorted(source_counts.items())),
            "usage_sources_by_day": {day: dict(tools) for day, tools in source_counts_by_day.items()},
            "activity_sources": activity_summary,
            "activity_by_day": {day: dict(tools) for day, tools in activity_by_day.items()},
            "value_sources": dict(sorted(value_sources.items())),
            "pricing_coverage_pct": round(100 - fallback_pct, 1),
            "outcome_cost_coverage_pct": outcome_coverage,
            "window": {"start": dated_days[0] if dated_days else None,
                       "end": dated_days[-1] if dated_days else None},
        },
        "bloat": {"median": int(median_fixed), "avg": int(avg_fixed), "samples": n},
        "bloat_by_day": dict(bloat_by_day),
        "composition": composition,
        "composition_by_day": composition_by_day,
        "by_project": sorted(
            ([k, v, round(pricing.cost_of(v), 2)] for k, v in by_project.items()),
            key=lambda r: -(r[1]["input"] + r[1]["cache_read"] + r[1]["cache_write"]),
        )[:25],
        "by_day": sorted(([k, v] for k, v in by_day.items() if k != "unknown")),
        "by_day_model_cost": by_day_model_cost,
        "by_day_model_tokens": {
            day: dict(models) for day, models in by_model_day_tokens.items()
        },
        "by_day_purpose_cost": by_day_purpose_cost,
        "tool_model_day_cost": tool_model_day_cost,
        "tool_model_day_tokens": tool_model_day_tokens,
        "by_model": sorted(
            ([k, v] for k, v in by_model.items()),
            key=lambda r: -(r[1]["input"] + r[1]["cache_read"] + r[1]["cache_write"]),
        ),
        "by_project_day_cost": by_project_day_cost,
        "directories": directories,
        "by_session": by_session,
        "by_skill": by_skill,
        "skill_by_day": {
            day: {
                skill: {
                    "cost": round(value["cost"], 4),
                    "invocations": value["invocations"],
                    "exact": value["exact"],
                    "tools": sorted(value["tools"]),
                    "projects": sorted(value["projects"]),
                    "models": [
                        {"model": model, "cost": round(cost, 4)}
                        for model, cost in sorted(value["models"].items(), key=lambda item: -item[1])
                    ],
                }
                for skill, value in skills.items()
            }
            for day, skills in skill_by_day.items()
        },
        "main_subagent_split": {"main_cost": round(main_cost_total, 2),
                                 "subagent_cost": round(subagent_cost_total, 2)},
        "main_subagent_by_day": {
            day: {role: round(cost, 4) for role, cost in buckets.items()}
            for day, buckets in main_subagent_cost_by_day.items()
        },
        "budget": budget,
        "handoff_runs": handoff_runs,
        "activity": {"by_tool": activity_summary,
                     "sessions": activity_sessions[:100],
                     "session_count": len(activity_sessions)},
        "github": github,
    }
    dataset["suggestions"] = [
        {"impact_usd": round(s.impact_usd, 2), "title": s.title,
         "message": s.message, "action": s.action}
        for s in _suggest.suggestions(dataset)
    ]
    return dataset


def cache_metrics(by_model):
    """Model-priced no-cache counterfactual; local counters, not a provider bill."""
    totals = _empty()
    savings = 0.0
    for model, tokens in by_model.items():
        rates, _ = pricing.rates_for_model(model)
        for key in totals:
            totals[key] += tokens.get(key, 0)
        savings += tokens.get("cache_read", 0) * (rates["input"] - rates["cache_read"]) / 1_000_000
    read_context = totals["input"] + totals["cache_read"]
    all_context = read_context + totals["cache_write"]
    return {**totals,
            "cache_hit_ratio": totals["cache_read"] / read_context if read_context else 0,
            "cached_context_share": totals["cache_read"] / all_context if all_context else 0,
            "estimated_savings": round(savings, 8),
            "label": "Estimated cache savings at catalog input rates"}


def _build_handoff_runs(all_summaries):
    """Join every pair-loop run manifest to its round costs."""
    rows = []
    for path in run_manifest.list_manifests():
        try:
            manifest = run_manifest.load_manifest(path)
            cost = run_manifest.run_cost(manifest, all_summaries)
            reviewer_rounds = [r for r in manifest["rounds"] if r["role"] == "reviewer"]
            final_findings = reviewer_rounds[-1].get("findings") if reviewer_rounds else None
            rows.append({
                "run_id": manifest["run_id"], "task": manifest.get("task", ""),
                "day": next((r.get("started", "")[:10] for r in manifest.get("rounds", []) if r.get("started")), None),
                "rounds": manifest["outcome"].get("rounds", 0),
                "coder_cost": cost["coder"], "reviewer_cost": cost["reviewer"],
                "total_cost": cost["total"], "outcome": manifest["outcome"].get("verdict"),
                "pr": manifest.get("pr"), "merged": manifest["outcome"].get("merged"),
                "final_round_findings": final_findings,
            })
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            # A structurally-valid-JSON-but-schema-incomplete manifest (e.g.
            # missing "rounds"/"outcome", or a null "outcome" — `.get()` on
            # None raises AttributeError, not KeyError) must not take down
            # the whole dashboard build — skip just that one run.
            continue
    rows.sort(key=lambda r: -r["total_cost"])
    return rows


_GITHUB_SSH_RE = re.compile(r"git@github\.com:([^/]+)/(.+?)(?:\.git)?$")
_GITHUB_HTTPS_RE = re.compile(r"https://github\.com/([^/]+)/(.+?)(?:\.git)?$")
_DOC_EXTS = (".md", ".mdx", ".rst", ".adoc", ".txt")


def _github_slug(remote):
    """Return owner/repo for a GitHub remote URL, or None."""
    if not remote:
        return None
    for pattern in (_GITHUB_SSH_RE, _GITHUB_HTTPS_RE):
        m = pattern.match(remote.strip())
        if m:
            owner, repo = m.group(1), m.group(2).rstrip("/")
            return f"{owner}/{repo}"
    return None


def _github_repos_from_dirs(directories):
    repos = {}
    for row in directories:
        value = row.get("value") or {}
        if value.get("kind") != "git" or not row.get("dir"):
            continue
        try:
            remote = subprocess.run(
                ["git", "-C", row["dir"], "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=2, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        url = remote.stdout.strip()
        slug = _github_slug(url)
        if slug:
            repos.setdefault(slug, {"label": row["label"], "remote": url, "slug": slug,
                                    "source": "local-remote"})
    return list(repos.values())


def _github_users_from_config(cfg):
    users = cfg.get("users") or []
    if isinstance(users, str):
        users = [u.strip() for u in users.split(",")]
    if not isinstance(users, list):
        return []
    cleaned = []
    for user in users:
        if isinstance(user, str):
            user = user.strip()
            if user and re.match(r"^[A-Za-z0-9-]+$", user):
                cleaned.append(user)
    return cleaned


def _github_repos_from_config(cfg):
    values = cfg.get("repos") or []
    if isinstance(values, str):
        values = [v.strip() for v in values.split(",")]
    if not isinstance(values, list):
        return []
    repos = []
    for value in values:
        if not isinstance(value, str):
            continue
        value = value.strip().rstrip("/")
        slug = _github_slug(value)
        if not slug and re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", value):
            slug = value
        if slug:
            repos.append({
                "label": slug,
                "remote": f"https://github.com/{slug}",
                "slug": slug,
                "source": "github-config",
            })
    return repos


def _github_repos_for_users(users, per_user_limit):
    repos, errors = [], []
    for user in users:
        try:
            data = _gh_api_json(
                f"users/{user}/repos?type=owner&sort=updated&per_page={per_user_limit}",
                timeout=8)
            if not isinstance(data, list):
                continue
            for repo in data[:per_user_limit]:
                if not isinstance(repo, dict) or not repo.get("full_name"):
                    continue
                repos.append({
                    "label": repo.get("full_name"),
                    "remote": repo.get("html_url") or f"https://github.com/{repo['full_name']}",
                    "slug": repo["full_name"],
                    "source": f"github-user:{user}",
                })
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            errors.append(f"{user}: {exc}")
    return repos, errors


def _load_github_cache(key):
    try:
        with open(GITHUB_CACHE_FILE, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("key") == key and time.time() - cached.get("saved_at", 0) < GITHUB_CACHE_SECONDS:
            data = cached.get("data")
            if isinstance(data, dict):
                data["cached"] = True
                return data
    except (OSError, ValueError, TypeError):
        pass
    return None


def _save_github_cache(key, data):
    try:
        os.makedirs(os.path.dirname(GITHUB_CACHE_FILE), exist_ok=True)
        tmp = GITHUB_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"key": key, "saved_at": time.time(), "data": data}, f, indent=2)
        os.replace(tmp, GITHUB_CACHE_FILE)
    except OSError as exc:
        print(f"warning: could not write GitHub insights cache ({GITHUB_CACHE_FILE}): {exc}",
              file=sys.stderr)


def _gh_api_json(endpoint, timeout=6):
    result = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"gh api failed for {endpoint}")
    return json.loads(result.stdout or "null")


def _is_doc_file(filename):
    low = (filename or "").lower()
    return low.endswith(_DOC_EXTS) or low.startswith("docs/") or "/docs/" in low


def _empty_github_summary(repos, enabled, message, auth_ok=False, fetched=False):
    return {
        "enabled": enabled,
        "gh_installed": bool(shutil.which("gh")),
        "auth_ok": auth_ok,
        "fetched": fetched,
        "cached": False,
        "repo_count": len(repos),
        "repos": repos[:20],
        "summary": {
            "prs": 0, "merged": 0, "open": 0, "closed": 0,
            "comment_heavy": 0, "docs_prs": 0, "deleted_file_prs": 0,
            "additions": 0, "deletions": 0,
        },
        "top_commented": [],
        "pr_rows": [],
        "repo_rows": [],
        "message": message,
    }


def _bounded_int(value, default, low, high):
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(low, min(n, high))


def _github_insights(directories):
    """Opt-in GitHub PR insight collector over locally checked-out GitHub remotes.

    Uses `gh api` only when ~/.100xprism/config.json has
    {"github":{"enabled":true}} and `gh auth status` succeeds. Results are cached
    so the 30-second dashboard refresh does not repeatedly hit the network.
    """
    local_repos = _github_repos_from_dirs(directories)
    cfg = _config.load_config().get("github", {})
    users = _github_users_from_config(cfg)
    configured_repos = _github_repos_from_config(cfg)
    enabled = bool(cfg.get("enabled"))
    if not enabled:
        return _empty_github_summary(
            local_repos, False,
            "GitHub PR insights are available for detected GitHub remotes, but remote fetching is off. Enable with gh auth + github.enabled=true.")
    if not shutil.which("gh"):
        return _empty_github_summary(local_repos, True, "GitHub CLI (`gh`) is not installed or not on PATH.")
    try:
        auth = subprocess.run(
            ["gh", "auth", "status", "-h", "github.com"],
            capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return _empty_github_summary(local_repos, True, "Could not check GitHub CLI authentication.")
    if auth.returncode != 0:
        return _empty_github_summary(local_repos, True, "Run `gh auth login` to allow PR metadata fetches.", auth_ok=False)

    max_repos = _bounded_int(cfg.get("max_repos"), 12, 1, 50)
    max_prs = _bounded_int(cfg.get("max_prs_per_repo"), 30, 1, 100)
    max_pr_file_fetches = _bounded_int(cfg.get("max_pr_file_fetches_per_repo"), 3, 0, 30)
    max_user_repos = _bounded_int(cfg.get("max_user_repos_per_user"), 20, 1, 100)
    cache_key = {
        "schema": GITHUB_CACHE_VERSION,
        "local": sorted(r["slug"] for r in local_repos),
        "users": sorted(users),
        "repos": sorted(r["slug"] for r in configured_repos),
        "max_repos": max_repos,
        "max_prs": max_prs,
        "max_pr_file_fetches": max_pr_file_fetches,
        "max_user_repos": max_user_repos,
    }
    cached = _load_github_cache(cache_key)
    if cached:
        return cached

    user_repos, user_errors = _github_repos_for_users(users, max_user_repos) if users else ([], [])
    merged_repos = {r["slug"]: r for r in local_repos}
    for repo in configured_repos:
        merged_repos[repo["slug"]] = repo
    for repo in user_repos:
        merged_repos.setdefault(repo["slug"], repo)
    repos = list(merged_repos.values())
    if not repos:
        return _empty_github_summary(
            repos, True,
            "No GitHub remotes found locally and no configured GitHub users returned repositories.",
            auth_ok=True)
    selected = sorted(
        repos,
        key=lambda r: (0 if r.get("source") == "github-config" else 1, r["slug"])
    )[:max_repos]

    summary = {
        "prs": 0, "merged": 0, "open": 0, "closed": 0,
        "comment_heavy": 0, "docs_prs": 0, "deleted_file_prs": 0,
        "additions": 0, "deletions": 0,
    }
    pr_rows, top_commented, repo_rows, errors = [], [], [], list(user_errors)
    by_developer = {}
    for repo in selected:
        slug = repo["slug"]
        row = {"repo": slug, "label": repo["label"], "prs": 0, "merged": 0,
               "comment_heavy": 0, "docs_prs": 0, "deleted_file_prs": 0,
               "additions": 0, "deletions": 0}
        try:
            pulls = _gh_api_json(f"repos/{slug}/pulls?state=all&per_page={max_prs}", timeout=8)
            if not isinstance(pulls, list):
                pulls = []
            file_fetches = 0
            for pr in pulls[:max_prs]:
                number = pr.get("number")
                if number is None:
                    continue
                detail, files, files_sampled = {}, [], False
                if file_fetches < max_pr_file_fetches:
                    try:
                        detail = _gh_api_json(f"repos/{slug}/pulls/{number}", timeout=3)
                        if not isinstance(detail, dict):
                            detail = {}
                        files = _gh_api_json(
                            f"repos/{slug}/pulls/{number}/files?per_page=100",
                            timeout=3)
                        if not isinstance(files, list):
                            files = []
                        files_sampled = True
                    except (OSError, ValueError, RuntimeError,
                            subprocess.SubprocessError, json.JSONDecodeError) as exc:
                        errors.append(f"{slug}#{number} detail: {exc}")
                        detail, files, files_sampled = {}, [], False
                    file_fetches += 1
                meta = {**pr, **detail}
                additions = meta.get("additions")
                deletions = meta.get("deletions")
                if additions is None:
                    additions = sum(f.get("additions", 0) for f in files if isinstance(f, dict))
                if deletions is None:
                    deletions = sum(f.get("deletions", 0) for f in files if isinstance(f, dict))
                deleted_files = sum(1 for f in files if isinstance(f, dict) and f.get("status") == "removed")
                docs_files = sum(1 for f in files if isinstance(f, dict) and _is_doc_file(f.get("filename")))
                comments_sampled = bool(detail) or "comments" in pr or "review_comments" in pr
                comments = (int(meta.get("comments") or 0) + int(meta.get("review_comments") or 0)
                            if comments_sampled else None)
                state = meta.get("state")
                merged = bool(meta.get("merged_at"))
                author = ((meta.get("user") or {}).get("login") or "unknown")

                summary["prs"] += 1
                summary["merged"] += 1 if merged else 0
                summary["open"] += 1 if state == "open" else 0
                summary["closed"] += 1 if state == "closed" and not merged else 0
                summary["additions"] += additions
                summary["deletions"] += deletions
                summary["comment_heavy"] += 1 if comments is not None and comments >= 5 else 0
                summary["docs_prs"] += 1 if docs_files else 0
                summary["deleted_file_prs"] += 1 if deleted_files else 0

                row["prs"] += 1
                row["merged"] += 1 if merged else 0
                row["additions"] += additions
                row["deletions"] += deletions
                row["comment_heavy"] += 1 if comments is not None and comments >= 5 else 0
                row["docs_prs"] += 1 if docs_files else 0
                row["deleted_file_prs"] += 1 if deleted_files else 0
                dev = by_developer.setdefault(author, {
                    "developer": author, "prs": 0, "merged": 0, "open": 0,
                    "closed": 0, "comments": 0, "comment_heavy": 0,
                    "docs_prs": 0, "deleted_file_prs": 0,
                    "additions": 0, "deletions": 0,
                })
                dev["prs"] += 1
                dev["merged"] += 1 if merged else 0
                dev["open"] += 1 if state == "open" else 0
                dev["closed"] += 1 if state == "closed" and not merged else 0
                dev["comments"] += comments or 0
                dev["comment_heavy"] += 1 if comments is not None and comments >= 5 else 0
                dev["docs_prs"] += 1 if docs_files else 0
                dev["deleted_file_prs"] += 1 if deleted_files else 0
                dev["additions"] += additions
                dev["deletions"] += deletions
                top_commented.append({
                    "repo": slug, "number": number, "title": meta.get("title", ""),
                    "author": author, "comments": comments, "docs_files": docs_files,
                    "deleted_files": deleted_files, "additions": additions,
                    "deletions": deletions, "merged": merged,
                })
                pr_rows.append({
                    "repo": slug, "number": number, "title": meta.get("title", ""),
                    "url": meta.get("html_url") or f"https://github.com/{slug}/pull/{number}",
                    "author": author, "state": state, "merged": merged,
                    "created_at": meta.get("created_at"), "updated_at": meta.get("updated_at"),
                    "merged_at": meta.get("merged_at"), "comments": comments,
                    "comments_sampled": comments_sampled,
                    "docs_files": docs_files, "deleted_files": deleted_files,
                    "files_sampled": files_sampled, "additions": additions,
                    "deletions": deletions,
                })
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            errors.append(f"{slug}: {exc}")
        repo_rows.append(row)

    top_commented.sort(key=lambda r: (-(r["comments"] or 0), -r["deleted_files"], -r["docs_files"]))
    pr_rows.sort(key=lambda r: (r.get("updated_at") or "", r["repo"], r["number"]), reverse=True)
    repo_rows.sort(key=lambda r: (-r["comment_heavy"], -r["prs"], r["repo"]))
    developer_rows = sorted(
        by_developer.values(),
        key=lambda r: (-r["prs"], -r["comments"], r["developer"])
    )
    data = {
        "enabled": True, "gh_installed": True, "auth_ok": True, "fetched": True,
        "cached": False, "repo_count": len(repos), "repos": repos[:20],
        "summary": summary, "top_commented": top_commented[:10], "pr_rows": pr_rows,
        "repo_rows": repo_rows, "developer_rows": developer_rows[:20],
        "errors": errors[:5],
        "message": f"Fetched PR metadata from {len(selected)} GitHub repo(s): local remotes"
                   + (", configured repos" if configured_repos else "")
                   + (f" plus configured users {', '.join(users)}." if users else "."),
    }
    _save_github_cache(cache_key, data)
    return data


def fmt(n):
    n = float(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return str(int(n))


def print_summary(data):
    t = data["totals"]
    print(f"\nToken usage (Claude Code + Codex) — {data['generated']}")
    print(f"{data['transcripts']} transcripts, {data['sessions']} sessions, "
          f"{fmt(data['messages'])} billed messages\n")
    print(f"  input (uncached) : {fmt(t['input']):>8}")
    print(f"  output           : {fmt(t['output']):>8}")
    print(f"  cache READ       : {fmt(t['cache_read']):>8}   (re-sent context — usually the largest)")
    print(f"  cache WRITE      : {fmt(t['cache_write']):>8}")
    print(f"  est. cost        : ${data['total_cost']:,}"
          + (f"  ({data['fallback_pct']}% priced at fallback rates)" if data['fallback_pct'] else ""))
    print(f"\n  startup bloat (fixed context re-sent each turn): "
          f"median {fmt(data['bloat']['median'])} / avg {fmt(data['bloat']['avg'])} tokens")
    if data.get("composition"):
        print("\n  Content composition (ESTIMATE — char-based, not billed tokens):")
        for label, toks, pct in data["composition"]:
            print(f"    {pct:>5.1f}%  {fmt(toks):>7}  {label}")
    print("\n  Top projects (by input volume):")
    for name, v, c in data["by_project"][:10]:
        tot = v["input"] + v["cache_read"] + v["cache_write"]
        print(f"    {fmt(tot):>8} in / {fmt(v['output']):>6} out  ${c:>8,.0f}  {name}")
    print()


# ---------------------------------------------------------------- value × cost

def assemble_directories(mangled_by_label, tokens_by_label, by_project_day_cost,
                         window_by_label, tool_by_label,
                         discovered=None, realdir_by_label=None, dir_index=None,
                         value_store=None):
    """Build the unified per-directory rows (cost + tool-agnostic value)."""
    if discovered is None:
        discovered = {}
    if realdir_by_label is None:
        realdir_by_label = {}

    label_to_mangled = {}
    for mangled, label in mangled_by_label.items():
        label_to_mangled.setdefault(label, mangled)

    all_labels = set(mangled_by_label.values()) | set(discovered.values())

    rows = []
    owns_value_store = value_store is None
    value_store = _value.load_store() if owns_value_store else value_store
    value_store.setdefault("dirs", {})
    value_store.setdefault("version", _value.STORE_VERSION)
    value_store_dirty = [False]
    for label in all_labels:
        mangled = label_to_mangled.get(label)
        real = realdir_by_label.get(label) \
               or (dir_index.get(mangled) if (dir_index and mangled) else None) \
               or (_value.resolve_real_dir(mangled) if mangled else None)
        start, end = window_by_label.get(label, (None, None))
        daycost = by_project_day_cost.get(label, {})
        _c = round(sum(daycost.values()), 2) if daycost else 0.0
        cost = _c if _c else None
        tool = tool_by_label.get(label)
        value = (_value.cached_dir_value(real, label, tool, start, end,
                                         store=value_store, dirty=value_store_dirty)
                 if real else _value._empty_value())
        if cost and value.get("commits"):
            value["cost_per_commit"] = round(cost / value["commits"], 2)
        if cost and value.get("prs"):
            value["cost_per_pr"] = round(cost / value["prs"], 2)
        rows.append({
            "dir": real, "label": label, "tool": tool,
            "cost": cost, "day_cost": daycost,
            "tokens": tokens_by_label.get(label, _empty()),
            "window": {"start": start, "end": end}, "value": value,
        })
    if value_store_dirty[0] and owns_value_store:
        _value.save_store(value_store)
    rows.sort(key=lambda r: -(r["cost"] or 0))
    return rows


# ---------------------------------------------------------------- web UI

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>100xPrism — AI Economics</title>
<style>
:root{--ink:#0E1116;--surface:#171B22;--line:#262C36;--text:#E6E9EF;--muted:#8A93A2;
--cost:#E8B24A;--value:#5BD0A6;--warn:#E5704B;
--in:#58a6ff;--out:#f778ba;--cr:#3fb950;--cw:#d29922;
--m1:#58a6ff;--m2:#a371f7;--m3:#5BD0A6;--m4:#E8B24A;--m5:#f778ba;--m6:#8A93A2;
--glow:rgba(91,208,166,.28);--shadow:rgba(0,0,0,.28)}
[data-theme=light]{--ink:#F6F8FB;--surface:#FFFFFF;--line:#DCE3EE;--text:#111827;--muted:#64748B;
--cost:#B7791F;--value:#047857;--warn:#C2410C;--in:#2563EB;--out:#BE185D;--cr:#16A34A;--cw:#CA8A04;
--glow:rgba(37,99,235,.18);--shadow:rgba(15,23,42,.12)}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:radial-gradient(circle at 20% -10%,#18263a 0,transparent 34%) no-repeat,var(--ink);color:var(--text);
font:14px/1.55 'IBM Plex Sans',-apple-system,Segoe UI,Roboto,sans-serif;transition:background .35s ease,color .25s ease}
body[data-theme=light]{background:radial-gradient(circle at 20% -10%,#DCEBFF 0,transparent 34%) no-repeat,var(--ink)}
.num,td.n,.money{font-family:'IBM Plex Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}
header{padding:18px 28px;border-bottom:1px solid var(--line);display:flex;background:rgba(14,17,22,.88);
align-items:center;gap:16px;flex-wrap:wrap;position:sticky;top:0;z-index:10;backdrop-filter:blur(12px)}
body[data-theme=light] header{background:rgba(255,255,255,.84)}
h1{font-size:18px;margin:0}.sub{color:var(--muted);font-size:13px}
.wrap{padding:24px 28px 60px;max-width:1180px;margin:0 auto}
.dashboard-shell{display:grid;grid-template-columns:210px minmax(0,1fr);gap:18px;align-items:start}
.side-panel{position:sticky;top:82px;background:color-mix(in srgb,var(--surface),transparent 5%);
border:1px solid var(--line);border-radius:14px;padding:10px;box-shadow:0 18px 50px var(--shadow)}
.side-title{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin:4px 6px 8px}
.tabbtn{display:flex;align-items:center;gap:9px;width:100%;text-align:left;margin:4px 0;border-radius:10px;
background:transparent}.tabbtn.active{border-color:var(--value);background:linear-gradient(90deg,rgba(91,208,166,.16),transparent)}
.tabbtn small{display:block;color:var(--muted);font-size:11px}.tabpane{display:none}.tabpane.active{display:block;animation:riseIn .22s ease both}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:24px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px 18px;
transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}
.card:hover{transform:translateY(-2px);border-color:color-mix(in srgb,var(--in),var(--line) 45%);box-shadow:0 14px 34px var(--shadow)}
.card .lbl{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.card .val{font-size:26px;font-weight:600;margin-top:4px}
.card .note{color:var(--muted);font-size:12px;margin-top:6px}
.hero{position:relative;overflow:hidden;background:linear-gradient(135deg,rgba(91,208,166,.14),rgba(88,166,255,.1) 42%,rgba(232,178,74,.08));
border:1px solid var(--line);border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 24px 80px var(--shadow);
animation:riseIn .45s ease both}
.hero:before{content:"";position:absolute;right:-100px;top:-120px;width:320px;height:320px;border-radius:50%;
background:radial-gradient(circle,var(--glow),transparent 64%);animation:drift 9s ease-in-out infinite alternate}
.hero h2{font-size:24px;text-transform:none;letter-spacing:0;color:var(--text);border:0;margin:0 0 8px;padding:0}
.hero p{max-width:760px;margin:0;color:var(--muted)}
.hero-grid{display:grid;grid-template-columns:1.2fr .8fr;gap:18px;align-items:center;position:relative}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}.chip{border:1px solid var(--line);border-radius:999px;padding:5px 9px;background:rgba(23,27,34,.72);font-size:12px;color:var(--muted)}
.flow{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.flow .step{background:color-mix(in srgb,var(--surface),transparent 18%);border:1px solid var(--line);border-radius:12px;padding:12px;
animation:riseIn .45s ease both}.flow .step:nth-child(2){animation-delay:.05s}.flow .step:nth-child(3){animation-delay:.1s}.flow .step:nth-child(4){animation-delay:.15s}
.flow .k{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em}.flow .v{font-size:20px;font-weight:650;margin-top:4px}
.economics-flow{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:18px 0 0;position:relative}
.economics-stage{background:color-mix(in srgb,var(--surface),transparent 12%);border:1px solid var(--line);border-radius:12px;padding:14px;min-width:0}
.economics-stage.cost{border-top:3px solid var(--cost)}.economics-stage.delivery{border-top:3px solid var(--value)}.economics-stage.value{border-top:3px solid var(--muted)}
.economics-stage .stage-title{font-weight:650}.economics-stage .stage-value{font:650 22px/1.25 'IBM Plex Mono',ui-monospace,monospace;margin:6px 0 3px}
.coverage{height:8px;background:#21262d;border-radius:99px;overflow:hidden;margin:8px 0 5px}.coverage span{display:block;height:100%;background:var(--value)}
.status-pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 7px;font-size:11px;color:var(--muted);white-space:nowrap}
.status-pill.merged{color:var(--value);border-color:color-mix(in srgb,var(--value),var(--line) 55%)}
.status-pill.open{color:var(--in);border-color:color-mix(in srgb,var(--in),var(--line) 55%)}
.insights{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:24px}
.insight{background:linear-gradient(180deg,color-mix(in srgb,var(--surface),transparent 2%),color-mix(in srgb,var(--surface),transparent 14%));border:1px solid var(--line);border-radius:12px;padding:14px 16px;
animation:riseIn .38s ease both;transition:transform .18s ease,border-color .18s ease}.insight:hover{transform:translateY(-2px);border-color:var(--value)}
.insight .big{font-size:23px;font-weight:650}.insight .caption{color:var(--muted);font-size:12px;margin-top:4px}
.dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px;vertical-align:middle}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
margin:28px 0 12px;border-bottom:1px solid var(--line);padding-bottom:8px}
table{width:100%;border-collapse:collapse}
td,th{padding:7px 10px;text-align:right;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
th{color:var(--muted);font-weight:500;font-size:12px;text-transform:uppercase}
td:first-child,th:first-child{text-align:left}
.bar{height:9px;border-radius:5px;display:flex;overflow:hidden;background:#21262d;min-width:120px}
.bar span{display:block;height:100%}
.meter{background:#21262d;border-radius:6px;height:22px;position:relative;overflow:hidden;max-width:520px}
.meter b{position:absolute;left:0;top:0;bottom:0;border-radius:6px}
.meter em{position:absolute;left:10px;top:0;line-height:22px;font-style:normal;font-size:12px}
.scan-status{margin-bottom:18px;border-color:color-mix(in srgb,var(--value),var(--line) 55%)}
.scan-status .cards{margin:14px 0 0}.scan-detail{display:flex;flex-wrap:wrap;gap:8px 16px;margin-top:10px;font-size:12px;color:var(--muted)}
.legend{font-size:12px;color:var(--muted);margin:10px 0}.legend span{margin-right:16px}
button{background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:7px;
padding:6px 12px;cursor:pointer;font-size:13px}button:hover{border-color:var(--in)}
.muted{color:var(--muted)}
section{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
section{transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}section:hover{border-color:color-mix(in srgb,var(--value),var(--line) 55%);box-shadow:0 12px 36px var(--shadow)}
section h2{margin-top:0;border-bottom-color:var(--line)}
.cards2{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:8px 0 24px}
.wide{grid-column:1/-1}.table-wrap{overflow-x:auto}
.quality{display:flex;gap:10px 24px;flex-wrap:wrap;padding:12px 0 20px;color:var(--muted);font-size:12px}
.quality b{color:var(--text);font-weight:600}.rec{border:1px solid var(--line);border-radius:12px;margin:12px 0;background:color-mix(in srgb,var(--surface),transparent 8%);overflow:hidden}
.rec summary{cursor:pointer;list-style:none}.rec summary::-webkit-details-marker{display:none}
.rec-head{display:grid;grid-template-columns:112px minmax(0,1fr) 110px;gap:14px;align-items:center;padding:14px}
.rank{color:var(--cost);font:650 16px/1.2 'IBM Plex Mono',ui-monospace,monospace}.rec strong{font-size:15px}.action{color:var(--value);margin-top:3px}
.rec-body{border-top:1px solid var(--line);padding:14px}.whatif{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0}
.whatif .option{border:1px solid var(--line);border-radius:10px;padding:10px;background:color-mix(in srgb,var(--surface),transparent 14%)}
.whatif .option b{display:block;color:var(--cost);font-size:16px}.tradeoffs{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:12px}
.tradeoffs div{border-left:3px solid var(--line);padding-left:10px}.tradeoffs b{display:block;color:var(--text);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.examples{margin:10px 0 0;padding-left:18px}.examples li{margin:4px 0}.rec-pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:3px 8px;color:var(--muted);font-size:12px}
.snippet{border:1px solid var(--line);border-radius:10px;margin:10px 0;overflow:hidden;background:color-mix(in srgb,var(--ink),#000 12%)}
.snippet-head{display:flex;align-items:center;gap:8px;justify-content:space-between;padding:8px 10px;border-bottom:1px solid var(--line);color:var(--muted);font-size:12px}
.snippet pre{margin:0;padding:12px;overflow:auto;font:12px/1.45 'IBM Plex Mono',ui-monospace,monospace;color:var(--text)}
.snippet button{padding:4px 8px;font-size:12px}
.windowbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 16px}
.windowbar .label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em;margin-right:2px}
.windowbtn{border-radius:999px;padding:6px 10px}.windowbtn.active{border-color:var(--cost);background:rgba(232,178,74,.14);color:var(--text)}
.windowbtn.limited{border-style:dashed;color:var(--muted)}.window-note{color:var(--muted);font-size:12px;margin-left:4px}
.cost-tree{display:grid;gap:10px;margin-top:12px}.tree-node{border:1px solid var(--line);border-radius:12px;background:color-mix(in srgb,var(--surface),transparent 8%);overflow:hidden}
.tree-node summary{cursor:pointer;list-style:none}.tree-node summary::-webkit-details-marker{display:none}
.tree-row{display:grid;grid-template-columns:150px minmax(160px,1fr) 92px 90px;gap:12px;align-items:center;padding:11px 13px}
.tree-row:hover{background:color-mix(in srgb,var(--surface),var(--ink) 16%)}.tree-name{font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tree-meta{color:var(--muted);font-size:12px}.tree-bar{height:11px;border-radius:999px;background:#21262d;overflow:hidden;position:relative}.tree-bar span{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,var(--cost),var(--value))}
.tree-children{border-top:1px solid var(--line);padding:4px 0}.tree-children .tree-row{grid-template-columns:150px minmax(160px,1fr) 92px 90px;padding:8px 13px 8px 30px}.tree-empty{padding:12px 13px;color:var(--muted)}
@media(max-width:900px){.dashboard-shell{grid-template-columns:1fr}.side-panel{position:static;display:flex;overflow-x:auto;gap:6px}.side-title{display:none}.tabbtn{min-width:150px;margin:0;white-space:nowrap}}
@media(max-width:760px){.cards2,.hero-grid,.economics-flow{grid-template-columns:1fr}.wide{grid-column:auto}.wrap{padding:18px 14px 40px}header{padding:14px}.cards{grid-template-columns:1fr 1fr;gap:10px}.card .val{font-size:22px}.flow{grid-template-columns:1fr 1fr}.tree-row,.tree-children .tree-row,.rec-head{grid-template-columns:1fr;gap:5px}.whatif,.tradeoffs{grid-template-columns:1fr}td,th{padding:7px 8px}}
@media(max-width:430px){.cards{grid-template-columns:1fr}.sub{width:100%}}
.history-summary{margin:0 0 20px;padding:12px 16px;border:1px solid var(--line);border-radius:14px;background:var(--surface);overflow:hidden}.history-summary summary{cursor:pointer;font-weight:600;font-size:12px}.history-summary table{font-size:12px;margin-top:12px}.history-summary p{color:var(--muted);font-size:12px;margin:10px 0 0}
.howto{margin:-4px 0 14px;font-size:13px;color:var(--muted)}
.howto summary{cursor:pointer;color:var(--text);user-select:none}
.howto ul{margin:10px 0 0;padding-left:18px;line-height:1.6}
.howto code{background:var(--surface);border:1px solid var(--line);border-radius:4px;padding:0 4px}
.badge{display:inline-block;font:600 10px/1 'IBM Plex Mono',ui-monospace,monospace;
padding:3px 5px;border:1px solid var(--line);border-radius:4px;color:var(--muted)}
#tip{position:fixed;z-index:1000;pointer-events:none;display:none;background:var(--surface);
border:1px solid var(--line);border-radius:6px;padding:6px 9px;font:12px/1.3 'IBM Plex Mono',ui-monospace,monospace;
color:var(--text);max-width:280px;box-shadow:0 4px 16px rgba(0,0,0,.5)}
@keyframes riseIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
@keyframes drift{from{transform:translate3d(0,0,0) scale(1)}to{transform:translate3d(-28px,34px,0) scale(1.08)}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important}}
</style></head><body data-theme=dark>
<div id=tip role=tooltip></div>
<header><h1>100xPrism · AI Economics</h1>
<span class=sub id=meta></span>
<span style=margin-left:auto><button id=themeToggle data-action=theme>☾ Dark</button> <button data-action=refresh>↻ Rescan</button></span></header>
<div class=wrap id=app><p class=muted>Loading…</p></div>
<script>
const PRISM_SECURE = false;
let PRISM_CSRF='';
const SESSION_READY=PRISM_SECURE?(async()=>{
 const token=new URLSearchParams(location.hash.slice(1)).get('token');
 const response=await fetch('/api/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(token?{token}:{})});
 history.replaceState(null,'',location.pathname+location.search);
 if(response.ok)PRISM_CSRF=(await response.json()).csrf;
})():Promise.resolve();
const C={input:'var(--in)',output:'var(--out)',cache_read:'var(--cr)',cache_write:'var(--cw)'};
function applyTheme(theme){document.body.dataset.theme=theme;const b=document.getElementById('themeToggle');if(b)b.textContent=theme==='light'?'☀ Light':'☾ Dark';}
function toggleTheme(){const next=document.body.dataset.theme==='light'?'dark':'light';localStorage.setItem('100xprism-theme',next);applyTheme(next);}
applyTheme(localStorage.getItem('100xprism-theme')||'dark');
function fmt(n){n=+n;for(const[u,d]of[['B',1e9],['M',1e6],['K',1e3]])if(Math.abs(n)>=d)return(n/d).toFixed(1)+u;return''+Math.round(n);}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function copyText(id){const el=document.getElementById(id);if(!el)return;const text=el.textContent||'';navigator.clipboard&&navigator.clipboard.writeText?navigator.clipboard.writeText(text):null;}
function bar(v){const tot=v.input+v.output+v.cache_read+v.cache_write||1;
 const seg=k=>`<span style="width:${100*v[k]/tot}%;background:${C[k]}"></span>`;
 return `<div class=bar>${seg('cache_read')}${seg('cache_write')}${seg('input')}${seg('output')}</div>`;}
function legend(){return `<div class=legend>
 <span><i class=dot style=background:var(--cr)></i>cache read</span>
 <span><i class=dot style=background:var(--cw)></i>cache write</span>
 <span><i class=dot style=background:var(--in)></i>input</span>
 <span><i class=dot style=background:var(--out)></i>output</span></div>`;}
function emptyState(msg){return `<p class=muted style="padding:24px 0">${esc(msg)}</p>`;}
const TABS=[
 ['overview','Economics','cost → delivery → value'],
 ['recommendations','Recommendations','optimize tokens'],
 ['delivery','Delivery','dirs, churn, cost'],
 ['github','GitHub','developers + PRs'],
 ['sessions','Sessions','runs + handoffs'],
 ['skills','Skills','skill economics'],
 ['diagnostics','Diagnostics','coverage + bloat']
];
const WINDOWS=[['1d','1 day',1],['7d','7 days',7],['30d','30 days',30],['90d','3 months',90],['180d','6 months',180],['365d','1 year',365],['all','All time',null]];
let LAST_DATA=null;
function tabId(id){return 'tab-'+id;}
function activeTab(){const saved=localStorage.getItem('100xprism-tab')||'overview';return TABS.some(t=>t[0]===saved)?saved:'overview';}
function activeWindow(){const saved=localStorage.getItem('100xprism-window')||'all';return WINDOWS.some(w=>w[0]===saved)?saved:'all';}
function setWindow(id){if(!WINDOWS.some(w=>w[0]===id))id='all';localStorage.setItem('100xprism-window',id);if(LAST_DATA)render(LAST_DATA);}
function windowDef(id){return WINDOWS.find(w=>w[0]===id)||WINDOWS[WINDOWS.length-1];}
function meteredDays(d){return Object.keys((d||{}).by_day_model_cost||{}).sort();}
function daySpan(days){
 if(!days.length)return 0;
 const start=new Date(days[0]+'T00:00:00'), end=new Date(days[days.length-1]+'T00:00:00');
 return Math.floor((end-start)/86400000)+1;
}
function windowButtons(current,d){
 const days=meteredDays(d), span=daySpan(days);
 const note=days.length?`Available metered range: ${esc(days[0])} → ${esc(days[days.length-1])} (${span} calendar days, ${days.length} active days)`:'No dated metered usage yet';
 return `<div class=windowbar aria-label="Time window"><span class=label>Time window</span>`+
   WINDOWS.map(([id,label,n])=>{
    const limited=n!=null&&span>0&&span<n;
    const tip=limited?`Only ${span} calendar days of metered token history are available, so this window currently matches all available data.`:`Show ${label} of available metered token history.`;
    return `<button class="windowbtn ${id===current?'active':''} ${limited?'limited':''}" data-tip="${esc(tip)}" data-action="window" data-window="${esc(id)}">${esc(label)}</button>`;
   }).join('')+
   `<span class=window-note>${note}</span></div>`;
}
function datedKeysFrom(raw){
 const days=new Set();
 Object.values(raw||{}).forEach(v=>{
  if(!v||typeof v!=='object')return;
  Object.values(v).forEach(daysObj=>Object.keys(daysObj||{}).forEach(day=>{if(day&&day!=='unknown')days.add(day);}));
 });
 return [...days].sort();
}
function dayInWindow(day,current,allDays){
 const def=windowDef(current); if(def[2]==null)return true;
 // Undated records (day "unknown") sort after ISO dates; they count only in "all".
 const isDay=v=>/^\\d{4}-\\d{2}-\\d{2}$/.test(v); if(!isDay(day))return false;
 const dated=(allDays||[]).filter(isDay);
 const end=(dated.length?dated[dated.length-1]:new Date().toISOString().slice(0,10));
 const cutoff=new Date(end+'T00:00:00'); cutoff.setDate(cutoff.getDate()-def[2]+1);
 return day>=cutoff.toISOString().slice(0,10)&&day<=end;
}
function diagnosticDays(d,current){
 const rows=(d.by_day||[]).filter(row=>/^\\d{4}-\\d{2}-\\d{2}$/.test(row[0]));
 const days=rows.map(row=>row[0]).sort();
 return rows.filter(row=>dayInWindow(row[0],current,days)).sort((a,b)=>b[0].localeCompare(a[0]));
}
function sourceHistory(d){
 return Object.entries(d.tool_model_day_tokens||{}).sort(([a],[b])=>a.localeCompare(b)).map(([tool,models])=>{
  const days=[...new Set(Object.values(models).flatMap(raw=>Object.keys(raw||{})))].filter(day=>/^\\d{4}-\\d{2}-\\d{2}$/.test(day)).sort();
  return {tool,start:days[0]||null,end:days[days.length-1]||null,days:days.length};
 });
}
function historyPanel(d){
 const rows=sourceHistory(d);if(!rows.length)return '';
 const label=tool=>({'claude-code':'Claude Code',codex:'Codex',pi:'Pi'}[tool]||tool);
 return `<details class=history-summary><summary>Available history by tool</summary><div class=table-wrap><table><tr><th>Tool</th><th>First recorded usage</th><th>Latest recorded usage</th><th>Active days</th></tr>`+
  rows.map(row=>`<tr><td>${esc(label(row.tool))}</td><td>${esc(row.start||'No dated usage')}</td><td>${esc(row.end||'—')}</td><td>${row.days}</td></tr>`).join('')+
  `</table></div><p>Each tool has its own available history. Usage recorded in the local store is retained after source transcripts disappear; dates before the first collection may be unavailable.</p></details>`;
}
function sumWindowDaily(raw,current){
 const days=Object.keys(raw||{}).sort(); let total=0;
 for(const day of days)if(dayInWindow(day,current,days))for(const v of Object.values(raw[day]||{}))total+=+v||0;
 return total;
}
function selectedProjectCost(d,label,current){
 const daycost=((d.by_project_day_cost||{})[label])||{}, days=meteredDays(d);
 let total=0, observed=false;
 for(const [day,cost] of Object.entries(daycost))if(dayInWindow(day,current,days)){total+=+cost||0;observed=true;}
 return observed?total:null;
}
function projectRowsForWindow(d,current,includeUnpriced=false){
 const anchorDays=meteredDays(d);
 const rows=(d.directories||[]).map(row=>({...row,window_cost:selectedProjectCost(d,row.label,current),window_value:selectedRowValue(row,current,anchorDays)}));
 return rows.filter(row=>includeUnpriced||row.window_cost!=null)
   .sort((a,b)=>(b.window_cost||0)-(a.window_cost||0)||a.label.localeCompare(b.label));
}
function sumWindowTokens(d,current){
 const days=(d.by_day||[]).map(r=>r[0]).sort(), out={input:0,output:0,cache_read:0,cache_write:0};
 for(const [day,tok] of d.by_day||[]){
  if(!dayInWindow(day,current,days))continue;
  for(const k of Object.keys(out))out[k]+=+(tok||{})[k]||0;
 }
 return out;
}
function sumWindowPurposeCost(d,current){
 const raw=d.by_day_purpose_cost||{}, days=Object.keys(raw).sort(), out={input:0,output:0,cache_read:0,cache_write:0};
 for(const [day,costs] of Object.entries(raw)){
  if(!dayInWindow(day,current,days))continue;
  for(const k of Object.keys(out))out[k]+=+(costs||{})[k]||0;
 }
 return out;
}
const COMP_LABELS={prompts:'your prompts',model_output:'model output (prose)',code_authored:'code written (edits)',tool_calls:'tool calls',files_read:'code / files read',logs:'command output / logs',other_results:'other tool results'};
function selectedComposition(d,current){
 const raw=d.composition_by_day||{}, days=Object.keys(raw).sort(), totals={};
 for(const day of days){if(!dayInWindow(day,current,days))continue;for(const[k,v] of Object.entries(raw[day]||{}))totals[k]=(totals[k]||0)+(+v||0);}
 const total=Object.values(totals).reduce((a,v)=>a+v,0)||1;
 const rows=Object.entries(totals).map(([k,v])=>[COMP_LABELS[k]||k,v,Math.round(100*v/total*10)/10]).sort((a,b)=>b[1]-a[1]);
 return rows.length?rows:(current==='all'?(d.composition||[]):[]);
}
function selectedBloat(d,current){
 const raw=d.bloat_by_day||{}, days=Object.keys(raw).sort(), values=[];
 for(const day of days)if(dayInWindow(day,current,days))values.push(...(raw[day]||[]).map(Number).filter(Number.isFinite));
 if(!values.length)return current==='all'?(d.bloat||{median:0,avg:0,samples:0}):{median:0,avg:0,samples:0};
 values.sort((a,b)=>a-b);
 return {median:values[Math.floor(values.length/2)]||0,avg:values.reduce((a,v)=>a+v,0)/values.length,samples:values.length};
}
function selectedDelivery(d,current){
 const raw=d.delivery_by_day||{}, days=Object.keys(raw).sort(), anchorDays=meteredDays(d);
 const outcomes={commits:0,prs:0,releases:0,files:0,insertions:0,deletions:0};
 let attributed=0;
 for(const day of days){
  if(!dayInWindow(day,current,anchorDays))continue;
  const row=raw[day]||{};
  for(const key of Object.keys(outcomes))outcomes[key]+=(+row[key]||0);
  attributed+=(+row.attributed_spend||0);
 }
 const total=sumWindowDaily(d.by_day_model_cost||{},current);
 const coverage=total?100*attributed/total:0;
 return {spend:{total,attributed,unattributed:Math.max(0,total-attributed),coverage_pct:Math.round(coverage*10)/10},outcomes,delivery_unit_cost:{
  per_commit:outcomes.commits?Math.round(attributed/outcomes.commits*100)/100:null,
  per_pr:outcomes.prs?Math.round(attributed/outcomes.prs*100)/100:null,
  per_release:outcomes.releases?Math.round(attributed/outcomes.releases*100)/100:null
 },business_value:(d.delivery_economics||{}).business_value||{label:'Not measured',reason:'Git delivery signals do not establish business or human value.'}};
}
function selectedRowValue(row,current,anchorDays){
 const value=row.value||{}, raw=value.by_day||{}, days=Object.keys(raw).sort();
 if(!days.length)return value;
 const out={commits:0,prs:0,releases:[],files:0,insertions:0,deletions:0};
 for(const day of days){if(!dayInWindow(day,current,anchorDays||days))continue;const v=raw[day]||{};for(const key of ['commits','prs','files','insertions','deletions'])out[key]+=(+v[key]||0);out.releases.push(...(v.releases||[]));}
 return {...value,...out};
}
function sideTabs(current){
 return `<aside class=side-panel aria-label="Dashboard sections"><div class=side-title>Dashboard</div>`+
   TABS.map(([id,label,note])=>`<button class="tabbtn ${id===current?'active':''}" data-tab="${esc(id)}" data-action="tab"><span>${esc(label)}<small>${esc(note)}</small></span></button>`).join('')+
   `</aside>`;
}
function showTab(id){
 if(!TABS.some(t=>t[0]===id)) id='overview';
 localStorage.setItem('100xprism-tab',id);
 document.querySelectorAll('.tabpane').forEach(p=>p.classList.toggle('active',p.id===tabId(id)));
 document.querySelectorAll('.tabbtn').forEach(b=>b.classList.toggle('active',b.dataset.tab===id));
}
function pane(id,content,current){return `<div class="tabpane ${id===current?'active':''}" id="${tabId(id)}">${content}</div>`;}
function svgEl(w,h,inner,label){return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" role="img" aria-label="${esc(label)}" style="max-width:100%">${inner}</svg>`;}
function deliveryScoreboard(d,current){
 const rows=projectRowsForWindow(d,current).filter(r=>r.window_value&&r.window_value.kind==='git'&&(r.window_cost||r.window_value.commits||r.window_value.prs))
   .sort((a,b)=>(b.window_cost||0)-(a.window_cost||0)||(b.window_value.prs||0)-(a.window_value.prs||0)||(b.window_value.commits||0)-(a.window_value.commits||0))
   .slice(0,8);
 if(!rows.length) return emptyState('No git outcomes joined to token-bearing directories in the selected window yet.');
 const maxCost=Math.max(...rows.map(r=>r.window_cost||0),1);
 const maxOut=Math.max(...rows.map(r=>(r.window_value.prs||0)*3+(r.window_value.commits||0)),1);
 let h='<p class=muted style="margin-top:0">Read this row by row: spend and git delivery follow the global selected time window. Lower delivery cost is directional, not business ROI.</p>';
 h+='<table><tr><th>directory</th><th>spend</th><th>observable delivery</th><th>churn</th><th>delivery unit cost</th></tr>';
 for(const r of rows){
  const v=r.window_value||{}, outcomes=(v.prs||0)*3+(v.commits||0);
  const costW=100*(r.window_cost||0)/maxCost, outW=100*outcomes/maxOut;
  const shipped=`${v.prs||0} PRs · ${v.commits||0} commits · ${v.files||0} files`;
  const unit=v.prs?`$${(r.window_cost/v.prs).toFixed(2)} / PR`:v.commits?`$${(r.window_cost/v.commits).toFixed(2)} / commit`:'—';
  h+=`<tr><td>${esc(r.label)}</td><td><div class=bar data-tip="${esc(r.label)} selected-window spend: ${money2(r.window_cost)}"><span style="width:${costW}%;background:var(--cost)"></span></div><span class=muted>${money2(r.window_cost)}</span></td>`+
     `<td><div class=bar data-tip="${esc(shipped)}"><span style="width:${outW}%;background:var(--value)"></span></div><span class=muted>${esc(shipped)}</span></td>`+
     `<td class=n>+${fmt(v.insertions||0)} / -${fmt(v.deletions||0)}</td><td class=money>${esc(unit)}</td></tr>`;
 }
 return h+'</table>';
}
function costOverTime(d){
 const bpd=d.by_project_day_cost||{}; const days=[...new Set(Object.values(bpd).flatMap(o=>Object.keys(o)))].sort();
 if(!days.length) return emptyState('No dated cost yet.');
 const labels=Object.keys(bpd); const W=520,H=200,PL=46,PB=30,PR=28,PT=28;
 const totalByDay=days.map(day=>labels.reduce((a,l)=>a+(bpd[l][day]||0),0));
 const maxC=Math.max(...totalByDay,1); const X=i=>PL+(W-PL-PR)*i/Math.max(days.length-1,1);
 const Y=v=>H-PB-(H-PB-PT)*v/maxC;
 const path=`M${days.map((day,i)=>`${X(i)},${Y(totalByDay[i])}`).join(' L')}`;
 const area=`${path} L${X(days.length-1)},${H-PB} L${X(0)},${H-PB} Z`;
 const tickAttrs='fill="var(--muted)" font-size="11"';
 const xLabels=`<text x="${PL}" y="${H-2}" ${tickAttrs} text-anchor="start">${esc(days[0].slice(5))}</text>`+
   (days.length>1?`<text x="${W-PR}" y="${H-2}" ${tickAttrs} text-anchor="end">${esc(days[days.length-1].slice(5))}</text>`:'');
 const yLabel=`<text x="${8}" y="${PT+(H-PB-PT)/2}" ${tickAttrs} text-anchor="middle" transform="rotate(-90,8,${PT+(H-PB-PT)/2})">$ / day</text>`;
 const yTick=`<text x="${PL-4}" y="${PT}" ${tickAttrs} text-anchor="end" dominant-baseline="middle">$${Math.round(maxC)}</text>`;
 const dots=days.map((day,i)=>`<circle cx="${X(i)}" cy="${Y(totalByDay[i])}" r="2.5" fill="var(--cost)" data-tip="$${Math.round(totalByDay[i])} on ${esc(day)}"/>`).join('');
 return svgEl(W,H,`<path d="${area}" fill="var(--cost)" fill-opacity=".18"/><path d="${path}" fill="none" stroke="var(--cost)" stroke-width="2"/>${dots}${xLabels}${yLabel}${yTick}`,
   'Daily token cost over time; x axis dates, y axis dollars per day');
}
function purposeSplit(t){
 if((t.cache_read+t.cache_write+t.input+t.output)===0) return emptyState('No token usage yet.');
 const parts=[['cache_read',t.cache_read,'var(--cr)'],['cache_write',t.cache_write,'var(--cw)'],
   ['input',t.input,'var(--in)'],['output',t.output,'var(--out)']];
 const sum=parts.reduce((a,p)=>a+p[1],0)||1; let x=0; const W=520,H=34;
 const segs=parts.map(([k,v,c])=>{const w=(W)*v/sum; const pct=Math.round(100*v/sum);
   const r=`<rect x="${x}" y="0" width="${w}" height="${H}" fill="${c}" data-tip="${esc(k)}: ${esc(fmt(v))} (${pct}%)"/>`; x+=w; return r;}).join('');
 return svgEl(W,H,segs,'Share of tokens by purpose: cache read, cache write, input, output');
}
function compValue(d,needle,current){
 const rows=selectedComposition(d,current); const row=rows.find(r=>r[0].toLowerCase().includes(needle));
 return row?{label:row[0],tokens:row[1],pct:row[2]}:{label:needle,tokens:0,pct:0};
}
function workMix(d,current){
 const code=compValue(d,'code written',current), read=compValue(d,'files read',current), chat=compValue(d,'model output',current), logs=compValue(d,'logs',current);
 const items=[['Code authored',code,'var(--in)'],['Files/docs read',read,'var(--m2)'],['Model/chat prose',chat,'var(--out)'],['Terminal logs',logs,'var(--cw)']];
 return `<div class=insights>${items.map(([name,row,col])=>`<div class=insight><div class=lbl><i class=dot style=background:${col}></i>${esc(name)}</div><div class=big>${fmt(row.tokens)}</div><div class=caption>${row.pct||0}% of estimated conversation text volume</div></div>`).join('')}</div>`;
}
function outcomeFlow(d,current){
 const selectedSpend=sumWindowDaily(d.by_day_model_cost||{},current), de=selectedDelivery(d,current), spend=de.spend||{}, outcomes=de.outcomes||{}, unit=de.delivery_unit_cost||{}, value=de.business_value||{};
 return `<div class=economics-flow aria-label="AI economics measurement chain">
   <div class="economics-stage cost"><div class=stage-title>1 · Token economics</div><div class=stage-value>${money2(selectedSpend)}</div><div class=muted>selected-window list-price spend</div></div>
   <div class="economics-stage delivery"><div class=stage-title>2 · Observable delivery</div><div class=stage-value>${fmt(outcomes.prs||0)} PRs · ${fmt(outcomes.commits||0)} commits</div><div class=muted>${money2(spend.attributed)} attributed spend · selected-window outcomes</div><div class=coverage aria-label="${spend.coverage_pct||0}% of spend joined to git outcomes"><span style="width:${Math.min(100,spend.coverage_pct||0)}%"></span></div><div class=muted>${spend.coverage_pct||0}% attribution coverage · ${money2(unit.per_pr)} delivery cost / PR</div></div>
   <div class="economics-stage value"><div class=stage-title>3 · Business value</div><div class=stage-value>${esc(value.label||'Not measured')}</div><div class=muted>${esc(value.reason||'Delivery activity is not ROI.')}</div></div>
 </div>`;
}
function scopedGithub(github,current,d){
 const g=github||{}; if(!g.fetched||current==='all')return g;
 const days=meteredDays(d), rows=(g.pr_rows||[]).filter(row=>{
  const date=(row.merged_at||row.updated_at||row.created_at||'').slice(0,10);
  return date&&dayInWindow(date,current,days);
 });
 const summary={prs:rows.length,merged:0,open:0,closed:0,comment_heavy:0,docs_prs:0,deleted_file_prs:0,additions:0,deletions:0};
 const repos={}, developers={};
 for(const row of rows){
  const merged=!!row.merged; summary.merged+=merged?1:0; summary.open+=row.state==='open'?1:0; summary.closed+=row.state==='closed'&&!merged?1:0;
  summary.comment_heavy+=row.comments!=null&&row.comments>=5?1:0; summary.docs_prs+=row.docs_files?1:0; summary.deleted_file_prs+=row.deleted_files?1:0;
  summary.additions+=row.additions||0; summary.deletions+=row.deletions||0;
  const repo=repos[row.repo]||(repos[row.repo]={repo:row.repo,label:row.repo,prs:0,merged:0,comment_heavy:0,docs_prs:0,deleted_file_prs:0,additions:0,deletions:0});
  repo.prs++; repo.merged+=merged?1:0; repo.comment_heavy+=row.comments!=null&&row.comments>=5?1:0; repo.docs_prs+=row.docs_files?1:0; repo.deleted_file_prs+=row.deleted_files?1:0; repo.additions+=row.additions||0; repo.deletions+=row.deletions||0;
  const author=row.author||'unknown'; const dev=developers[author]||(developers[author]={developer:author,prs:0,merged:0,open:0,closed:0,comments:0,comment_heavy:0,docs_prs:0,deleted_file_prs:0,additions:0,deletions:0});
  dev.prs++; dev.merged+=merged?1:0; dev.open+=row.state==='open'?1:0; dev.closed+=row.state==='closed'&&!merged?1:0; dev.comments+=row.comments||0; dev.comment_heavy+=row.comments!=null&&row.comments>=5?1:0; dev.docs_prs+=row.docs_files?1:0; dev.deleted_file_prs+=row.deleted_files?1:0; dev.additions+=row.additions||0; dev.deletions+=row.deletions||0;
 }
 return {...g,summary,pr_rows:rows,repo_rows:Object.values(repos).sort((a,b)=>b.prs-a.prs),developer_rows:Object.values(developers).sort((a,b)=>b.prs-a.prs||b.comments-a.comments),top_commented:rows.filter(row=>row.comments!=null).sort((a,b)=>(b.comments||0)-(a.comments||0)).slice(0,10),message:`${g.message||''} Selected window: ${windowDef(current)[1]}.`};
}
function githubPanel(github,current,d){
 github=scopedGithub(github,current,d);
 const g=github||{}, repos=g.repos||[], s=g.summary||{};
 let rows=repos.slice(0,8).map(r=>{const remote=r.remote.replace('git@github.com:','github.com/').replace('https://github.com/','github.com/');
   return `<tr><td>${esc(r.label)}</td><td class=muted>${esc(remote)}</td></tr>`;}).join('');
 if(!rows) rows='<tr><td colspan=2 class=muted>No GitHub remotes detected in discovered/token-bearing directories.</td></tr>';
 const prDetails=(g.pr_rows||[]).slice(0,30).map(p=>{const status=p.merged?'merged':p.state==='open'?'open':'closed';
   const sampled=p.files_sampled?`${p.docs_files||0} docs · ${p.deleted_files||0} removed`:'not sampled';
   const comments=p.comments==null?'—':p.comments, churn=p.files_sampled?`+${fmt(p.additions||0)} / -${fmt(p.deletions||0)}`:'—';
   return `<tr><td><a href="${esc(p.url||'#')}" target=_blank rel="noopener noreferrer">${esc(p.repo)} #${p.number}</a></td><td><span class="status-pill ${status}">${status}</span></td><td>${esc(p.author||'unknown')}</td><td style="text-align:left">${esc(p.title||'')}</td><td class=n>${comments}</td><td class=muted>${sampled}</td><td class=n>${churn}</td><td class=muted>${esc((p.updated_at||'—').slice(0,10))}</td></tr>`;
  }).join('');
 const top=(g.top_commented||[]).slice(0,6).map(p=>`<tr><td>${esc(p.repo)} #${p.number}</td><td>${esc(p.author||'unknown')}</td><td>${esc(p.title||'')}</td><td class=n>${p.comments==null?'—':p.comments}</td><td class=n>${p.docs_files}</td><td class=n>${p.deleted_files}</td><td class=n>+${fmt(p.additions||0)} / -${fmt(p.deletions||0)}</td></tr>`).join('');
 const repoRows=(g.repo_rows||[]).slice(0,8).map(r=>`<tr><td>${esc(r.repo)}</td><td class=n>${r.prs}</td><td class=n>${r.merged}</td><td class=n>${r.comment_heavy}</td><td class=n>${r.docs_prs}</td><td class=n>${r.deleted_file_prs}</td><td class=n>+${fmt(r.additions||0)} / -${fmt(r.deletions||0)}</td></tr>`).join('');
 const devRows=(g.developer_rows||[]).slice(0,12).map(r=>`<tr><td>${esc(r.developer)}</td><td class=n>${r.prs}</td><td class=n>${r.merged}</td><td class=n>${r.open}</td><td class=n>${r.closed}</td><td class=n>${r.comments}</td><td class=n>${r.comment_heavy}</td><td class=n>${r.docs_prs}</td><td class=n>${r.deleted_file_prs}</td><td class=n>+${fmt(r.additions||0)} / -${fmt(r.deletions||0)}</td></tr>`).join('');
 const fetched=g.fetched?`<div class=cards style="margin:12px 0">
     <div class=card><div class=lbl>PRs fetched</div><div class=val>${s.prs||0}</div><div class=note>${s.merged||0} merged · ${s.open||0} open · ${s.closed||0} closed</div></div>
     <div class=card><div class=lbl>Comment-heavy PRs</div><div class=val>${s.comment_heavy||0}</div><div class=note>5+ issue/review comments</div></div>
     <div class=card><div class=lbl>Docs PRs</div><div class=val>${s.docs_prs||0}</div><div class=note>docs/ or markdown files touched</div></div>
     <div class=card><div class=lbl>Deleted-file PRs</div><div class=val>${s.deleted_file_prs||0}</div><div class=note>at least one removed file</div></div>
   </div>
   <h2 style="margin-top:18px">PR details</h2>
   <p class=muted>Newest fetched pull requests across the configured repository scope. File/churn detail is explicitly marked when sampled; status, author, title, and dates come from GitHub PR metadata.</p>
   <div class=table-wrap><table><tr><th>PR</th><th>status</th><th>author</th><th>title</th><th>comments</th><th>file detail</th><th>+ / -</th><th>updated</th></tr>${prDetails||'<tr><td colspan=8 class=muted>No PR details returned.</td></tr>'}</table></div>
   <h2 style="margin-top:18px">Developer breakdown</h2>
   <p class=muted>Grouped by GitHub PR author across configured users, configured repos, and detected local remotes. This is repository evidence, not performance scoring.</p>
   <div class=table-wrap><table><tr><th>developer</th><th>PRs</th><th>merged</th><th>open</th><th>closed</th><th>comments</th><th>comment-heavy</th><th>docs PRs</th><th>deleted-file PRs</th><th>+ / -</th></tr>${devRows||'<tr><td colspan=10 class=muted>No developer rows returned.</td></tr>'}</table></div>
   <h2 style="margin-top:18px">Repository breakdown</h2>
   <div class=table-wrap><table><tr><th>repo</th><th>PRs</th><th>merged</th><th>comment-heavy</th><th>docs PRs</th><th>deleted-file PRs</th><th>+ / -</th></tr>${repoRows||'<tr><td colspan=7 class=muted>No PR rows returned.</td></tr>'}</table></div>
   <h2 style="margin-top:18px">Most discussed PRs</h2>
   <div class=table-wrap><table><tr><th>PR</th><th>author</th><th>title</th><th>comments</th><th>docs files</th><th>deleted files</th><th>+ / -</th></tr>${top||'<tr><td colspan=7 class=muted>No comment-heavy PRs returned.</td></tr>'}</table></div>`
   : `<div class=cards style="margin:12px 0">
     <div class=card><div class=lbl>GitHub repos detected locally</div><div class=val>${g.repo_count||0}</div><div class=note>from remotes in discovered dirs</div></div>
     <div class=card><div class=lbl>Remote PR insights</div><div class=val>${g.enabled?'Blocked':'Off'}</div><div class=note>${g.gh_installed?'gh found':'gh missing'} · ${g.auth_ok?'authenticated':'auth needed'}</div></div>
   </div>
   <p class=muted>To fetch PR comments, reviews, changed files, additions, and deletions for local GitHub remotes: <code>gh auth login</code>, then add <code>{"github":{"enabled":true}}</code> to <code>~/.100xprism/config.json</code>.</p>
   <div class=table-wrap><table><tr><th>local repo</th><th>origin</th></tr>${rows}</table></div>`;
 return `<section style="margin:24px 0"><h2>GitHub deep dive <span class=muted style="text-transform:none;font-weight:400">— local remotes + opt-in remote PR metadata</span></h2>
   <p class=muted>${esc(g.message||'GitHub analytics are not enabled.')}</p>
   ${fetched}
   ${(g.errors||[]).length?`<p class=muted>Fetch warnings: ${esc((g.errors||[]).join(' | '))}</p>`:''}
 </section>`;
}
function costByDir(d,current){
 const rows=projectRowsForWindow(d,current).slice(0,12); if(!rows.length) return emptyState('No directory cost in the selected window yet.');
 const mx=Math.max(...rows.map(r=>r.window_cost),1); const H=rows.length*26+8,W=520;
 const bars=rows.map((r,i)=>{const w=(W-160)*r.window_cost/mx; const y=i*26+4;
   return `<text x="0" y="${y+14}" fill="var(--muted)" font-size="12">${esc(r.label.slice(-26))}</text>`+
     `<rect x="150" y="${y+3}" width="${w}" height="14" rx="3" fill="var(--cost)" data-tip="${esc(r.label)}: ${money2(r.window_cost)} in selected window" tabindex="0"/>`+
     `<text x="${156+w}" y="${y+14}" fill="var(--text)" font-size="11">$${Math.round(r.window_cost)}</text>`;}).join('');
 return svgEl(W,H,bars,'Estimated token cost by directory for the selected time window, highest first');
}
function donut(totals){
 const parts=[['cache_read',totals.cache_read,'var(--cr)'],['cache_write',totals.cache_write,'var(--cw)'],
   ['input',totals.input,'var(--in)'],['output',totals.output,'var(--out)']];
 const sum=parts.reduce((a,p)=>a+p[1],0); if(!sum) return emptyState('No token usage yet.');
 const W=180,H=180,cx=90,cy=90,r=70,rInner=42;
 const pt=(rad,a)=>[cx+rad*Math.cos(a),cy+rad*Math.sin(a)];
 const nonzero=parts.filter(p=>p[1]>0);
 let angle=-Math.PI/2, path='';
 if(nonzero.length===1){
  // A single 100%-share segment is a full circle: start===end for a lone SVG
  // arc command, which the spec treats as a zero-length (invisible) segment.
  // Split it into two half-circle arcs instead so the wedge actually renders.
  const[k,v,c]=nonzero[0];
  const a1=angle, aMid=angle+Math.PI, a2=angle+2*Math.PI;
  const[x1,y1]=pt(r,a1),[xm,ym]=pt(r,aMid),[x2,y2]=pt(r,a2);
  const[ix1,iy1]=pt(rInner,a1),[ixm,iym]=pt(rInner,aMid),[ix2,iy2]=pt(rInner,a2);
  path=`M${x1},${y1} A${r},${r} 0 1 1 ${xm},${ym} A${r},${r} 0 1 1 ${x2},${y2} `+
       `L${ix2},${iy2} A${rInner},${rInner} 0 1 0 ${ixm},${iym} A${rInner},${rInner} 0 1 0 ${ix1},${iy1} Z`;
  const center=`<text x="${cx}" y="${cy-2}" fill="var(--text)" text-anchor="middle" font-size="18">$${sum.toFixed(2)}</text><text x="${cx}" y="${cy+16}" fill="var(--muted)" text-anchor="middle" font-size="10">list-price spend</text>`;
  return svgEl(W,H,`<path d="${path}" fill="${c}" data-tip="${esc(k)}: $${v.toFixed(2)} (100%)"/>${center}`,
    'Estimated dollar spend share by token purpose');
 }
 for(const[k,v,c]of parts){ if(!v) continue;
   const frac=v/sum, a1=angle, a2=angle+frac*2*Math.PI; angle=a2;
   const[x1,y1]=pt(r,a1),[x2,y2]=pt(r,a2);
   const[ix1,iy1]=pt(rInner,a1),[ix2,iy2]=pt(rInner,a2);
   const large=frac>0.5?1:0;
   path+=`<path d="M${x1},${y1} A${r},${r} 0 ${large} 1 ${x2},${y2} L${ix2},${iy2} A${rInner},${rInner} 0 ${large} 0 ${ix1},${iy1} Z" fill="${c}" data-tip="${esc(k)}: $${v.toFixed(2)} (${Math.round(100*frac)}%)"/>`;
 }
 path+=`<text x="${cx}" y="${cy-2}" fill="var(--text)" text-anchor="middle" font-size="18">$${sum.toFixed(2)}</text><text x="${cx}" y="${cy+16}" fill="var(--muted)" text-anchor="middle" font-size="10">list-price spend</text>`;
 return svgEl(W,H,path,'Estimated dollar spend share by token purpose');
}
function budgetBar(block,label){
 if(block.limit==null) return '';
 const pct=Math.min(100,(block.fraction||0)*100);
 const col=block.level==='alert'?'var(--warn)':block.level==='warn'?'var(--cw)':'var(--value)';
 return `<div style="margin:6px 0"><div class=muted style="font-size:11px;margin-bottom:2px">${esc(label)}: $${block.spend.toFixed(0)} / $${block.limit.toFixed(0)}</div>
   <div class=meter style="height:10px"><b style="width:${pct}%;background:${col}"></b></div></div>`;
}
function scopedSessions(rows,current,d){
 const days=meteredDays(d), scoped=(rows||[]).map(row=>{
  const dayCost=row.day_cost||{}, dated=Object.keys(dayCost).sort();
  const windowCost=dated.length?Object.entries(dayCost).reduce((sum,[day,cost])=>sum+(dayInWindow(day,current,days)?+cost||0:0),0):row.cost;
  return {...row,window_cost:windowCost,window_days:dated};
 }).filter(row=>row.window_days.length?row.window_cost>0:!row.mtime||dayInWindow(new Date(row.mtime*1000).toISOString().slice(0,10),current,days));
 return scoped;
}
function sessionsTable(rows,current,d){
 const scoped=scopedSessions(rows,current,d).sort((a,b)=>b.window_cost-a.window_cost);
 if(!scoped.length) return emptyState(`No sessions in the selected window (${windowDef(current)[1]}).`);
 let h='<table><tr><th>session</th><th>project</th><th>tool</th><th>msgs</th><th>cost</th><th>lifetime tokens</th><th>context growth</th><th>lifetime cache savings</th></tr>';
 for(const r of scoped.slice(0,20)){
  h+=`<tr><td class=muted>${esc(r.session_id.slice(0,8))}</td><td>${esc(r.project)}</td>`+
     `<td>${toolBadge(r.tool)}</td><td class=n>${r.msgs}</td><td class=money>$${r.window_cost.toFixed(2)}</td><td>${fmt(Object.values(r.tokens||{}).reduce((sum,n)=>sum+n,0))}${r.historical?' · historical':''}</td><td>${r.context_growth?`${fmt(r.context_growth.first_context_tokens)} → ${fmt(r.context_growth.last_context_tokens)}${r.context_growth.growth_ratio?' · '+(+r.context_growth.growth_ratio).toFixed(1)+'×':''}`:'unavailable'}</td><td>${money2((r.cache||{}).estimated_savings)}<div class=muted>${(((r.cache||{}).cache_hit_ratio||0)*100).toFixed(1)}% hits</div></td></tr>`;
 }
 return h+'</table>';
}
function contextGrowthTable(rows,current,d){
 const scoped=scopedSessions(rows,current,d).filter(r=>r.context_growth&&r.context_growth.delta_tokens>0).sort((a,b)=>b.context_growth.delta_tokens-a.context_growth.delta_tokens).slice(0,10);
 if(!scoped.length)return emptyState('No observed context growth in the selected sessions.');
 return '<p class=muted>Lifetime growth in per-turn context, ranked by added tokens. Cumulative session usage is excluded.</p><table><tr><th>session</th><th>project</th><th>tool</th><th>first → last context</th><th>added tokens</th><th>largest observed context</th></tr>'+scoped.map(r=>`<tr><td>${esc(r.session_id.slice(0,8))}</td><td>${esc(r.project)}</td><td>${toolBadge(r.tool)}</td><td>${fmt(r.context_growth.first_context_tokens)} → ${fmt(r.context_growth.last_context_tokens)}</td><td>${fmt(r.context_growth.delta_tokens)}</td><td>${fmt(r.context_growth.max_context_tokens)}</td></tr>`).join('')+'</table>';
}
function cacheModelTable(d){
 const rows=Object.entries(d.cache_by_model||{}).sort((a,b)=>b[1].estimated_savings-a[1].estimated_savings);
 if(!rows.length)return '';
 return '<details><summary>Lifetime cache economics by model</summary><table><tr><th>model</th><th>cache-read tokens</th><th>hit ratio</th><th>cached-context share</th><th>estimated savings</th></tr>'+rows.map(([model,c])=>`<tr><td>${esc(model)}</td><td>${fmt(c.cache_read)}</td><td>${(c.cache_hit_ratio*100).toFixed(1)}%</td><td>${(c.cached_context_share*100).toFixed(1)}%</td><td>${money2(c.estimated_savings)}</td></tr>`).join('')+'</table></details>';
}
function selectedSkills(d,current){
 const raw=d.skill_by_day||{}, days=Object.keys(raw).sort(), base=Object.fromEntries((d.by_skill||[]).map(row=>[row.skill,row]));
 const totals={};
 for(const day of days){if(!dayInWindow(day,current,days))continue;for(const[skill,value]of Object.entries(raw[day]||{})){
  const row=totals[skill]||(totals[skill]={cost:0,invocations:0,exact:false,tools:new Set(),projects:new Set(),models:{}});
  row.cost+=(+value.cost||0); row.invocations+=(+value.invocations||0); row.exact=row.exact||!!value.exact;
  (value.tools||[]).forEach(tool=>row.tools.add(tool)); (value.projects||[]).forEach(project=>row.projects.add(project));
  (value.models||[]).forEach(model=>row.models[model.model]=(row.models[model.model]||0)+(+model.cost||0));
 }}
 if(!Object.keys(totals).length)return current==='all'?(d.by_skill||[]):[];
 return Object.entries(totals).map(([skill,value])=>({...base[skill],skill,cost:value.cost,invocations:value.invocations,exact:value.exact,
  tools:[...value.tools].sort(),projects:[...value.projects].sort(),models:Object.entries(value.models).map(([model,cost])=>({model,cost})).sort((a,b)=>b.cost-a.cost)})).sort((a,b)=>b.cost-a.cost);
}
function listLabel(values,limit=2){
 const items=(values||[]).filter(Boolean); if(!items.length)return '—';
 return items.length<=limit?items.join(', '):`${items.slice(0,limit).join(', ')} +${items.length-limit} more`;
}
function skillsTable(rows,current,d){
 rows=selectedSkills(d,current);
 if(!rows.length) return emptyState('No skill attribution yet.');
 let h='<table><tr><th>skill</th><th>agent</th><th>directory</th><th>model</th><th>invocations</th><th>cost</th><th>$/invocation</th><th></th></tr>';
 for(const r of rows.slice(0,20)){
  const perInv=r.cost/(r.invocations||1);
  const agents=(r.tools||[]).map(toolBadge).join(' · '), modelEntries=(r.models||[]).map(model=>({label:modelDisplay(model.model).label,tip:modelDisplay(model.model).tip})), models=modelEntries.map(model=>model.label);
  h+=`<tr><td>${esc(r.skill)}</td><td title="${esc((r.tools||[]).join(', '))}">${agents||'—'}</td>`+
     `<td title="${esc((r.projects||[]).join(', '))}">${esc(listLabel(r.projects))}</td>`+
     `<td title="${esc(modelEntries.map(model=>model.tip).join('\\n'))}">${esc(listLabel(models))}</td>`+
     `<td class=n>${r.invocations}</td><td class=money>$${r.cost.toFixed(2)}</td>`+
     `<td class=money>$${perInv.toFixed(3)}</td><td>${r.exact?'<span class=badge title="exact — native Claude Code attribution">exact</span>':'<span class=badge title="attributed — heuristic segmentation">attr.</span>'}</td></tr>`;
 }
 return h+'</table>';
}
function handoffTable(rows,current,d){
 const days=meteredDays(d), scoped=(rows||[]).filter(row=>!row.day||dayInWindow(row.day,current,days));
 if(!scoped.length) return '';
 let h='<h2>Pair-loop handoff runs</h2><table><tr><th>run</th><th>rounds</th><th>coder $</th><th>reviewer $</th><th>total $</th><th>outcome</th><th>PR</th></tr>';
 for(const r of scoped){
  h+=`<tr><td class=muted>${esc(r.task||r.run_id)}</td><td class=n>${r.rounds}</td>`+
     `<td class=money>$${r.coder_cost.toFixed(2)}</td><td class=money>$${r.reviewer_cost.toFixed(2)}</td>`+
     `<td class=money>$${r.total_cost.toFixed(2)}</td><td>${esc(r.outcome||'—')}</td>`+
     `<td>${r.pr?('#'+r.pr):'—'}</td></tr>`;
 }
 return h+'</table>';
}
function suggestionsCard(rows,scale,scopeLabel){
 if(!rows.length) return '';
 let h='<section style="margin:24px 0"><h2>Recommended next actions <span class=muted style="text-transform:none;font-weight:400">— what-if savings, autonomy, and tradeoffs</span></h2><p class=muted style="margin-top:0">These are not hard mandates. Each card shows the observed signal, a conservative what-if model, examples of changes, and the expected impact on agent autonomy/performance.</p>';
 rows.forEach((s,i)=>{h+=suggestionCard(s,i,scale,scopeLabel);});
 return h+'</section>';
}
function suggestionDetails(s){
 const title=(s.title||'').toLowerCase();
 if(title.includes('fixed context')||title.includes('startup')){
  return {
   why:'Every new session pays for fixed startup context before useful work starts. Reducing duplicated always-loaded instructions lowers cost without changing the agent loop.',
   examples:[
    'Move rarely used policy, release, or debugging playbooks from always-loaded files into on-demand skills/docs.',
    'Deduplicate repeated MCP/tool descriptions that appear in multiple startup surfaces.',
    'Keep the short routing contract in the default context; put long examples behind explicit reads.'
   ],
   autonomy:'Usually positive. The agent still has access to the knowledge, but retrieves it only when the task needs it.',
   performance:'May improve startup speed and focus. Risk: if instructions are moved too far away, the agent may need one extra lookup on specialized tasks.',
   monitor:'Watch median fixed tokens, first-turn latency, and whether task quality drops on specialized workflows.'
  };
 }
 if(title.includes('skill')){
  return {
   why:'A high cost per invocation usually means the skill loads too much context or produces too broad an answer by default.',
   examples:[
    'Keep the skill trigger, but replace long inline examples with links/paths the agent reads only when needed.',
    'Split “quick answer” and “deep review” modes so routine calls do not pay for the deep mode.',
    'Tighten the default output contract: decision, evidence, next action; optional appendix only on request.'
   ],
   autonomy:'Neutral if the skill remains available. Negative only if you remove capability instead of moving depth behind on-demand reads.',
   performance:'Routine calls get cheaper and usually faster. Deep calls may add one extra read step but preserve quality.',
   monitor:'Watch $/invocation, invocation count, and whether follow-up corrections increase.'
  };
 }
 if(title.includes('route')||title.includes('short tasks')||title.includes('difficulty')){
  return {
   why:'Short deterministic tasks often do not need the most expensive model. Routing simple edits/lookups to lower-cost models saves money while reserving premium models for ambiguity and risk.',
   examples:[
    'Use lower-cost routing for formatting, small renames, obvious test updates, and mechanical docs edits.',
    'Keep premium routing for architecture decisions, high-risk migrations, unclear failures, and security-sensitive changes.',
    'Escalate automatically when tests fail, uncertainty is high, or the diff crosses a risk threshold.'
   ],
   autonomy:'Preserved if routing is a default with escalation, not a hard ban. The agent can still choose premium when risk rises.',
   performance:'Simple tasks should stay similar or faster. Complex tasks can degrade if routed too aggressively, so escalation rules matter.',
   monitor:'Watch short-session spend, rework rate, failed tests after short sessions, and manual escalations.'
  };
 }
 return {
  why:'This signal crossed the dashboard threshold for avoidable token cost.',
  examples:['Apply the proposed change to a small subset first.', 'Compare cost and quality before broad rollout.', 'Keep a rollback path if task quality changes.'],
  autonomy:'Depends on implementation. Prefer defaults and on-demand context over removing capabilities.',
  performance:'Expected to improve cost efficiency; validate quality with the same tests/review loop.',
  monitor:'Watch spend, task completion rate, test failures, and follow-up correction rate.'
 };
}
function suggestionSnippets(s){
 const title=(s.title||'').toLowerCase();
 if(title.includes('fixed context')||title.includes('startup')){
  return [
   ['CLAUDE.md: keep startup small', 'markdown', [
    '# Project AI instructions',
    '',
    'Default behavior:',
    '- Prefer small, testable changes.',
    '- Read only the files needed for the current task.',
    '- Use repository search before opening large files.',
    '',
    'On demand:',
    '- For release workflow, read docs/release.md.',
    '- For architecture review, read docs/architecture.md.',
    '- For incident/debug workflow, read docs/debug-playbook.md.'
   ].join('\\n')],
   ['Diff: move rarely used content out of startup', 'diff', [
    '--- CLAUDE.md',
    '+++ CLAUDE.md',
    '@@',
    '- Long release checklist...',
    '- Long architecture history...',
    '- Long debug playbook...',
    '+ For release workflow, read docs/release.md only when release work is requested.',
    '+ For architecture review, read docs/architecture.md only when design tradeoffs matter.',
    '+ For incidents/debugging, read docs/debug-playbook.md only when failures are being diagnosed.'
   ].join('\\n')],
   ['Tool/MCP definition pattern', 'markdown', [
    '## Tool definitions',
    '',
    'Keep always-loaded tool descriptions short:',
    '- what the tool does',
    '- when to use it',
    '- one safety rule',
    '',
    'Move examples, schemas, and long provider docs to:',
    '- docs/tools/<tool-name>.md',
    '- skills/<tool-name>/references/'
   ].join('\\n')]
  ];
 }
 if(title.includes('skill')){
  return [
   ['SKILL.md: compact default contract', 'markdown', [
    '---',
    'name: effort',
    'description: Use for effort sizing when the user asks for complexity, estimate, or implementation effort.',
    '---',
    '',
    'Default output:',
    '1. Short answer: S / M / L / XL',
    '2. Main drivers: 3 bullets max',
    '3. Risks: only material risks',
    '4. Next action: one concrete step',
    '',
    'If deeper analysis is needed, read references/effort-deep-dive.md first.'
   ].join('\\n')],
   ['Diff: progressive disclosure for an expensive skill', 'diff', [
    '--- modules/effort/SKILL.md',
    '+++ modules/effort/SKILL.md',
    '@@',
    '- Include all examples, rubrics, and historical cases in every invocation.',
    '+ Start with the compact sizing rubric below.',
    '+ Read references/effort-examples.md only when the task is ambiguous.',
    '+ Read references/historical-cases.md only when the user asks for calibration.',
    '+ Keep the default answer under 250 words unless the user requests detail.'
   ].join('\\n')]
  ];
 }
 if(title.includes('route')||title.includes('short tasks')||title.includes('difficulty')){
  return [
   ['AGENTS.md / CLAUDE.md: routing policy', 'markdown', [
    '## Model routing policy',
    '',
    'Use a lower-cost capable model for:',
    '- formatting and copy edits',
    '- small deterministic refactors',
    '- simple documentation updates',
    '- straightforward test updates',
    '',
    'Escalate to premium models for:',
    '- ambiguous failures',
    '- architecture or data-model changes',
    '- security-sensitive work',
    '- migrations touching many files',
    '- any task where tests fail after the first attempt'
   ].join('\\n')],
   ['Checklist: safe short-task routing', 'markdown', [
    'Before using lower-cost routing:',
    '- Is the task deterministic?',
    '- Is the expected diff small?',
    '- Can tests or static checks validate it?',
    '',
    'Escalate immediately if:',
    '- requirements are unclear',
    '- generated diff grows unexpectedly',
    '- tests fail',
    '- user impact is high'
   ].join('\\n')]
  ];
 }
 return [
  ['Safe rollout checklist', 'markdown', [
   '1. Apply the recommendation to one workflow first.',
   '2. Compare cost before/after in this dashboard.',
   '3. Check quality: tests, review comments, follow-up corrections.',
   '4. Keep a rollback path if quality drops.'
  ].join('\\n')]
 ];
}
function snippetsHtml(s,i){
 const snippets=suggestionSnippets(s);
 if(!snippets.length)return '';
 return `<details class=howto open><summary>Copy/paste examples</summary>`+
  snippets.map(([label,lang,text],j)=>{const id=`snippet-${i}-${j}`;return `<div class=snippet><div class=snippet-head><span>${esc(label)} · ${esc(lang)}</span><button data-action="copy" data-copy="${esc(id)}">Copy</button></div><pre><code id="${id}">${esc(text)}</code></pre></div>`;}).join('')+
  `</details>`;
}
function suggestionCard(s,i,scale,scopeLabel){
 const d=suggestionDetails(s), impact=+s.impact_usd||0;
 const scopedImpact=impact*scale;
 const options=[['Low-risk pilot',.25,'Apply to one workflow or a few sessions'],['Practical default',.5,'Adopt for common cases with escape hatch'],['Aggressive',.8,'Apply broadly; monitor quality closely']];
 const optHtml=options.map(([label,frac,note])=>`<div class=option data-tip="${esc(note)}"><span class=rec-pill>${esc(label)}</span><b>${money2(scopedImpact*frac)}</b><span class=muted>${Math.round(frac*100)}% of ${esc(scopeLabel)} opportunity</span></div>`).join('');
 return `<details class=rec ${i===0?'open':''}><summary><div class=rec-head>
   <div class=rank>~${money2(scopedImpact)}</div>
   <div><strong>${esc(s.title)}</strong><div class=muted>${esc(s.message)}</div><div class=action>Recommended change: ${esc(s.action)}</div></div>
   <div><span class=rec-pill>${esc(scopeLabel)}</span><div class=tree-meta>${scale===1?'exact source scope':'scaled from spend share'}</div></div>
  </div></summary><div class=rec-body>
   <p class=muted style="margin-top:0"><b style="color:var(--text)">Why this saves money:</b> ${esc(d.why)}</p>
   <p class=muted>${scale===1?'This estimate uses the recommendation source scope directly.':'Selected-window estimate is scaled by this window’s share of metered spend. Some signals, such as fixed startup context and skill attribution, do not yet have exact per-day attribution, so this is directional.'}</p>
   <div class=whatif>${optHtml}</div>
   <div><b>Concrete examples</b><ul class=examples>${d.examples.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>
   ${snippetsHtml(s,i)}
   <div class=tradeoffs>
    <div><b>Autonomy impact</b><span class=muted>${esc(d.autonomy)}</span></div>
    <div><b>Performance impact</b><span class=muted>${esc(d.performance)}</span></div>
    <div><b>Monitor after change</b><span class=muted>${esc(d.monitor)}</span></div>
   </div>
  </div></details>`;
}
function modelDisplay(model){
 const raw=String(model||'unknown'), low=raw.toLowerCase();
 const family=low.includes('opus')?'Opus':low.includes('sonnet')?'Sonnet':low.includes('haiku')?'Haiku':low.includes('gpt')?'GPT':low.includes('codex')?'Codex':null;
 const match=low.match(/(?:opus|sonnet|haiku)-(\\d)-(\\d)/);
 const version=match?`${match[1]}.${match[2]}`:'';
 const claudeRaw=/^claude-/.test(low);
 if(family&&claudeRaw){
  const label=version?`${family} ${version}`:family;
  return {
   label,
   detail:`raw transcript id: ${raw}`,
   tip:`${label} was read from Claude Code transcript field message.model=${raw}. Claude Code logs can expose provider/internal model ids that do not exactly match the visible model-picker labels.`
  };
 }
 return {label:raw,detail:'',tip:`model id: ${raw}`};
}
function toolModelCostTree(d,current){
 const raw=d.tool_model_day_cost||{}, tokRaw=d.tool_model_day_tokens||{}, allDays=datedKeysFrom(raw);
 const activity=(d.activity||{}).by_tool||{};
 const tools=Object.keys({...raw,...activity});
 if(!tools.length)return emptyState('No tool-level usage has been observed yet.');
 const rows=tools.map(tool=>{
  const models=Object.keys(raw[tool]||{}).map(model=>{
   let cost=0,tokens=0,activeDays=0;
   const dayCosts=(raw[tool]||{})[model]||{}, dayTokens=(tokRaw[tool]||{})[model]||{};
   for(const day of Object.keys(dayCosts)){
    if(!dayInWindow(day,current,allDays))continue;
    cost+=+dayCosts[day]||0; tokens+=+dayTokens[day]||0; activeDays+=1;
   }
   return {model,cost,tokens,activeDays};
  }).filter(r=>r.cost||r.tokens).sort((a,b)=>b.cost-a.cost||b.tokens-a.tokens);
  const cost=models.reduce((a,r)=>a+r.cost,0), tokens=models.reduce((a,r)=>a+r.tokens,0);
  const act=activity[tool]||{};
  return {tool,cost,tokens,models,activity:act};
 }).sort((a,b)=>b.cost-a.cost||b.tokens-a.tokens||a.tool.localeCompare(b.tool));
 const max=Math.max(...rows.map(r=>r.cost),1);
 const total=rows.reduce((a,r)=>a+r.cost,0);
 let h=`<section style="margin:24px 0"><h2>Total cost tree <span class=muted style="text-transform:none;font-weight:400">— tool → model, selected window</span></h2>
   <p class=muted style="margin-top:0">Click a metered tool row to expand the models that drove spend. Model rows use raw transcript IDs as metadata because Claude Code logs provider/internal model IDs, which may not exactly match the visible model-picker labels. Cursor and Antigravity are shown as <b>Unpriced</b> when only activity coverage exists; this means their local logs did not expose exact token/model counters, not that the work was free.</p>
   <div class=cost-tree>`;
 for(const r of rows){
  const hasMetered=r.models.length>0, pct=hasMetered&&total?Math.round(100*r.cost/total):null, w=hasMetered?Math.max(0,100*r.cost/max):0;
  const activityNote=r.activity&&r.activity.sessions?`${r.activity.sessions} activity sessions · ${r.activity.projects||0} projects`:'metered token counters';
  const costLabel=hasMetered?money2(r.cost):'<span class=muted>Unpriced</span>';
  const shareLabel=hasMetered?`${pct}%`:'activity-only';
  const tip=hasMetered
    ? `${r.tool}: ${money2(r.cost)} · ${fmt(r.tokens)} tokens · ${activityNote}`
    : `${r.tool}: activity detected, but token/model counters are unavailable in local logs, so cost is not calculated`;
  h+=`<details class=tree-node ${hasMetered?'open':''}><summary>
      <div class=tree-row data-tip="${esc(tip)}">
       <div><span class=tree-name>${toolBadge(r.tool)} ${esc(r.tool)}</span><div class=tree-meta>${esc(activityNote)}</div></div>
       <div class=tree-bar><span style="width:${w}%"></span></div>
       <div class=money>${costLabel}</div>
       <div class=tree-meta>${shareLabel}</div>
      </div></summary><div class=tree-children>`;
  const modelMax=Math.max(...r.models.map(m=>m.cost),1);
  if(r.models.length){
   for(const m of r.models){
    const mw=Math.max(0,100*m.cost/modelMax), share=r.cost?Math.round(100*m.cost/r.cost):0;
    const md=modelDisplay(m.model);
    h+=`<div class=tree-row data-tip="${esc(md.tip)} · ${fmt(m.tokens)} tokens · ${m.activeDays} active days">
       <div><span class=tree-name>${esc(md.label)}</span><div class=tree-meta>${esc(md.detail||`${fmt(m.tokens)} tokens`)}</div></div>
       <div class=tree-bar><span style="width:${mw}%"></span></div>
       <div class=money>${money2(m.cost)}</div>
       <div class=tree-meta>${share}%</div>
      </div>`;
   }
  }else{
   h+=`<div class=tree-empty>Activity was detected for this tool, but token/model counters are not available from its local logs yet.</div>`;
  }
  h+='</div></details>';
 }
 return h+'</div></section>';
}
function recommendationPanel(d){
 const current=activeWindow();
 const t=sumWindowTokens(d,current), total=(t.input||0)+(t.output||0)+(t.cache_read||0)+(t.cache_write||0);
 const cachePct=total?Math.round(100*((t.cache_read||0)+(t.cache_write||0))/total):0;
 const b=selectedBloat(d,current), bloatPct=b.median?((b.median/200000)*100).toFixed(1):'0.0';
 const pricingCoverage=(d.data_quality||{}).pricing_coverage_pct||0;
 const selectedSpend=sumWindowDaily(d.by_day_model_cost||{},current);
 const selectedLabel=windowDef(current)[1];
 const lifetimeSpend=(d.period_cost||{}).lifetime||d.total_cost||selectedSpend||0;
 const suggestionScale=current==='all'||!lifetimeSpend?1:Math.min(1,selectedSpend/lifetimeSpend);
 const spendRows=projectRowsForWindow(d,current).slice(0,5);
 const rows=spendRows.map(x=>`<tr><td>${esc(x.label)}</td><td class=money>${money2(x.window_cost)}</td><td>${toolBadge(x.tool)}</td><td class=muted>${x.window_value&&x.window_value.kind==='git'?`${x.window_value.commits||0} commits · ${x.window_value.prs||0} PRs`:'local activity'}</td></tr>`).join('');
 let h=`<section><h2>Recommendations to optimize tokens</h2>
   <p class=muted style="margin-top:0">Start here. This panel separates token-optimization signals from delivery and GitHub analytics.</p>
   <div class=cards>
     <div class=card tabindex=0 data-tip="Sum of metered token usage inside the selected dashboard window. Cost is computed from local provider token counters by model using the bundled pricing catalog; it is a list-price estimate, not your provider invoice."><div class=lbl>${esc(selectedLabel)} spend</div><div class=val>${money2(selectedSpend)}</div><div class=note>list-price estimate from metered sources</div></div>
     <div class=card tabindex=0 data-tip="(cache_read + cache_write) divided by all observed tokens. High cache share means much of the context was served through provider prompt caching; it does not mean the work was free."><div class=lbl>Cache share</div><div class=val>${cachePct}%</div><div class=note>higher usually means less repeated prompt cost</div></div>
     <div class=card tabindex=0 data-tip="Median fixed startup context observed in Claude Code sessions, divided by a 200K reference context window. This includes system/tool/skill/hook descriptions re-sent at session start."><div class=lbl>Startup bloat</div><div class=val>${bloatPct}%</div><div class=note>median fixed context vs 200K window</div></div>
     <div class=card tabindex=0 data-tip="Percent of observed tokens whose model id matched a named rate in the local pricing catalog. 100% means no fallback rate was used; it does not prove the dollar estimate is invoice-exact because subscriptions, credits, regional pricing, and provider-side billing adjustments are not reconstructed."><div class=lbl>Pricing coverage</div><div class=val>${pricingCoverage}%</div><div class=note>${d.fallback_pct?`${d.fallback_pct}% fallback pricing`:'all observed models matched named rates'}</div></div>
  </div>
   <p class=muted style="margin:0">Accuracy note: token counts come from local metered logs where available. Dollar values are deterministic list-price estimates from those counters; “100%” coverage means model-pricing match coverage, not invoice certainty.</p>
 </section>`;
 h+=toolModelCostTree(d,current);
 h+=suggestionsCard(d.suggestions||[],suggestionScale,selectedLabel)||'<section style="margin:24px 0"><h2>Recommended next actions</h2><p class=muted>No automated optimization suggestions crossed the current threshold for the selected window. Check the top spend directories below and the Diagnostics tab for startup bloat.</p></section>';
 h+=`<section style="margin:24px 0"><h2>Highest token spend areas <span class=muted style="text-transform:none;font-weight:400">— ${esc(selectedLabel)}</span></h2><p class=muted style="margin-top:0">Spend and observed git outcomes follow the global selected time window.</p><div class=table-wrap><table><tr><th>directory</th><th>selected-window spend</th><th>tool</th><th>observed outcome</th></tr>${rows||'<tr><td colspan=4 class=muted>No directory spend found in the selected window.</td></tr>'}</table></div></section>`;
 return h;
}
function stackedByModel(d){
 const raw=d.by_day_model_cost||{}, current=activeWindow(), allDays=Object.keys(raw).sort(), days=allDays.filter(day=>dayInWindow(day,current,allDays));
 if(!days.length) return emptyState('No dated cost yet for the selected window.');
 const modelTotals={}; for(const day of days)for(const[m,c]of Object.entries(raw[day]))modelTotals[m]=(modelTotals[m]||0)+c;
 const models=Object.keys(modelTotals).sort((a,b)=>modelTotals[b]-modelTotals[a]).slice(0,5), hasOther=Object.keys(modelTotals).length>5;
 const series=[...models,...(hasOther?['Other']:[])], colors=['var(--m1)','var(--m2)','var(--m3)','var(--m4)','var(--m5)','var(--m6)'];
 const values=days.map(day=>series.map(m=>m==='Other'?Object.entries(raw[day]).filter(([x])=>!models.includes(x)).reduce((a,[,v])=>a+v,0):(raw[day][m]||0)));
 const totals=values.map(v=>v.reduce((a,x)=>a+x,0)), max=Math.max(...totals,1), W=720,H=230,PL=48,PB=36,PR=18,PT=20;
 const slot=(W-PL-PR)/days.length,bw=Math.max(2,slot*.72),Y=v=>(H-PB-PT)*v/max; let bars='';
 values.forEach((vals,i)=>{let used=0; vals.forEach((v,j)=>{const h=Y(v),x=PL+i*slot+(slot-bw)/2,y=H-PB-used-h;bars+=`<rect x="${x}" y="${y}" width="${bw}" height="${Math.max(0,h)}" fill="${colors[j]}" data-tip="${esc(days[i])} · ${esc(series[j])}: $${v.toFixed(2)}" tabindex="0"/>`;used+=h;});});
 const ticks=`<text x="${PL}" y="${H-8}" fill="var(--muted)" font-size="11">${esc(days[0].slice(5))}</text><text x="${W-PR}" y="${H-8}" fill="var(--muted)" font-size="11" text-anchor="end">${esc(days[days.length-1].slice(5))}</text><text x="${PL-5}" y="${PT+3}" fill="var(--muted)" font-size="11" text-anchor="end">$${max.toFixed(0)}</text>`;
 const leg=series.map((m,i)=>{const md=m==='Other'?{label:'Other',tip:'Other lower-cost models'}:modelDisplay(m);return `<span data-tip="${esc(md.tip)}"><i class=dot style="background:${colors[i]}"></i>${esc(md.label)}</span>`;}).join('');
 return svgEl(W,H,bars+ticks,'Daily estimated list-price cost stacked by model for the selected time window')+`<div class=legend>${leg}</div>`;
}
function selectedModelTotals(d,current){
 const raw=d.by_day_model_tokens||{}, days=Object.keys(raw).sort(), totals={};
 for(const day of days){if(!dayInWindow(day,current,days))continue;for(const[model,tokens]of Object.entries(raw[day]||{})){
  const row=totals[model]||(totals[model]={input:0,output:0,cache_read:0,cache_write:0});
  for(const key of Object.keys(row))row[key]+=(+(tokens||{})[key]||0);
 }}
 return Object.entries(totals).sort((a,b)=>-(a[1].input+a[1].output+a[1].cache_read+a[1].cache_write)+(b[1].input+b[1].output+b[1].cache_read+b[1].cache_write));
}
function selectedMainSubagent(d,current){
 const raw=d.main_subagent_by_day||{}, days=Object.keys(raw).sort(), out={main:0,subagent:0};
 for(const day of days)if(dayInWindow(day,current,days))for(const role of Object.keys(out)){const bucket=(raw[day]||{})[role]||{};for(const value of Object.values(bucket))out[role]+=+value||0;}
 return out;
}
function toolBadge(t){
 const m={'claude-code':'CC','codex':'CX','cursor':'CU','antigravity':'AG'};
 const raw=String(t||'unknown');
 const parts=raw.split('+').filter(Boolean);
 const label=parts.map(p=>m[p]||p.replace(/[^A-Za-z0-9]/g,'').slice(0,2).toUpperCase()||'?').join(' + ');
 return `<span class=badge title="${esc(raw)}">${esc(label)}</span>`;
}
function activityTable(activity,current,d){const rows=(activity||{}).sessions||[], byTool=(activity||{}).by_tool||{}, days=meteredDays(d);
 const scoped=rows.filter(row=>!row.day||dayInWindow(row.day,current,days)); if(!scoped.length)return '';
 const scopedByTool={}; for(const row of scoped){const v=scopedByTool[row.tool]||(scopedByTool[row.tool]={sessions:0,messages:0,artifacts:0,projects:new Set()});v.sessions++;v.messages+=row.messages||0;v.artifacts+=row.artifacts||0;v.projects.add(row.project||'unknown');}
 const summary=Object.entries(scopedByTool).map(([tool,v])=>`${tool}: ${v.sessions} sessions · ${v.projects.size} projects${v.messages?' · '+v.messages+' messages':''}${v.artifacts?' · '+v.artifacts+' artifacts':''}`).join(' | ');
 let h=`<section style="margin:24px 0"><h2>Activity coverage without token counters</h2><p class=muted>${esc(summary)}. These sources improve project/session coverage but never enter token cost.</p><div class=table-wrap><table><tr><th>date</th><th>tool</th><th>project</th><th>session</th><th>messages</th><th>artifacts</th></tr>`;
 for(const r of scoped.slice(0,30)){h+=`<tr><td>${esc(r.day||'—')}</td><td>${toolBadge(r.tool)}</td><td>${esc(r.project||'unmapped')}</td><td class=muted>${esc((r.session_id||'').slice(0,8))}</td><td>${r.messages==null?'—':r.messages}</td><td>${r.artifacts==null?'—':r.artifacts}</td></tr>`;}
 return h+'</table></div></section>';}
function dirsTable(d,current){
 const rows=projectRowsForWindow(d,current,true);
 let h=`<h2>All directories <span class=muted style="text-transform:none;font-weight:400">— selected-window spend × delivery evidence</span></h2>`;
 h+=`<details class=howto><summary>How this is calculated</summary>
   <ul>
     <li><b style="color:var(--cost)">Cost</b> is computed from exact local token counters per model using standard API list prices. It is not a subscription invoice; unknown models are visibly marked through pricing coverage.</li>
     <li><b style="color:var(--value)">Delivery</b> uses each directory's available source window, shown in the table. Git repos use commits, deduplicated merged PRs, releases, and changed files. Non-repos use modified-file counts and are explicitly estimates.</li>
     <li><b>Coverage</b> includes Claude Code and Codex transcripts plus agentic directories discovered by marker files. A dash means no supported local token source, never zero spend.</li>
     <li><b>Business value</b> is not inferred from engineering activity. PRs, commits, files, and churn are delivery evidence—not ROI.</li>
   </ul></details>`;
 h+=`<div class=table-wrap><table><tr><th>directory</th><th>tool</th><th>selected-window $</th><th>observable delivery</th><th>outcome scope</th><th>+ / -</th><th>delivery unit cost</th></tr>`;
 for(const d of rows){
  const v=d.window_value||d.value||{}; const removed=!d.dir;
  const shipped = removed ? '<span class=muted>(removed)</span>'
    : v.kind==='git' ? esc(`${v.commits||0} commits${v.prs?'·'+v.prs+' PRs':''}`)
    : v.kind==='fs' ? esc(`${v.fs_files} files`) : '—';
  const lbl = removed
    ? `<span class=muted title="directory no longer on disk — value can't be computed">${esc(d.label)}</span>`
    : esc(d.label);
  h+=`<tr><td>${lbl}</td><td>${toolBadge(d.tool)}</td>`+
     `<td class=money>${money2(d.window_cost)}</td>`+
     `<td style="color:var(--value)">${shipped}</td>`+
     `<td class=muted>${d.window&&d.window.start?`${esc(d.window.start)} → ${esc(d.window.end||d.window.start)}`:'discovery snapshot'}</td>`+
     `<td class=n>${v.kind==='git'?`+${fmt(v.insertions||0)} / -${fmt(v.deletions||0)}`:'—'}</td>`+
     `<td class=muted>${d.window_cost!=null&&v.prs?'$'+(d.window_cost/v.prs).toFixed(2)+'/PR':d.window_cost!=null&&v.commits?'$'+(d.window_cost/v.commits).toFixed(2)+'/commit':'—'}</td></tr>`;
 }
 return h+(rows.length?'':'<tr><td colspan=7 class=muted>No directory spend found in the selected window.</td></tr>')+'</table></div>';
}
let pollTimer;
function schedule(d){clearTimeout(pollTimer);pollTimer=setTimeout(load,(d.loading||d.refreshing)?750:30000);}
async function load(){
 try{await SESSION_READY;const d=await (await fetch('/api/data')).json();render(d);schedule(d);}
 catch(err){document.getElementById('meta').textContent='dashboard unavailable · retrying';pollTimer=setTimeout(load,2000);}
}
async function refresh(){
 clearTimeout(pollTimer);
 await SESSION_READY;
 const response=await fetch('/api/refresh',PRISM_SECURE?{method:'POST',headers:{'Content-Type':'application/json','X-Prism-CSRF':PRISM_CSRF},body:'{}'}:{});
 if(!response.ok){document.getElementById('meta').textContent='Reopen with 100xprism tokens to authorize collection.';return;}
 const d=await response.json();render(d);schedule(d);
}
async function fetchGithub(){
 await SESSION_READY;
 const response=await fetch('/api/github',{method:'POST',headers:{'Content-Type':'application/json','X-Prism-CSRF':PRISM_CSRF},body:'{}'});
 if(response.ok){const d=await response.json();render(d);schedule(d);}
 else document.getElementById('meta').textContent='GitHub request unavailable. Check local authorization and github.enabled.';
}
function coveragePanel(d){
 const live=d.live_status;if(!live)return '';
 const locations=live.locations||live.source_locations||[];
 const rows=locations.map(row=>`<tr><td>${esc(row.tool||'')}</td><td>${esc(row.root_path||'')}</td><td>${esc(row.status||'never_scanned')}</td><td>${row.present_file_count||0}/${row.file_count||0}</td><td>${esc(row.last_full_scan_at?new Date(row.last_full_scan_at*1000).toLocaleString():'never')}</td><td>${esc(row.last_error||'')}</td></tr>`).join('');
 const run=live.run||live.latest_run||live.scan_run||{};
 const state=locations.some(row=>row.status==='scanning')?'scanning':locations.some(row=>row.status==='degraded')?'degraded':locations.some(row=>row.status==='never_scanned')?'not yet scanned':'incrementally current';
 const integrity=live.integrity_verified_at?new Date(live.integrity_verified_at*1000).toLocaleString():'verification pending';
 return `<details class=howto><summary>Local collection coverage · ${esc(state)}</summary><p>Complete snapshot totals remain visible during collection. Historical integrity is checked separately: ${esc(integrity)}.</p><p>${fmt(run.files_parsed||0)} changed files · ${fmt(run.bytes_parsed||0)} bytes parsed in latest run</p><div class=table-wrap><table><tr><th>tool</th><th>source</th><th>state</th><th>present / recorded</th><th>last full scan</th><th>error</th></tr>${rows}</table></div></details>`;
}
function localSearchPanel(d){
 if(!PRISM_SECURE)return '';
 const live=d.live_status||{},enabled=live.content_search_enabled||live.search_enabled||(live.content_search||{}).enabled;
 if(!enabled)return '<p class=muted>Local prompt and assistant-prose search is off. Enable with <code>100xprism tokens --enable-content-search</code>.</p>';
 const coverage=live.content_backfill||{};
 return `<section><h2>Search local prompts and prose</h2><p class=muted>Tool results and file contents are excluded. Backfill: ${fmt(coverage.complete||0)} / ${fmt(coverage.total||0)} files complete.</p><form id=localSearchForm><input name=q aria-label="Search query" placeholder="Keyword, phrase, prefix or Boolean query" required maxlength=512><input name=project aria-label="Repository" placeholder="Repository"><select name=tool aria-label="Tool"><option value="">All tools</option><option>claude-code</option><option>codex</option><option>cursor</option><option>pi</option></select><select name=role aria-label="Role"><option value="">All roles</option><option>user</option><option>assistant</option></select><input type=date name=date_from aria-label="From date"><input type=date name=date_to aria-label="To date"><button>Search</button></form><div id=localSearchResults></div></section>`;
}
async function searchLocal(form){
 await SESSION_READY;const query=new URLSearchParams();for(const[key,value]of new FormData(form))if(value)query.set(key,value);
 const response=await fetch('/api/search?'+query.toString()),target=document.getElementById('localSearchResults');
 if(!response.ok){target.textContent='Search unavailable. Reopen using 100xprism tokens, or wait for backfill.';return;}
 const data=await response.json();target.innerHTML=(data.results||[]).map(row=>`<article><h3>${esc(row.project||row.session_key||row.session_id||'session')} · ${esc(row.tool||'')} · ${esc(row.role||'')}</h3><p>${(row.segments||row.excerpt||[]).map(part=>part.matched?'<mark>'+esc(part.text)+'</mark>':esc(part.text)).join('')}</p></article>`).join('')||'<p>No matching prose.</p>';
}
function money(c){return c==null?'<span class=muted>—</span>':'$'+(+c).toLocaleString(undefined,{maximumFractionDigits:0});}
function money2(c){return c==null?'<span class=muted>—</span>':'$'+(+c).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});}
function qualityStrip(d,current){const q=d.data_quality||{},v=q.value_sources||{},w=q.window||{},days=meteredDays(d),u={},a={};
 for(const[day,tools]of Object.entries(q.usage_sources_by_day||{}))if(dayInWindow(day,current,days))for(const[tool,n]of Object.entries(tools))u[tool]=(u[tool]||0)+n;
 for(const[day,tools]of Object.entries(q.activity_by_day||{}))if(dayInWindow(day,current,days))for(const[tool,n]of Object.entries(tools))a[tool]=(a[tool]||0)+n;
 const usage=Object.entries(u).map(([k,n])=>`${esc(k)} ${n}`).join(' · '), activity=Object.entries(a).map(([k,n])=>`${esc(k)} ${n}`).join(' · '), delivery=selectedDelivery(d,current);
 return `<div class=quality aria-label="Data provenance"><span><b>${usage||'No metered sources'}</b> selected-window records with provider token counters</span><span><b>${activity||'No activity-only sources'}</b> selected-window activity sessions without counters</span><span><b>${q.pricing_coverage_pct||0}%</b> tokens matched to a named price</span><span><b>${delivery.spend.coverage_pct||0}%</b> selected-window spend joined to git outcomes</span><span><b>${v.git||0} git · ${v.fs||0} filesystem</b> available outcome sources</span><span>${w.start?`${esc(w.start)} → ${esc(w.end)}`:'No dated usage'}</span></div>`;}
function scanStatus(d,compact=false){
 const s=d.scan||{},p=d.preview||{},total=+s.total||0,current=+s.current||0;
 const pct=s.stage==='complete'?100:s.stage==='aggregating'?96:total?Math.min(95,Math.round(100*current/total)):4;
 const source=(s.tool||s.stage||'starting').replace('-',' '), done=(s.completed_sources||[]).join(' · ');
 const cards=p.records==null?'':`<div class=cards>
   <div class=card><div class=lbl>Records available</div><div class=val>${fmt(p.records||0)}</div><div class=note>${esc(done||'first source still scanning')}</div></div>
   <div class=card><div class=lbl>Tokens surfaced</div><div class=val>${fmt(p.tokens||0)}</div><div class=note>partial provider counters</div></div>
   <div class=card><div class=lbl>Estimated spend</div><div class=val>${money2(p.estimated_cost||0)}</div><div class=note>partial list-price estimate</div></div>
 </div>`;
 return `<section class=scan-status><h2>${compact?'Refreshing data':'Building dashboard'}</h2>
   <p class=muted>${esc(d.scan_message||d.message||'Scanning local transcripts and repositories…')}</p>
   <div class=meter><b style="width:${pct}%;background:var(--value)"></b><em>${esc(source)} · ${total?`${fmt(current)} / ${fmt(total)}`:'preparing'}</em></div>
   <div class=scan-detail><span>${fmt(s.reparsed||0)} parsed</span><span>${fmt(s.cached||0)} cached</span><span>${(s.completed_sources||[]).length} sources complete</span></div>
   ${compact?'':cards}${d.error?`<p class=muted>Last error: ${esc(d.error)}</p>`:''}</section>`;
}
function render(d){
 const oldForm=document.getElementById('localSearchForm'),oldSearch=oldForm?Object.fromEntries(new FormData(oldForm)):null,oldResults=document.getElementById('localSearchResults')?.innerHTML;
 if(d.loading){
  document.getElementById('meta').textContent=`building local dataset · ${d.generated||''}`;
  document.getElementById('app').innerHTML=scanStatus(d);
  return;
 }
 LAST_DATA=d;
 document.getElementById('meta').textContent=
  `${d.transcripts} source records · ${d.sessions} metered sessions · ${(d.activity||{}).session_count||0} activity-only · updated ${d.generated}`;
 const win=activeWindow(), winLabel=windowDef(win)[1], winSpend=sumWindowDaily(d.by_day_model_cost||{},win);
 const t=sumWindowTokens(d,win), p=d.period_cost||{}, e=d.economics||{};
 const winPurposeCost=sumWindowPurposeCost(d,win);
 const card=(lbl,val,note,col)=>`<div class=card><div class=lbl>${col?`<i class=dot style=background:${col}></i>`:''}${lbl}</div>
   <div class=val>${val}</div><div class=note>${note||''}</div></div>`;
 const W=200000, scopedBloat=selectedBloat(d,win), mw=Math.min(100,100*scopedBloat.median/W);
 const current=activeTab();
 const de=selectedDelivery(d,win), deSpend=de.spend||{}, deOutcomes=de.outcomes||{}, deUnit=de.delivery_unit_cost||{};
 const overview=`<div class=hero>
   <h2>Token cost → observable delivery → business value</h2>
   <p>This dashboard measures the first two layers separately. Token counters establish estimated cost; Git establishes delivery evidence. Business or human value remains explicitly unmeasured until an outcome source is connected.</p>
   ${outcomeFlow(d,win)}
   <div class=chips><span class=chip>no cloud upload</span><span class=chip>list-price estimate</span><span class=chip>observable delivery</span><span class=chip>no synthetic value score</span></div>
  </div>
 ${workMix(d,win)}
 <div class=cards>
   ${card(`${esc(winLabel)} spend`,money2(winSpend),'selected-window list-price spend','var(--cost)')}
   ${card('selected tokens',fmt((t.input||0)+(t.output||0)+(t.cache_read||0)+(t.cache_write||0)),'provider counters in selected window','var(--in)')}
   ${card('attributed spend',money2(deSpend.attributed),`${deSpend.coverage_pct||0}% of selected-window spend joined to git`,'var(--value)')}
   ${card('delivery cost / PR',money2(deUnit.per_pr),`${deOutcomes.prs||0} observed PRs · attributed spend only`,'var(--value)')}
   ${card('delivery cost / commit',money2(deUnit.per_commit),`${deOutcomes.commits||0} commits · not business ROI`,'var(--value)')}
   ${card('business value','Not measured','connect an outcome source before claiming ROI','var(--muted)')}
  </div>${qualityStrip(d,win)}`;
 const budget=`<section style="margin-bottom:24px"><h2>Budget</h2>
   ${d.budget.daily.limit==null&&d.budget.weekly.limit==null
     ? '<p class=muted>No budget configured — add "budget" to ~/.100xprism/config.json.</p>'
     : budgetBar(d.budget.daily,'today')+budgetBar(d.budget.weekly,'last 7 days')}
   <p class=muted style="margin-top:8px">Pricing catalog checked ${esc((d.pricing||{}).as_of||'unknown')}. ${d.fallback_pct?`${d.fallback_pct}% of tokens use conservative fallback pricing.`:'All model tokens matched a named rate.'} Subscription plans and provider credits are not reconstructed.</p>
 </section>`;
 const delivery=`<div class=cards2>
   <section><h2>Directory delivery scoreboard <span class=muted style="text-transform:none;font-weight:400">— selected-window spend, delivery, churn</span></h2>${deliveryScoreboard(d,win)}</section>
   <section><h2>Cost by directory <span class=muted style="text-transform:none;font-weight:400">— ${esc(winLabel)}</span></h2>${costByDir(d,win)}</section>
   <section class=wide><h2>Daily list-price cost by model</h2>${stackedByModel(d)}</section>
   <section><h2>Dollar spend by token purpose <span class=muted style="text-transform:none;font-weight:400">— ${esc(winLabel)}</span></h2>${donut(winPurposeCost)}${legend()}</section>
   <section><h2>Token volume by purpose <span class=muted style="text-transform:none;font-weight:400">— ${esc(winLabel)}</span></h2>${purposeSplit(t)}${legend()}</section>
 </div>${dirsTable(d,win)}`;
 const sessions=localSearchPanel(d)+handoffTable(d.handoff_runs||[],win,d)+activityTable(d.activity,win,d)+
  `<h2>Sessions <span class=muted style="text-transform:none;font-weight:400">— ${esc(winLabel)} · top 20 by cost</span></h2>${sessionsTable(d.by_session||[],win,d)}<h2>Largest context growth</h2>${contextGrowthTable(d.by_session||[],win,d)}${cacheModelTable(d)}`;
 const scopedMainSubagent=selectedMainSubagent(d,win);
 const skills=`<h2>By skill <span class=muted style="text-transform:none;font-weight:400">— ${esc(winLabel)} · exact from Claude Code's native attribution, attr. from command-marker segmentation</span></h2>${skillsTable(d.by_skill||[],win,d)}
   <h2>Main vs subagent <span class=muted style="text-transform:none;font-weight:400">— ${esc(winLabel)}</span></h2><p>main ${money2(scopedMainSubagent.main)} · subagent ${money2(scopedMainSubagent.subagent)}</p>`;
 let diagnostics=`<h2>Startup bloat — fixed context re-sent every turn</h2>
   <div class=meter><b style="width:${mw}%;background:${mw>30?'var(--cw)':'var(--cr)'}"></b>
   <em>median ${fmt(scopedBloat.median)} of 200K window (${(scopedBloat.median/W*100).toFixed(1)}%)</em></div>
   <p class=muted style=margin-top:8px>avg ${fmt(scopedBloat.avg)} · ${esc(winLabel)}. This is system prompt + tool/skill/agent descriptions + SessionStart injections, read on every turn.</p>`;
 const scopedComposition=selectedComposition(d,win);
 if(scopedComposition&&scopedComposition.length){
  const CC=['#58a6ff','#f778ba','#3fb950','#d29922','#a371f7','#ff7b72','#8b949e'];
  const ct=scopedComposition.reduce((a,r)=>a+r[1],0)||1;
  let segs='',rows='';
  scopedComposition.forEach((r,i)=>{const col=CC[i%CC.length];
   segs+=`<span style="width:${100*r[1]/ct}%;background:${col}"></span>`;
   rows+=`<tr><td><i class=dot style=background:${col}></i>${esc(r[0])}</td><td>${fmt(r[1])}</td><td>${r[2]}%</td></tr>`;});
  diagnostics+=`<h2>Content composition <span class=muted style="text-transform:none;font-weight:400">— estimate, char-based (not billed tokens)</span></h2>
   <div class=bar style="height:14px;margin-bottom:12px">${segs}</div>
   <table><tr><th>content type</th><th>est. tokens</th><th>share</th></tr>${rows}</table>
   <p class=muted style=margin-top:8px>The API bills per-turn aggregates, so this approximates where your conversation <em>text volume</em> goes (chars÷4): code written, files read, command output/logs, model prose, prompts. Directional, not exact.</p>`;
 }
 const scopedModels=selectedModelTotals(d,win);
 diagnostics+=`<h2>By model <span class=muted style="text-transform:none;font-weight:400">— ${esc(winLabel)}</span></h2><table><tr><th>model</th><th>mix</th><th>cache read</th><th>output</th></tr>`;
 for(const[name,v]of scopedModels){diagnostics+=`<tr><td>${esc(name)}</td><td>${bar(v)}</td>
   <td>${fmt(v.cache_read)}</td><td>${fmt(v.output)}</td></tr>`;}
 diagnostics+='</table>';
 const days=diagnosticDays(d,win);
 diagnostics+=`<h2>${esc(winLabel)} active days <span class=muted>(${days.length})</span></h2><div class=table-wrap><table><tr><th>day</th><th>mix</th><th>read</th><th>out</th></tr>`;
 for(const[day,v]of days){diagnostics+=`<tr><td>${esc(day)}</td><td>${bar(v)}</td>
   <td>${fmt(v.cache_read)}</td><td>${fmt(v.output)}</td></tr>`;}
 diagnostics+='</table></div>';
 const cache=d.cache||{},cacheView=d.cache?`<div class=cards><div class=card><div class=lbl>Lifetime cached context</div><div class=val>${fmt(cache.cache_read||0)}</div><div class=note>${((cache.cache_hit_ratio||0)*100).toFixed(1)}% cache hit ratio · ${((cache.cached_context_share||0)*100).toFixed(1)}% cached-context share</div></div><div class=card><div class=lbl>Estimated lifetime cache savings</div><div class=val>${money2(cache.estimated_savings)}</div><div class=note>Cache-read priced at ordinary model input rates; catalog estimate</div></div></div>`:'';
 const h=`<div class=dashboard-shell>${sideTabs(current)}<main>
   ${windowButtons(win,d)}${historyPanel(d)}
   ${pane('recommendations',recommendationPanel(d),current)}
   ${pane('overview',overview+budget,current)}
   ${pane('delivery',delivery,current)}
   ${pane('github',(PRISM_SECURE?'<p class=muted>Fetching insights uses gh and accesses GitHub when github.enabled is true.</p><button data-action=github>Fetch GitHub insights</button>':'')+githubPanel(d.github,win,d),current)}
   ${pane('sessions',cacheView+sessions,current)}
   ${pane('skills',skills,current)}
   ${pane('diagnostics',diagnostics,current)}
 </main></div>`;

 document.getElementById('app').innerHTML=coveragePanel(d)+(d.refreshing?scanStatus(d,true):'')+h;
 const newForm=document.getElementById('localSearchForm');if(oldSearch&&newForm){for(const[key,value]of Object.entries(oldSearch))if(newForm.elements.namedItem(key))newForm.elements.namedItem(key).value=value;document.getElementById('localSearchResults').innerHTML=oldResults||'';}
}
document.addEventListener('click',event=>{const b=event.target.closest('[data-action]');if(!b)return;switch(b.dataset.action){case'theme':toggleTheme();break;case'refresh':refresh();break;case'github':fetchGithub();break;case'window':setWindow(b.dataset.window);break;case'tab':showTab(b.dataset.tab);break;case'copy':copyText(b.dataset.copy);break;}});
document.addEventListener('submit',event=>{if(event.target.id==='localSearchForm'){event.preventDefault();searchLocal(event.target);}});
load();
document.addEventListener('mousemove',e=>{const el=e.target.closest('[data-tip]');const tip=document.getElementById('tip');
  if(el){tip.textContent=el.getAttribute('data-tip');tip.style.display='block';
    let x=e.clientX+12,y=e.clientY+12;tip.style.left=Math.min(x,innerWidth-tip.offsetWidth-8)+'px';tip.style.top=y+'px';}
  else{tip.style.display='none';}});
document.addEventListener('focusin',e=>{const el=e.target.closest&&e.target.closest('[data-tip]');const tip=document.getElementById('tip');
  if(el){const r=el.getBoundingClientRect();tip.textContent=el.getAttribute('data-tip');tip.style.display='block';
    tip.style.left=r.left+'px';tip.style.top=(r.bottom+6)+'px';}});
document.addEventListener('focusout',()=>{document.getElementById('tip').style.display='none';});
</script></body></html>"""


_build_lock = threading.Lock()
_build_state_lock = threading.Lock()
_build_state = {
    "building": False, "error": None, "stage": "idle", "tool": None,
    "current": 0, "total": 0, "reparsed": 0, "cached": 0,
    "completed_sources": [], "preview": None, "message": None,
}


def _scan_state():
    with _build_state_lock:
        return {
            key: (list(value) if isinstance(value, list) else value)
            for key, value in _build_state.items()
            if key not in ("building", "error", "preview", "message")
        }


def _publish_progress(update):
    with _build_state_lock:
        _build_state.update(update)


def _rebuild(verbose=False):
    with _build_state_lock:
        if not _build_state["building"]:
            _build_state.update({
                "building": True, "error": None, "stage": "discovering", "tool": None,
                "current": 0, "total": 0, "reparsed": 0, "cached": 0,
                "completed_sources": [], "preview": None,
                "message": "Discovering local repositories",
            })
    with _build_lock:
        try:
            Handler.data = build(verbose=verbose, progress=_publish_progress)
            threading.Thread(target=lambda: _summaries.backfill(), daemon=True).start()
            return Handler.data
        except Exception as exc:
            with _build_state_lock:
                _build_state["error"] = str(exc)
            raise
        finally:
            with _build_state_lock:
                _build_state["building"] = False
                if _build_state["error"] is None:
                    _build_state["stage"] = "complete"
                    _build_state["message"] = "Dashboard ready"


def _start_rebuild(verbose=False):
    with _build_state_lock:
        if _build_state["building"]:
            return False
        _build_state.update({
            "building": True, "error": None, "stage": "discovering", "tool": None,
            "current": 0, "total": 0, "reparsed": 0, "cached": 0,
            "completed_sources": [], "preview": None,
            "message": "Discovering local repositories",
        })
    threading.Thread(target=lambda: _safe_rebuild(verbose=verbose), daemon=True).start()
    return True


def _safe_rebuild(verbose=False):
    try:
        _rebuild(verbose=verbose)
    except Exception as exc:
        print(f"dashboard rebuild failed: {exc}", file=sys.stderr)


def _client_data(data):
    """Token data for the browser."""
    payload = dict(data)
    with _build_state_lock:
        building = _build_state["building"]
        preview = _build_state.get("preview")
        message = _build_state.get("message")
        error = _build_state.get("error")
    if building:
        payload.update({
            "refreshing": True,
            "scan": _scan_state(),
            "preview": preview,
            "scan_message": message,
            "scan_error": error,
        })
    return payload


def _loading_data():
    with _build_state_lock:
        message = _build_state.get("message")
        preview = _build_state.get("preview")
        error = _build_state.get("error")
    return {
        "loading": True,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": message or "Dashboard is scanning local transcripts and repositories.",
        "scan": _scan_state(),
        "preview": preview,
        "error": error,
    }


class Handler(BaseHTTPRequestHandler):
    data = None

    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json", status=200):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        try:
            if self.path.startswith("/api/refresh"):
                _start_rebuild(verbose=False)
                response = (_loading_data() if Handler.data is None
                            else _client_data(Handler.data))
                self._send(json.dumps(response))
            elif self.path.startswith("/api/data"):
                if Handler.data is None:
                    _start_rebuild(verbose=False)
                    self._send(json.dumps(_loading_data()))
                else:
                    self._send(json.dumps(_client_data(Handler.data)))
            else:
                self._send(PAGE, "text/html; charset=utf-8")
        except Exception as exc:
            print(f"dashboard request failed: {exc}", file=sys.stderr)
            self._send(json.dumps({"error": "dashboard refresh failed"}), status=500)


def _token_summary() -> str | None:
    """Return a short cached-token summary string (+ budget glyph), or None."""
    cc_cache = claude_code.load_cache()
    cx_cache = codex.load_cache()
    if not cc_cache and not cx_cache:
        return None
    tot = _empty()
    today_str = datetime.now().strftime("%Y-%m-%d")
    week_start = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
    today_by_model, week_models = defaultdict(_empty), defaultdict(_empty)
    for cache in (cc_cache, cx_cache):
        for s in cache.values():
            t = s.get("totals", {})
            _add(tot, t.get("input", 0), t.get("output", 0), t.get("cache_read", 0), t.get("cache_write", 0))
            for day, models in s.get("by_day_model", {}).items():
                for model, d in models.items():
                    if day == today_str:
                        _add(today_by_model[model], d["input"], d["output"], d["cache_read"], d["cache_write"])
                    if day >= week_start:
                        _add(week_models[model], d["input"], d["output"], d["cache_read"], d["cache_write"])
    if not any(tot.values()):
        return None
    by_model_total = _bucket_by_model(cc_cache, cx_cache)
    cost, _ = pricing.cost_by_model(by_model_total)
    today_cost, _ = pricing.cost_by_model(today_by_model)
    week_cost, _ = pricing.cost_by_model(week_models)
    budget = _budget.budget_summary(today_cost, week_cost)
    suffix = _budget.oneline_suffix(budget)
    line = f"{fmt(tot['output'])} out · {fmt(tot['cache_read'])} ctx · ~${cost:,.0f}"
    if suffix:
        line += f" · {suffix}"
    return line


def _bucket_by_model(cc_cache, cx_cache):
    agg = defaultdict(_empty)
    for cache in (cc_cache, cx_cache):
        for s in cache.values():
            for model, d in s.get("by_model", {}).items():
                _add(agg[model], d["input"], d["output"], d["cache_read"], d["cache_write"])
    return dict(agg)


def _oneline():
    """Fast cache-only summary line for shell startup. Silent if no cache yet."""
    s = _token_summary()
    if s:
        print(f"100xPrism tokens (as of last scan): {s} · run `100x-tokens` for the dashboard")


def _port_in_use(port):
    """True if something is already listening on 127.0.0.1:port (a running dash)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _command_for_pid(pid):
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="], capture_output=True,
            text=True, timeout=2, check=False)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _owned_dashboard_pids(port):
    """Return only processes that can be verified as this dashboard script."""
    candidates = set()
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            record = json.load(f)
        if int(record.get("port", -1)) == port:
            candidates.add(int(record["pid"]))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    # Compatibility with dashboard processes started before the PID file existed.
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=2, check=False)
        candidates.update(int(line) for line in result.stdout.splitlines() if line.isdigit())
    except (OSError, subprocess.SubprocessError):
        pass
    script = os.path.realpath(__file__)
    owned = []
    for pid in candidates:
        if pid != os.getpid() and script in _command_for_pid(pid):
            owned.append(pid)
    return owned


def _stop_previous_dashboard(port, timeout=3.0):
    """Stop a prior owned UI/daemon, without touching an unrelated port owner."""
    stopped = []
    for pid in _owned_dashboard_pids(port):
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except (OSError, ProcessLookupError):
            continue
    deadline = time.time() + timeout
    for pid in stopped:
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.05)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    try:
        os.remove(PID_FILE)
    except OSError:
        pass
    return stopped


def _write_pid(port):
    tmp = PID_FILE + ".tmp"
    try:
        os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "port": port,
                       "script": os.path.realpath(__file__)}, f)
        os.replace(tmp, PID_FILE)
        return True
    except OSError as exc:
        print(f"dashboard pid file unavailable: {exc}", file=sys.stderr)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _remove_pid():
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            record = json.load(f)
        if int(record.get("pid", -1)) == os.getpid():
            os.remove(PID_FILE)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass


def ensure_daemon(port: int) -> str | None:
    """
    Ensure the dashboard is running as a background daemon.
    Returns a one-line status string, or None if opt-out is set.
    Never raises.
    """
    try:
        if os.environ.get("PRISM_NO_DASHBOARD"):
            return None
        url = f"http://127.0.0.1:{port}"
        s = _token_summary()
        suffix = f"  · {s}" if s else ""
        if _port_in_use(port):
            return f"📊 AI economics dashboard live → {url}{suffix}"
        # A recorded owned process that is no longer listening is stale/hung;
        # stop it before creating the replacement daemon.
        _stop_previous_dashboard(port)
        # Not running — spawn a detached background process
        logpath = os.path.join(HOME, ".claude", ".token-dashboard.log")
        os.makedirs(os.path.dirname(logpath), exist_ok=True)
        with open(logpath, "a") as log:
            subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), "--no-open", "--port", str(port)],
                stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True)
        return f"📊 AI economics dashboard starting → {url}{suffix}  (first scan runs in the background)"
    except Exception as exc:
        print(f"dashboard startup failed: {exc}", file=sys.stderr)
        return None


def _sqlite_dashboard(args):
    """Start/reopen the on-demand server independently of scheduled collection."""
    from _token_store import TokenStore, register_artifact
    from _token_http import DashboardServer

    root = os.path.abspath(args.root or os.path.expanduser("~/.100xprism/token-economics"))
    runtime_path = os.path.join(root, "dashboard-runtime.json")
    script = os.path.abspath(__file__)
    collector = os.path.join(os.path.dirname(script), "token-collector.py")

    def collect():
        subprocess.Popen([sys.executable, "-B", collector, "--db", args.db, "--root", root],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)

    if args.text or args.oneline:
        store = TokenStore(args.db)
        try:
            data = store.latest_snapshot()
        finally:
            store.close()
        if data is None:
            result = subprocess.run([sys.executable, "-B", collector, "--db", args.db, "--root", root])
            if result.returncode:
                return result.returncode
            store = TokenStore(args.db)
            try:
                data = store.latest_snapshot()
            finally:
                store.close()
        if data:
            if args.oneline:
                print(f"{fmt(sum(data.get('totals', {}).values()))} tokens · ${data.get('total_cost', 0):.2f} estimated")
            else:
                print_summary(data)
        else:
            print("No completed local snapshot yet.", file=sys.stderr)
        return 0

    runtime = None
    try:
        if os.path.islink(runtime_path):
            raise ValueError("refusing symlink credential")
        with open(runtime_path) as stream:
            runtime = json.load(stream)
        pid = runtime.get("pid")
        owned = (isinstance(pid, int) and pid > 0 and
                 runtime.get("script") == script and
                 script in _command_for_pid(pid) and
                 isinstance(runtime.get("port"), int) and
                 re.fullmatch(r"[0-9a-f]{64}", runtime.get("token", "")))
        if owned and _port_in_use(runtime["port"]):
            url = f"http://127.0.0.1:{runtime['port']}"
            print(f"Token dashboard → {url}")
            collect()
            if not args.no_open:
                webbrowser.open(url + "#token=" + runtime["token"])
            return 0
        # Only a registered credential for a dead PID can be removed here.
        from _token_store import load_manifest
        manifest = load_manifest(root)
        registered = any(row["path"] == runtime_path for row in manifest["artifacts"])
        if registered and isinstance(pid, int) and not _command_for_pid(pid):
            os.unlink(runtime_path)
        else:
            raise ValueError("dashboard credential belongs to another process; stop it before starting")
    except FileNotFoundError:
        pass

    if args.ensure_daemon:
        command = [sys.executable, "-B", script, "--db", args.db, "--root", root,
                   "--no-open", "--port", str(args.port)]
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and child.poll() is None:
            try:
                with open(runtime_path) as stream:
                    record = json.load(stream)
                if record.get("pid") == child.pid:
                    print(f"Token dashboard → http://127.0.0.1:{record['port']}")
                    if not args.no_open:
                        webbrowser.open(f"http://127.0.0.1:{record['port']}#token={record['token']}")
                    return 0
            except (OSError, ValueError):
                pass
            time.sleep(.05)
        print("Dashboard did not start; run 100xprism tokens --no-open to inspect startup.", file=sys.stderr)
        return 1

    page = PAGE.replace("const PRISM_SECURE = false", "const PRISM_SECURE = true")
    def interactive_github():
        store = TokenStore(args.db)
        try:
            data = store.latest_snapshot() or {}
        finally:
            store.close()
        return _github_insights(data.get("directories", []))
    srv = DashboardServer(("127.0.0.1", args.port), page, lambda: TokenStore(args.db), collect,
                          runtime_path, register=lambda target: register_artifact(root, target),
                          github=interactive_github)
    # Signal handlers cannot call shutdown on the serving thread itself.
    def stop(signum, frame):
        threading.Thread(target=srv.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    print(f"Token dashboard → {srv.origin}", flush=True)
    collect()
    if not args.no_open:
        webbrowser.open(srv.url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--print", action="store_true", dest="text")
    ap.add_argument("--oneline", action="store_true",
                    help="one fast summary line from cache (no rescan) — for shell startup")
    ap.add_argument("--ensure-daemon", action="store_true",
                    help="ensure dashboard is running as a background daemon; print URL + status")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--db", help="read committed economics snapshots from SQLite")
    ap.add_argument("--root", help="owned token-economics state root")
    args = ap.parse_args()

    if args.db:
        sys.exit(_sqlite_dashboard(args))

    if args.oneline:
        _oneline()
        return

    if args.ensure_daemon:
        line = ensure_daemon(args.port)
        if line:
            print(line)
        return

    available_sources = [
        collector.source
        for collector in usage_collectors()
        if os.path.isdir(getattr(collector.module, "SOURCE_DIR", ""))
    ]
    if not available_sources:
        print("No supported local AI-tool transcripts found", file=sys.stderr)
        sys.exit(1)

    url = f"http://127.0.0.1:{args.port}"

    # An explicit launch replaces an older owned UI/daemon. Shell-startup calls to
    # --ensure-daemon remain idempotent and do not churn a healthy server.
    if not args.text:
        stopped = _stop_previous_dashboard(args.port)
        if stopped:
            print(f"Stopped previous token dashboard ({', '.join(map(str, stopped))})")
        if _port_in_use(args.port):
            print(f"Port {args.port} is occupied by another service; not stopping it.",
                  file=sys.stderr)
            return

    if args.text:
        print("Scanning transcripts (first run is slow; later runs use the cache)...", file=sys.stderr)
        data = build(verbose=True)
        print_summary(data)
        return

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        # Lost a startup race with another session — point at the live one.
        print(f"Token dashboard already running → {url}  (covers all sessions/repos)")
        if not args.no_open:
            try:
                webbrowser.open(url)
            except Exception as exc:
                print(f"dashboard browser open failed: {exc}", file=sys.stderr)
        return
    _write_pid(args.port)
    print(f"\nToken dashboard → {url}  (Ctrl-C to stop) — all sessions & repos on this machine", flush=True)
    print("Scanning starts now; the page shows live progress and partial totals as sources finish.",
          file=sys.stderr)
    _start_rebuild(verbose=True)

    def _auto_refresh():
        while True:
            time.sleep(REFRESH_SECONDS)
            with _build_state_lock:
                if _build_state["building"]:
                    continue
            try:
                data = _rebuild()
                today_str = datetime.now().strftime("%Y-%m-%d")
                _budget.maybe_notify(data["budget"], today_str)
            except Exception as exc:
                print(f"dashboard auto-refresh failed: {exc}", file=sys.stderr)

    threading.Thread(target=_auto_refresh, daemon=True).start()

    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception as exc:
            print(f"dashboard browser open failed: {exc}", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        srv.server_close()
        _remove_pid()


if __name__ == "__main__":
    main()
