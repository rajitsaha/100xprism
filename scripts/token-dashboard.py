#!/usr/bin/env python3
"""
token-dashboard.py — local, offline token-usage dashboard for Claude Code.

Reads ~/.claude/projects/**/*.jsonl (the transcripts Claude Code writes), and
serves a small web UI that breaks usage down by the four token "purposes"
(input / output / cache-read / cache-write), by project, by day, and by model.
It also shows a "startup bloat" meter: the fixed context (system prompt + tool/
skill/agent descriptions + SessionStart injections) re-sent on every turn.

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
import sys
import webbrowser
from collections import defaultdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")
CACHE_FILE = os.path.join(HOME, ".claude", ".token-dashboard-cache.json")
CACHE_VERSION = 2

# $ per 1M tokens — rough Opus-tier list prices; edit to match your plan/model.
RATES = {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75}


def _empty():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def _add(dst, i, o, cr, cw):
    dst["input"] += i
    dst["output"] += o
    dst["cache_read"] += cr
    dst["cache_write"] += cw


def parse_file(path):
    """Aggregate one transcript. Returns a per-file summary dict."""
    totals = _empty()
    by_day = defaultdict(_empty)
    by_model = defaultdict(_empty)
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


def project_label(path):
    """Turn a transcript path into a readable project name."""
    seg = path.split("/projects/")[1].split("/")[0] if "/projects/" in path else path
    return seg.replace("-Users-rajit-", "~/").replace("-", "/")


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
    by_model = defaultdict(_empty)
    sessions = 0
    fixed_samples = []
    total_msgs = 0
    for s in new_cache.values():
        t = s["totals"]
        _add(totals, t["input"], t["output"], t["cache_read"], t["cache_write"])
        bp = by_project[s.get("project", "?")]
        _add(bp, t["input"], t["output"], t["cache_read"], t["cache_write"])
        for day, d in s.get("by_day", {}).items():
            _add(by_day[day], d["input"], d["output"], d["cache_read"], d["cache_write"])
        for mdl, d in s.get("by_model", {}).items():
            _add(by_model[mdl], d["input"], d["output"], d["cache_read"], d["cache_write"])
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

    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "transcripts": len(paths),
        "sessions": sessions,
        "messages": total_msgs,
        "totals": totals,
        "total_cost": round(cost(totals), 2),
        "rates": RATES,
        "bloat": {"median": int(median_fixed), "avg": int(avg_fixed), "samples": n},
        "by_project": sorted(
            ([k, v, round(cost(v), 2)] for k, v in by_project.items()),
            key=lambda r: -(r[1]["input"] + r[1]["cache_read"] + r[1]["cache_write"]),
        )[:25],
        "by_day": sorted(([k, v] for k, v in by_day.items() if k != "unknown")),
        "by_model": sorted(
            ([k, v] for k, v in by_model.items()),
            key=lambda r: -(r[1]["input"] + r[1]["cache_read"] + r[1]["cache_write"]),
        ),
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
    print("\n  Top projects (by input volume):")
    for name, v, c in data["by_project"][:10]:
        tot = v["input"] + v["cache_read"] + v["cache_write"]
        print(f"    {fmt(tot):>8} in / {fmt(v['output']):>6} out  ${c:>8,.0f}  {name}")
    print()


# ---------------------------------------------------------------- web UI

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Claude Code — Token Usage</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--bd:#30363d;--fg:#e6edf3;--mut:#8b949e;
--in:#58a6ff;--out:#f778ba;--cr:#3fb950;--cw:#d29922}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif}
header{padding:20px 28px;border-bottom:1px solid var(--bd);display:flex;
align-items:baseline;gap:16px;flex-wrap:wrap}
h1{font-size:18px;margin:0}.sub{color:var(--mut);font-size:13px}
.wrap{padding:24px 28px;max-width:1100px;margin:0 auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px 18px}
.card .lbl{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.card .val{font-size:26px;font-weight:600;margin-top:4px}
.card .note{color:var(--mut);font-size:12px;margin-top:6px}
.dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px;vertical-align:middle}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);
margin:28px 0 12px;border-bottom:1px solid var(--bd);padding-bottom:8px}
table{width:100%;border-collapse:collapse}
td,th{padding:7px 10px;text-align:right;border-bottom:1px solid var(--bd);font-variant-numeric:tabular-nums}
th{color:var(--mut);font-weight:500;font-size:12px;text-transform:uppercase}
td:first-child,th:first-child{text-align:left}
.bar{height:9px;border-radius:5px;display:flex;overflow:hidden;background:#21262d;min-width:120px}
.bar span{display:block;height:100%}
.meter{background:#21262d;border-radius:6px;height:22px;position:relative;overflow:hidden;max-width:520px}
.meter b{position:absolute;left:0;top:0;bottom:0;border-radius:6px}
.meter em{position:absolute;left:10px;top:0;line-height:22px;font-style:normal;font-size:12px}
.legend{font-size:12px;color:var(--mut);margin:10px 0}.legend span{margin-right:16px}
button{background:var(--card);color:var(--fg);border:1px solid var(--bd);border-radius:7px;
padding:6px 12px;cursor:pointer;font-size:13px}button:hover{border-color:var(--in)}
.muted{color:var(--mut)}
</style></head><body>
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
async function load(){
 const d=await (await fetch('/api/data')).json(); render(d);}
async function refresh(){document.getElementById('app').innerHTML='<p class=muted>Rescanning…</p>';
 const d=await (await fetch('/api/refresh')).json(); render(d);}
function render(d){
 document.getElementById('meta').textContent=
  `${d.transcripts} transcripts · ${d.sessions} sessions · generated ${d.generated}`;
 const t=d.totals, max=Math.max(...d.bloat.window?[]:[1]);
 const card=(lbl,val,note,col)=>`<div class=card><div class=lbl>${col?`<i class=dot style=background:${col}></i>`:''}${lbl}</div>
   <div class=val>${val}</div><div class=note>${note||''}</div></div>`;
 // bloat meter: 200k window
 const W=200000, mw=Math.min(100,100*d.bloat.median/W);
 let h=`<div class=cards>
   ${card('cache read',fmt(t.cache_read),'re-sent context (cheap/token, huge volume)','var(--cr)')}
   ${card('output',fmt(t.output),'model generations — costliest per token','var(--out)')}
   ${card('cache write',fmt(t.cache_write),'context written on a miss','var(--cw)')}
   ${card('input',fmt(t.input),'new uncached text','var(--in)')}
   ${card('est. cost','$'+d.total_cost.toLocaleString(),'rough, list rates (editable)')}
  </div>`;
 h+=`<h2>Startup bloat — fixed context re-sent every turn</h2>
   <div class=meter><b style="width:${mw}%;background:${mw>30?'var(--cw)':'var(--cr)'}"></b>
   <em>median ${fmt(d.bloat.median)} of 200K window (${(d.bloat.median/W*100).toFixed(1)}%)</em></div>
   <p class=muted style=margin-top:8px>avg ${fmt(d.bloat.avg)} · lower is better. This is system prompt + tool/skill/agent descriptions + SessionStart injections, read on every turn.</p>`;
 h+=`<h2>By project</h2>${legend()}<table><tr><th>project</th><th>mix</th><th>cache read</th><th>output</th><th>est $</th></tr>`;
 for(const[name,v,c]of d.by_project){h+=`<tr><td>${esc(name)}</td><td>${bar(v)}</td>
   <td>${fmt(v.cache_read)}</td><td>${fmt(v.output)}</td><td>$${c.toLocaleString()}</td></tr>`;}
 h+='</table>';
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
</script></body></html>"""


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
            Handler.data = build(verbose=False)
            self._send(json.dumps(Handler.data))
        elif self.path.startswith("/api/data"):
            if Handler.data is None:
                Handler.data = build(verbose=False)
            self._send(json.dumps(Handler.data))
        else:
            self._send(PAGE, "text/html; charset=utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--print", action="store_true", dest="text")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(PROJECTS_DIR):
        print(f"No transcripts found at {PROJECTS_DIR}", file=sys.stderr)
        sys.exit(1)

    print("Scanning transcripts (first run is slow; later runs use the cache)...", file=sys.stderr)
    data = build(verbose=True)

    if args.text:
        print_summary(data)
        return

    Handler.data = data
    url = f"http://127.0.0.1:{args.port}"
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"\nToken dashboard → {url}  (Ctrl-C to stop)")
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
