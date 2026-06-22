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
instance covers every session and every repo/directory on the machine. Launching
it again (from any repo, any session) just opens the already-running URL.

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

Pricing for the optional cost estimate is editable below (RATES). Defaults are
Opus-tier list prices and are only a rough guide.
"""
import argparse
import glob
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from collections import defaultdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _value  # noqa: E402 — shared value layer (one source of truth)
import _summaries  # noqa: E402

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")
CACHE_FILE = os.path.join(HOME, ".claude", ".token-dashboard-cache.json")
CACHE_VERSION = 2
REFRESH_SECONDS = 300  # auto-rebuild cadence so a long-lived tab never goes stale

# $ per 1M tokens — rough Opus-tier list prices; edit to match your plan/model.
RATES = {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75}


def _empty():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def _add(dst, i, o, cr, cw):
    dst["input"] += i
    dst["output"] += o
    dst["cache_read"] += cr
    dst["cache_write"] += cw


# Content-composition categories. CHAR counts (later ÷4 → an *estimate* of tokens).
# This is NOT billed truth: the API bills per-turn aggregates, not per content
# block — so this shows where conversation TEXT VOLUME goes, not exact tokens.
COMP_CATS = ["prompts", "model_output", "code_authored", "tool_calls",
             "files_read", "logs", "other_results"]
COMP_LABELS = {
    "prompts": "your prompts",
    "model_output": "model output (prose)",
    "code_authored": "code written (edits)",
    "tool_calls": "tool calls",
    "files_read": "code / files read",
    "logs": "command output / logs",
    "other_results": "other tool results",
}
_READ_TOOLS = {"Read", "Glob", "Grep", "LS", "NotebookRead"}
_SHELL_TOOLS = {"Bash", "BashOutput"}
_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}


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
            name = tool_names.get(b.get("tool_use_id", ""), "")
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


def parse_file(path):
    """Aggregate one transcript. Returns a per-file summary dict."""
    totals = _empty()
    by_day = defaultdict(_empty)
    by_model = defaultdict(_empty)
    comp = defaultdict(int)      # content-type char counts (composition estimate)
    tool_names = {}              # tool_use id -> tool name, for classifying results
    msgs = 0
    first_fixed = None  # fixed context at first billed turn (bloat proxy)
    turns = 0
    for line in open(path, errors="ignore"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if not isinstance(o, dict):
            continue
        m = o.get("message")
        # Composition: classify every message's content blocks (billed or not).
        if isinstance(m, dict):
            role = m.get("role") or o.get("type") or ""
            _classify(role, m.get("content"), comp, tool_names)
        u = m.get("usage") if isinstance(m, dict) else None
        if not isinstance(u, dict):
            u = o.get("usage") if isinstance(o.get("usage"), dict) else None
        if not isinstance(u, dict):
            continue
        i = u.get("input_tokens", 0) or 0
        ot = u.get("output_tokens", 0) or 0
        cr = u.get("cache_read_input_tokens", 0) or 0
        cw = u.get("cache_creation_input_tokens", 0) or 0
        if not (i or ot or cr or cw):
            continue
        msgs += 1
        turns += 1
        _add(totals, i, ot, cr, cw)
        model = (m.get("model") if isinstance(m, dict) else None) or "unknown"
        _add(by_model[model], i, ot, cr, cw)
        day = (o.get("timestamp") or "")[:10] or "unknown"
        _add(by_day[day], i, ot, cr, cw)
        if first_fixed is None and (i + cr + cw) > 0:
            first_fixed = i + cr + cw
    return {
        "totals": totals,
        "by_day": dict(by_day),
        "by_model": dict(by_model),
        "comp": {k: comp.get(k, 0) for k in COMP_CATS},
        "msgs": msgs,
        "turns": turns,
        "first_fixed": first_fixed or 0,
    }


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
        print(f"warning: could not write cache: {e}", file=sys.stderr)


# project_label lives in _value so the dashboard and the value store derive the
# exact same join key from a path.
project_label = _value.project_label


def build(verbose=True):
    """Scan transcripts (incremental via cache) and return the full dataset."""
    cache = load_cache()
    paths = glob.glob(os.path.join(PROJECTS_DIR, "*", "*.jsonl"))
    new_cache = {}
    reparsed = 0
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        key = p
        prev = cache.get(key)
        if prev and prev.get("mtime") == st.st_mtime and prev.get("size") == st.st_size:
            new_cache[key] = prev
            continue
        summary = parse_file(p)
        summary["mtime"] = st.st_mtime
        summary["size"] = st.st_size
        summary["project"] = project_label(p)
        summary["projdir"] = os.path.basename(os.path.dirname(p))
        new_cache[key] = summary
        reparsed += 1
        if verbose and reparsed % 200 == 0:
            print(f"  parsed {reparsed} new/changed transcripts...", file=sys.stderr)
    save_cache(new_cache)
    if verbose:
        print(f"scanned {len(paths)} transcripts ({reparsed} re-parsed, "
              f"{len(paths) - reparsed} cached)", file=sys.stderr)

    # Aggregate
    totals = _empty()
    by_project = defaultdict(_empty)
    by_day = defaultdict(_empty)
    by_project_day = defaultdict(lambda: defaultdict(_empty))
    by_model = defaultdict(_empty)
    comp_chars = defaultdict(int)
    sessions = 0
    fixed_samples = []
    total_msgs = 0
    for s in new_cache.values():
        t = s["totals"]
        _add(totals, t["input"], t["output"], t["cache_read"], t["cache_write"])
        proj = s.get("project", "?")
        bp = by_project[proj]
        _add(bp, t["input"], t["output"], t["cache_read"], t["cache_write"])
        for day, d in s.get("by_day", {}).items():
            _add(by_day[day], d["input"], d["output"], d["cache_read"], d["cache_write"])
            _add(by_project_day[proj][day], d["input"], d["output"], d["cache_read"], d["cache_write"])
        for mdl, d in s.get("by_model", {}).items():
            _add(by_model[mdl], d["input"], d["output"], d["cache_read"], d["cache_write"])
        for cat, n in s.get("comp", {}).items():
            comp_chars[cat] += n
        if s.get("turns"):
            sessions += 1
            total_msgs += s.get("msgs", 0)
        if s.get("first_fixed"):
            fixed_samples.append(s["first_fixed"])

    fixed_samples.sort()
    n = len(fixed_samples)
    median_fixed = fixed_samples[n // 2] if n else 0
    avg_fixed = sum(fixed_samples) / n if n else 0

    def cost(d):
        return sum(d[k] / 1_000_000 * RATES[k] for k in RATES)

    # cost per (project label × day) — server-side input to the value↔cost join
    by_project_day_cost = {
        lbl: {day: round(cost(dd), 4) for day, dd in days.items()}
        for lbl, days in by_project_day.items()
    }
    # Build per-directory cost+value rows (git subprocesses run here, not on request path).
    mangled_by_label, tokens_by_label, window_by_label, tool_by_label = {}, {}, {}, {}
    for s in new_cache.values():
        label = s.get("project", "?")
        mangled = s.get("projdir", "")
        mangled_by_label.setdefault(mangled, label)
        tk = tokens_by_label.setdefault(label, _empty())
        t = s["totals"]
        _add(tk, t["input"], t["output"], t["cache_read"], t["cache_write"])
        tool_by_label[label] = "claude-code"
        days = sorted(d for d in s.get("by_day", {}) if d != "unknown")
        if days:
            lo, hi = window_by_label.get(label, (days[0], days[-1]))
            window_by_label[label] = (min(lo, days[0]), max(hi, days[-1]))
    directories = assemble_directories(
        mangled_by_label, tokens_by_label, by_project_day_cost,
        window_by_label, tool_by_label)

    # Composition estimate: chars ÷ 4 ≈ tokens. Labelled an estimate in the UI.
    comp_tokens = {k: comp_chars.get(k, 0) // 4 for k in COMP_CATS}
    comp_sum = sum(comp_tokens.values()) or 1
    composition = sorted(
        ([COMP_LABELS[k], comp_tokens[k], round(100 * comp_tokens[k] / comp_sum, 1)]
         for k in COMP_CATS if comp_tokens[k]),
        key=lambda r: -r[1],
    )

    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "transcripts": len(paths),
        "sessions": sessions,
        "messages": total_msgs,
        "totals": totals,
        "total_cost": round(cost(totals), 2),
        "rates": RATES,
        "bloat": {"median": int(median_fixed), "avg": int(avg_fixed), "samples": n},
        "composition": composition,
        "by_project": sorted(
            ([k, v, round(cost(v), 2)] for k, v in by_project.items()),
            key=lambda r: -(r[1]["input"] + r[1]["cache_read"] + r[1]["cache_write"]),
        )[:25],
        "by_day": sorted(([k, v] for k, v in by_day.items() if k != "unknown")),
        "by_model": sorted(
            ([k, v] for k, v in by_model.items()),
            key=lambda r: -(r[1]["input"] + r[1]["cache_read"] + r[1]["cache_write"]),
        ),
        "by_project_day_cost": by_project_day_cost,
        "directories": directories,
    }


def fmt(n):
    n = float(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return str(int(n))


def print_summary(data):
    t = data["totals"]
    print(f"\nClaude Code token usage — {data['generated']}")
    print(f"{data['transcripts']} transcripts, {data['sessions']} sessions, "
          f"{fmt(data['messages'])} billed messages\n")
    print(f"  input (uncached) : {fmt(t['input']):>8}")
    print(f"  output           : {fmt(t['output']):>8}")
    print(f"  cache READ       : {fmt(t['cache_read']):>8}   (re-sent context — usually the largest)")
    print(f"  cache WRITE      : {fmt(t['cache_write']):>8}")
    print(f"  est. cost        : ${data['total_cost']:,}")
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
                         window_by_label, tool_by_label):
    """Build the unified per-directory rows (cost + tool-agnostic value).

    mangled_by_label: {mangled_dirname: label} — key is the raw Claude projects dir name,
    value is the human-readable project label (e.g. "~/personal-github/100xprism").
    """
    rows = []
    for mangled, label in mangled_by_label.items():
        real = _value.resolve_real_dir(mangled)
        start, end = window_by_label.get(label, (None, None))
        daycost = by_project_day_cost.get(label, {})
        cost = round(sum(daycost.values()), 2) if daycost else None
        tool = tool_by_label.get(label, "claude-code")
        value = (_value.cached_dir_value(real, label, tool, start, end)
                 if real else _value._empty_value())
        rows.append({
            "dir": real, "label": label, "tool": tool,
            "cost": cost, "tokens": tokens_by_label.get(label, _empty()),
            "window": {"start": start, "end": end}, "value": value,
        })
    rows.sort(key=lambda r: -(r["cost"] or 0))
    return rows


# ---------------------------------------------------------------- web UI

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Claude Code — Token Usage</title>
<style>
:root{--ink:#0E1116;--surface:#171B22;--line:#262C36;--text:#E6E9EF;--muted:#8A93A2;
--cost:#E8B24A;--value:#5BD0A6;--warn:#E5704B;
--in:#58a6ff;--out:#f778ba;--cr:#3fb950;--cw:#d29922}
*{box-sizing:border-box}
body{margin:0;background:var(--ink);color:var(--text);
font:14px/1.55 'IBM Plex Sans',-apple-system,Segoe UI,Roboto,sans-serif}
.num,td.n,.money{font-family:'IBM Plex Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}
header{padding:20px 28px;border-bottom:1px solid var(--line);display:flex;
align-items:baseline;gap:16px;flex-wrap:wrap}
h1{font-size:18px;margin:0}.sub{color:var(--muted);font-size:13px}
.wrap{padding:24px 28px;max-width:1100px;margin:0 auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:24px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.card .lbl{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.card .val{font-size:26px;font-weight:600;margin-top:4px}
.card .note{color:var(--muted);font-size:12px;margin-top:6px}
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
.legend{font-size:12px;color:var(--muted);margin:10px 0}.legend span{margin-right:16px}
button{background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:7px;
padding:6px 12px;cursor:pointer;font-size:13px}button:hover{border-color:var(--in)}
.muted{color:var(--muted)}
section{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
section h2{margin-top:0;border-bottom-color:var(--line)}
.cards2{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:8px 0 24px}
@media(max-width:760px){.cards2{grid-template-columns:1fr}}
.badge{display:inline-block;font:600 10px/1 'IBM Plex Mono',ui-monospace,monospace;
padding:3px 5px;border:1px solid var(--line);border-radius:4px;color:var(--muted)}
#tip{position:fixed;z-index:1000;pointer-events:none;display:none;background:var(--surface);
border:1px solid var(--line);border-radius:6px;padding:6px 9px;font:12px/1.3 'IBM Plex Mono',ui-monospace,monospace;
color:var(--text);max-width:280px;box-shadow:0 4px 16px rgba(0,0,0,.5)}
</style></head><body>
<div id=tip role=tooltip></div>
<header><h1>Claude Code · Token Usage</h1>
<span class=sub id=meta></span>
<span style=margin-left:auto><button onclick=refresh()>↻ Rescan</button></span></header>
<div class=wrap id=app><p class=muted>Loading…</p></div>
<script>
const C={input:'var(--in)',output:'var(--out)',cache_read:'var(--cr)',cache_write:'var(--cw)'};
function fmt(n){n=+n;for(const[u,d]of[['B',1e9],['M',1e6],['K',1e3]])if(Math.abs(n)>=d)return(n/d).toFixed(1)+u;return''+Math.round(n);}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function bar(v){const tot=v.input+v.output+v.cache_read+v.cache_write||1;
 const seg=k=>`<span style="width:${100*v[k]/tot}%;background:${C[k]}"></span>`;
 return `<div class=bar>${seg('cache_read')}${seg('cache_write')}${seg('input')}${seg('output')}</div>`;}
function legend(){return `<div class=legend>
 <span><i class=dot style=background:var(--cr)></i>cache read</span>
 <span><i class=dot style=background:var(--cw)></i>cache write</span>
 <span><i class=dot style=background:var(--in)></i>input</span>
 <span><i class=dot style=background:var(--out)></i>output</span></div>`;}
function emptyState(msg){return `<p class=muted style="padding:24px 0">${esc(msg)}</p>`;}
function svgEl(w,h,inner,label){return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" role="img" aria-label="${esc(label)}" style="max-width:100%">${inner}</svg>`;}
function leverageChart(dirs){
 const pts=dirs.filter(d=>d.cost!=null&&d.value).map(d=>({x:d.cost,
   y:(d.value.commits||0)+3*(d.value.prs||0)+(d.value.fs_files||0?1:0), d}));
 if(!pts.length) return emptyState('No cost-bearing directories yet.');
 const W=520,H=300,PL=50,PB=46,PR=36,PT=36;
 const mx=Math.max(...pts.map(p=>p.x),1), my=Math.max(...pts.map(p=>p.y),1);
 const X=x=>PL+(W-PL-PR)*x/mx, Y=y=>H-PB-(H-PB-PT)*y/my;
 const ratios=pts.map(p=>p.y/(p.x||1)).sort((a,b)=>a-b), r=ratios[ratios.length>>1]||1;
 const bline=`<line x1="${X(0)}" y1="${Y(0)}" x2="${X(mx)}" y2="${Y(r*mx)}" stroke="var(--muted)" stroke-dasharray="4 4"/>`;
 const dots=pts.map(p=>{const above=p.y>=r*p.x; const col=above?'var(--value)':'var(--warn)';
   const tip=`${esc(p.d.label)} — $${Math.round(p.x)} · ${p.d.value.commits||0} commits · ${p.d.value.prs||0} PRs`;
   return `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="5" fill="${col}" fill-opacity=".85" data-tip="${tip}" tabindex="0"/>`;}).join('');
 const ax=`<line x1="${PL}" y1="${H-PB}" x2="${W-PR}" y2="${H-PB}" stroke="var(--line)"/><line x1="${PL}" y1="${PT}" x2="${PL}" y2="${H-PB}" stroke="var(--line)"/>`;
 const tickAttrs='fill="var(--muted)" font-size="11"';
 const xTicks=`<text x="${PL}" y="${H-PB+14}" ${tickAttrs} text-anchor="middle">$0</text>`+
   `<text x="${W-PR}" y="${H-PB+14}" ${tickAttrs} text-anchor="middle">$${Math.round(mx)}</text>`;
 const yTicks=`<text x="${PL-6}" y="${H-PB}" ${tickAttrs} text-anchor="end" dominant-baseline="middle">0</text>`+
   `<text x="${PL-6}" y="${PT}" ${tickAttrs} text-anchor="end" dominant-baseline="middle">${Math.round(my)}</text>`;
 const xLabel=`<text x="${PL+(W-PL-PR)/2}" y="${H-2}" ${tickAttrs} text-anchor="middle">token cost ($) →</text>`;
 const yLabel=`<text x="${12}" y="${PT+(H-PB-PT)/2}" ${tickAttrs} text-anchor="middle" transform="rotate(-90,12,${PT+(H-PB-PT)/2})">↑ value shipped (commits + PRs)</text>`;
 return svgEl(W,H,ax+bline+dots+xTicks+yTicks+xLabel+yLabel,'Value shipped (y, commits+PRs) versus token cost (x, dollars); dots above the dashed break-even line ship more per token');
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
function costByDir(dirs){
 const rows=dirs.filter(d=>d.cost!=null).slice(0,12); if(!rows.length) return emptyState('No cost yet.');
 const mx=Math.max(...rows.map(r=>r.cost),1); const H=rows.length*26+8,W=520;
 const bars=rows.map((r,i)=>{const w=(W-160)*r.cost/mx; const y=i*26+4;
   return `<text x="0" y="${y+14}" fill="var(--muted)" font-size="12">${esc(r.label.slice(-26))}</text>`+
     `<rect x="150" y="${y+3}" width="${w}" height="14" rx="3" fill="var(--cost)" data-tip="${esc(r.label)}: $${Math.round(r.cost)}" tabindex="0"/>`+
     `<text x="${156+w}" y="${y+14}" fill="var(--text)" font-size="11">$${Math.round(r.cost)}</text>`;}).join('');
 return svgEl(W,H,bars,'Estimated token cost by directory, highest first');
}
function toolBadge(t){const m={'claude-code':'CC','codex':'CX'}; return `<span class=badge title="${esc(t)}">${esc(m[t]||'?')}</span>`;}
function dirsTable(dirs){
 let h=`<h2>All directories <span class=muted style="text-transform:none;font-weight:400">— cost (amber) × value shipped (green); — = no local token data for that tool</span></h2>`;
 h+=`<table><tr><th>directory</th><th>tool</th><th>est $</th><th>value (shipped)</th><th>AI note</th></tr>`;
 for(const d of dirs){
  const v=d.value||{}; const shipped = v.kind==='git'
    ? `${v.commits||0} commits${v.prs?'·'+v.prs+' PRs':''}`
    : v.kind==='fs' ? `${v.fs_files} files` : '—';
  h+=`<tr><td>${esc(d.label)}</td><td>${toolBadge(d.tool)}</td>`+
     `<td class=money>${d.cost==null?'<span class=muted>—</span>':'$'+Math.round(d.cost).toLocaleString()}</td>`+
     `<td style="color:var(--value)">${esc(shipped)}</td>`+
     `<td class=muted>${v.summary?esc(v.summary):'<span class=muted>—</span>'}</td></tr>`;
 }
 return h+'</table>';
}
async function load(){render(await (await fetch('/api/data')).json());}
async function refresh(){document.getElementById('app').innerHTML='<p class=muted>Rescanning…</p>';render(await (await fetch('/api/refresh')).json());}
function money(c){return c==null?'<span class=muted>—</span>':'$'+(+c).toLocaleString(undefined,{maximumFractionDigits:0});}
function render(d){
 document.getElementById('meta').textContent=
  `${d.transcripts} transcripts · ${d.sessions} sessions · updated ${d.generated}`;
 const t=d.totals, max=Math.max(...d.bloat.window?[]:[1]);
 const card=(lbl,val,note,col)=>`<div class=card><div class=lbl>${col?`<i class=dot style=background:${col}></i>`:''}${lbl}</div>
   <div class=val>${val}</div><div class=note>${note||''}</div></div>`;
 const W=200000, mw=Math.min(100,100*d.bloat.median/W);
 let h=`<div class=cards>
   ${card('cache read',fmt(t.cache_read),'re-sent context (cheap/token, huge volume)','var(--cr)')}
   ${card('output',fmt(t.output),'model generations — costliest per token','var(--out)')}
   ${card('cache write',fmt(t.cache_write),'context written on a miss','var(--cw)')}
   ${card('input',fmt(t.input),'new uncached text','var(--in)')}
   ${card('est. cost','$'+d.total_cost.toLocaleString(),'rough, list rates (editable)')}
  </div>`;
 h+=`<div class=cards2>
   <section><h2>Leverage — value vs cost</h2>${leverageChart(d.directories||[])}</section>
   <section><h2>Cost over time</h2>${costOverTime(d)}</section>
   <section><h2>Token-purpose split</h2>${purposeSplit(d.totals)}${legend()}</section>
   <section><h2>Cost by directory</h2>${costByDir(d.directories||[])}</section>
 </div>`;
 h+=dirsTable(d.directories||[]);
 h+=`<h2>Startup bloat — fixed context re-sent every turn</h2>
   <div class=meter><b style="width:${mw}%;background:${mw>30?'var(--cw)':'var(--cr)'}"></b>
   <em>median ${fmt(d.bloat.median)} of 200K window (${(d.bloat.median/W*100).toFixed(1)}%)</em></div>
   <p class=muted style=margin-top:8px>avg ${fmt(d.bloat.avg)} · lower is better. This is system prompt + tool/skill/agent descriptions + SessionStart injections, read on every turn.</p>`;
 if(d.composition&&d.composition.length){
  const CC=['#58a6ff','#f778ba','#3fb950','#d29922','#a371f7','#ff7b72','#8b949e'];
  const ct=d.composition.reduce((a,r)=>a+r[1],0)||1;
  let segs='',rows='';
  d.composition.forEach((r,i)=>{const col=CC[i%CC.length];
   segs+=`<span style="width:${100*r[1]/ct}%;background:${col}"></span>`;
   rows+=`<tr><td><i class=dot style=background:${col}></i>${esc(r[0])}</td><td>${fmt(r[1])}</td><td>${r[2]}%</td></tr>`;});
  h+=`<h2>Content composition <span class=muted style="text-transform:none;font-weight:400">— estimate, char-based (not billed tokens)</span></h2>
   <div class=bar style="height:14px;margin-bottom:12px">${segs}</div>
   <table><tr><th>content type</th><th>est. tokens</th><th>share</th></tr>${rows}</table>
   <p class=muted style=margin-top:8px>The API bills per-turn aggregates, so this approximates where your conversation <em>text volume</em> goes (chars÷4): code written, files read, command output/logs, model prose, prompts. Directional, not exact.</p>`;
 }
 h+=`<h2>By model</h2><table><tr><th>model</th><th>mix</th><th>cache read</th><th>output</th></tr>`;
 for(const[name,v]of d.by_model){h+=`<tr><td>${esc(name)}</td><td>${bar(v)}</td>
   <td>${fmt(v.cache_read)}</td><td>${fmt(v.output)}</td></tr>`;}
 h+='</table>';
 const days=d.by_day.slice(-30);
 h+=`<h2>Last ${days.length} active days</h2><table><tr><th>day</th><th>mix</th><th>read</th><th>out</th></tr>`;
 for(const[day,v]of days.reverse()){h+=`<tr><td>${esc(day)}</td><td>${bar(v)}</td>
   <td>${fmt(v.cache_read)}</td><td>${fmt(v.output)}</td></tr>`;}
 h+='</table>';
 document.getElementById('app').innerHTML=h;
}
load();
setInterval(load, 300000);  // auto-refresh every 5 min so the tab never goes stale
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


def _rebuild():
    with _build_lock:
        Handler.data = build(verbose=False)
    threading.Thread(target=lambda: _summaries.backfill(), daemon=True).start()
    return Handler.data


def _client_data(data):
    """Token data for the browser. Trim per-day cost to the dirs we chart."""
    top = [d["label"] for d in data.get("directories", [])[:12]]
    bpd = {k: v for k, v in data.get("by_project_day_cost", {}).items() if k in top}
    out = {k: v for k, v in data.items() if k != "by_project_day_cost"}
    out["by_project_day_cost"] = bpd
    return out


class Handler(BaseHTTPRequestHandler):
    data = None

    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/refresh"):
            self._send(json.dumps(_client_data(_rebuild())))
        elif self.path.startswith("/api/data"):
            if Handler.data is None:
                _rebuild()
            self._send(json.dumps(_client_data(Handler.data)))
        else:
            self._send(PAGE, "text/html; charset=utf-8")


def _oneline():
    """Fast cache-only summary line for shell startup. Silent if no cache yet."""
    files = load_cache()
    if not files:
        return
    tot = _empty()
    for s in files.values():
        t = s.get("totals", {})
        _add(tot, t.get("input", 0), t.get("output", 0),
             t.get("cache_read", 0), t.get("cache_write", 0))
    if not any(tot.values()):
        return
    cost = sum(tot[k] / 1_000_000 * RATES[k] for k in RATES)
    print(f"100xPrism tokens (as of last scan): {fmt(tot['output'])} out · "
          f"{fmt(tot['cache_read'])} ctx · ~${cost:,.0f} · run `100x-tokens` for the dashboard")


def _port_in_use(port):
    """True if something is already listening on 127.0.0.1:port (a running dash)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--print", action="store_true", dest="text")
    ap.add_argument("--oneline", action="store_true",
                    help="one fast summary line from cache (no rescan) — for shell startup")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    if args.oneline:
        _oneline()
        return

    if not os.path.isdir(PROJECTS_DIR):
        print(f"No transcripts found at {PROJECTS_DIR}", file=sys.stderr)
        sys.exit(1)

    url = f"http://127.0.0.1:{args.port}"

    # Singleton: one dashboard serves EVERY session/repo on this machine (it reads
    # the global ~/.claude/projects). If one is already up, just open that URL.
    if not args.text and _port_in_use(args.port):
        print(f"Token dashboard already running → {url}  (covers all sessions/repos)")
        if not args.no_open:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return

    print("Scanning transcripts (first run is slow; later runs use the cache)...", file=sys.stderr)
    data = build(verbose=True)

    if args.text:
        print_summary(data)
        return

    Handler.data = data
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        # Lost a startup race with another session — point at the live one.
        print(f"Token dashboard already running → {url}  (covers all sessions/repos)")
        if not args.no_open:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return
    print(f"\nToken dashboard → {url}  (Ctrl-C to stop) — all sessions & repos on this machine")

    def _auto_refresh():
        while True:
            time.sleep(REFRESH_SECONDS)
            try:
                _rebuild()
            except Exception:
                pass

    threading.Thread(target=_auto_refresh, daemon=True).start()

    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
