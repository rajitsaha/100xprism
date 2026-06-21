#!/usr/bin/env python3
"""Eval harness for the per-module evals.json files (issue #24).

Grading is done by the /eval skill, which fans each case out to parallel subagents and
has Haiku 4.5 judge every assertion (pass/fail + reason). This script is the
deterministic engine around that: it discovers and validates the eval files, emits the
work-list the skill grades, and renders the per-assertion scorecard from the graded
results. No model calls happen here, so it runs anywhere (CI included) with zero deps.

Subcommands:
  validate [SELECTOR]            Structurally validate eval files; non-zero exit on error.
  plan     [SELECTOR] [--json]   Emit the case/assertion work-list for the /eval skill.
  score    --results FILE [--json]
                                 Render a per-assertion pass/fail scorecard from graded
                                 results; non-zero exit if any assertion failed.

SELECTOR (default --all):
  --module SLUG        one module
  --all                every module that ships evals
  --changed [BASE]     modules changed vs BASE (default origin/main) — for per-PR CI

Results file (for `score`) is JSON: a list of
  {"module": str, "case_id": int, "assertion": str, "passed": bool, "reason": str}
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODULES_DIR = REPO / "modules"


# ── discovery ────────────────────────────────────────────────────────────────

def eval_path(slug: str) -> Path:
    return MODULES_DIR / slug / "evals" / "evals.json"


def modules_with_evals() -> list[str]:
    return sorted(p.parent.parent.name for p in MODULES_DIR.glob("*/evals/evals.json"))


def changed_modules(base: str) -> list[str]:
    """Slugs under modules/ touched vs BASE that ship evals."""
    r = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:  # base ref unavailable (shallow clone, fork) → fall back to all
        sys.stderr.write(f"note: `git diff {base}...HEAD` failed; scanning all modules\n")
        return modules_with_evals()
    touched = set()
    for line in r.stdout.splitlines():
        parts = line.split("/")
        if len(parts) >= 2 and parts[0] == "modules":
            touched.add(parts[1])
    have = set(modules_with_evals())
    return sorted(touched & have)


def select(args) -> list[str]:
    if args.module:
        return [args.module]
    if args.changed is not None:
        return changed_modules(args.changed or "origin/main")
    return modules_with_evals()


# ── load + validate ──────────────────────────────────────────────────────────

def load_evals(slug: str) -> tuple[dict | None, list[str]]:
    """Return (data, errors). data is None when the file is missing/unparseable."""
    path = eval_path(slug)
    if not path.exists():
        return None, [f"{slug}: no evals/evals.json"]
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return None, [f"{slug}: invalid JSON — {e}"]

    errors: list[str] = []
    if data.get("skill_name") and data["skill_name"] != slug:
        errors.append(f"{slug}: skill_name '{data['skill_name']}' != directory '{slug}'")
    cases = data.get("evals")
    if not isinstance(cases, list) or not cases:
        errors.append(f"{slug}: 'evals' must be a non-empty list")
        return data, errors
    seen_ids: set = set()
    for i, case in enumerate(cases):
        where = f"{slug} case[{i}]"
        cid = case.get("id")
        if cid is None:
            errors.append(f"{where}: missing id")
        elif cid in seen_ids:
            errors.append(f"{where}: duplicate id {cid}")
        else:
            seen_ids.add(cid)
        if not str(case.get("prompt", "")).strip():
            errors.append(f"{where}: empty prompt")
        assertions = case.get("assertions")
        if not isinstance(assertions, list) or not assertions:
            errors.append(f"{where}: needs a non-empty assertions list")
    return data, errors


# ── subcommands ──────────────────────────────────────────────────────────────

def cmd_validate(args) -> int:
    slugs = select(args)
    if not slugs:
        if args.changed is not None:
            print("validate: no changed module ships evals — nothing to grade ✓")
        else:
            print("validate: no modules selected — ok")
        return 0
    all_errors: list[str] = []
    total_cases = total_assertions = 0
    for slug in slugs:
        data, errors = load_evals(slug)
        all_errors += errors
        if data and isinstance(data.get("evals"), list):
            cases = data["evals"]
            total_cases += len(cases)
            total_assertions += sum(len(c.get("assertions", []) or []) for c in cases)
    print(f"validated {len(slugs)} module(s): {total_cases} cases, {total_assertions} assertions")
    if all_errors:
        for e in all_errors:
            sys.stderr.write(f"  ✗ {e}\n")
        sys.stderr.write(f"\neval-harness validate: {len(all_errors)} error(s)\n")
        return 1
    print("eval-harness validate: all eval files well-formed ✓")
    return 0


def build_plan(slugs: list[str]) -> dict:
    modules = []
    for slug in slugs:
        data, _ = load_evals(slug)
        if not data:
            continue
        cases = [
            {
                "id": c.get("id"),
                "prompt": c.get("prompt", ""),
                "expected_output": c.get("expected_output", ""),
                "assertions": list(c.get("assertions", []) or []),
                "files": list(c.get("files", []) or []),
            }
            for c in data.get("evals", [])
        ]
        modules.append({"module": slug, "cases": cases})
    return {"modules": modules}


def cmd_plan(args) -> int:
    plan = build_plan(select(args))
    if args.json:
        print(json.dumps(plan, indent=2))
        return 0
    for m in plan["modules"]:
        print(f"\n### {m['module']} — {len(m['cases'])} case(s)")
        for c in m["cases"]:
            print(f"  [{c['id']}] {c['prompt'][:90]}")
            for a in c["assertions"]:
                print(f"        - {a}")
    if not plan["modules"]:
        print("(no modules selected)")
    return 0


def cmd_score(args) -> int:
    results = json.loads(Path(args.results).read_text())
    if not isinstance(results, list):
        sys.stderr.write("score: results file must be a JSON list\n")
        return 2

    # group: module -> case_id -> [rows]
    grouped: dict = {}
    for r in results:
        grouped.setdefault(r.get("module", "?"), {}).setdefault(r.get("case_id"), []).append(r)

    if args.json:
        passed = sum(1 for r in results if r.get("passed"))
        print(json.dumps({
            "total": len(results), "passed": passed, "failed": len(results) - passed,
            "results": results,
        }, indent=2))
        return 0 if passed == len(results) else 1

    total = passed = 0
    for module in sorted(grouped):
        print(f"\n=== {module} ===")
        for case_id in sorted(grouped[module], key=lambda x: (x is None, x)):
            rows = grouped[module][case_id]
            n_pass = sum(1 for r in rows if r.get("passed"))
            print(f"  case {case_id}: {n_pass}/{len(rows)} assertions passed")
            for r in rows:
                mark = "✓" if r.get("passed") else "✗"
                line = f"    {mark} {r.get('assertion', '')}"
                if not r.get("passed") and r.get("reason"):
                    line += f" — {r['reason']}"
                print(line)
            total += len(rows)
            passed += n_pass
    print(f"\nscorecard: {passed}/{total} assertions passed across {len(grouped)} module(s)")
    return 0 if passed == total else 1


def add_selector(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument("--module", help="single module slug")
    g.add_argument("--all", action="store_true", help="every module with evals (default)")
    g.add_argument("--changed", nargs="?", const="origin/main", default=None,
                   metavar="BASE", help="modules changed vs BASE (default origin/main)")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="100xprism eval harness")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("validate", help="structurally validate eval files")
    add_selector(pv)
    pv.set_defaults(func=cmd_validate)

    pp = sub.add_parser("plan", help="emit the case/assertion work-list")
    add_selector(pp)
    pp.add_argument("--json", action="store_true")
    pp.set_defaults(func=cmd_plan)

    ps = sub.add_parser("score", help="render a scorecard from graded results")
    ps.add_argument("--results", required=True)
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_score)

    args = ap.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
