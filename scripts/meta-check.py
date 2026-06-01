#!/usr/bin/env python3
"""Repo consistency / drift checker for 100x-dev.

Run locally (`python3 scripts/meta-check.py`) or in CI (.github/workflows/meta.yml).
Catches the drift classes that have bitten releases before:

  1. Module frontmatter parses cleanly (delimited block + non-empty name & description).
  2. Declared counts in README match what the repo actually contains
     (modules / slash commands / auto-trigger skills / plugins) — every mention,
     including the banner alt-text.
  3. Every evals.json is valid JSON.
  4. Version triple agrees: VERSION == package.json.version
     (== git tag when TAG is passed via --tag or the TAG env var, e.g. in release.yml).

Exit code 0 = all checks pass; 1 = at least one failure (each printed with a ✗).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODULES_DIR = REPO / "modules"

failures: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def ok(msg: str) -> None:
    notes.append(msg)


def split_frontmatter(text: str) -> tuple[dict[str, str], bool]:
    """Lenient line-based parse mirroring adapters/lib/modules.py.

    Returns (fields, well_formed). well_formed is False when the leading/closing
    `---` delimiters are missing.
    """
    if not text.startswith("---\n"):
        return {}, False
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, False
    fm: dict[str, str] = {}
    current_key: str | None = None
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        if line.startswith(" ") and current_key:
            fm[current_key] = (fm[current_key] + " " + line.strip()).strip()
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            current_key = key.strip()
            fm[current_key] = val.strip()
    return fm, True


def check_modules() -> dict[str, int]:
    """Parse every module; fail on malformed frontmatter. Returns computed counts."""
    skill_files = sorted(MODULES_DIR.glob("*/SKILL.md"))
    if not skill_files:
        fail("no modules found under modules/*/SKILL.md")
        return {"modules": 0, "slash": 0, "skills": 0}

    slash = 0
    for sf in skill_files:
        fm, well_formed = split_frontmatter(sf.read_text())
        slug = sf.parent.name
        if not well_formed:
            fail(f"{slug}: SKILL.md has no parseable `---` frontmatter block")
            continue
        if not fm.get("name"):
            fail(f"{slug}: frontmatter missing `name`")
        if not fm.get("description"):
            fail(f"{slug}: frontmatter missing `description`")
        if fm.get("slash_command"):
            slash += 1

    total = len(skill_files)
    counts = {"modules": total, "slash": slash, "skills": total - slash}
    ok(f"modules parsed: {total} ({slash} slash commands, {total - slash} auto-trigger skills)")
    return counts


def check_plugins() -> int:
    data = json.loads((REPO / "plugins" / "plugins.json").read_text())
    n = len(data.get("plugins", []))
    # Every enabled plugin must be a fully-qualified name@marketplace id, and its
    # marketplace must be resolvable (built-in official marketplaces or declared
    # in extraKnownMarketplaces). Bare names silently no-op `claude plugin update`.
    OFFICIAL = {"claude-plugins-official", "claude-code-plugins"}
    known = OFFICIAL | set(data.get("extraKnownMarketplaces", {}).keys())
    for entry in data.get("plugins", []):
        if "@" not in entry:
            fail(f"plugins.json: '{entry}' is not a fully-qualified name@marketplace id")
            continue
        marketplace = entry.split("@", 1)[1]
        if marketplace not in known:
            fail(f"plugins.json: '{entry}' references unknown marketplace '{marketplace}'")
    ok(f"plugins[] entries: {n}")
    return n


def check_readme_counts(counts: dict[str, int]) -> None:
    """Assert every numeric count mention in the README matches the repo."""
    readme = (REPO / "README.md").read_text()
    # label in README -> computed key
    patterns = {
        r"(\d+)\s+modules": ("modules", counts["modules"]),
        r"(\d+)\s+slash commands": ("slash commands", counts["slash"]),
        r"(\d+)\s+auto-trigger skills": ("auto-trigger skills", counts["skills"]),
        r"(\d+)\s+plugins": ("plugins", counts["plugins"]),
    }
    for pat, (label, expected) in patterns.items():
        found = [int(m) for m in re.findall(pat, readme)]
        if not found:
            fail(f"README: no '{label}' count mention found (expected {expected})")
            continue
        bad = [v for v in found if v != expected]
        if bad:
            fail(f"README: '{label}' says {bad} but repo has {expected}")
        else:
            ok(f"README '{label}' count = {expected} ({len(found)} mention(s)) ✓")


def check_evals() -> None:
    eval_files = sorted(MODULES_DIR.glob("**/evals.json"))
    bad = 0
    for ef in eval_files:
        try:
            json.loads(ef.read_text())
        except json.JSONDecodeError as e:
            fail(f"{ef.relative_to(REPO)}: invalid JSON — {e}")
            bad += 1
    ok(f"evals.json validated: {len(eval_files)} file(s), {bad} invalid")


def check_version_triple(tag: str | None) -> None:
    version_file = (REPO / "VERSION").read_text().strip()
    pkg_version = json.loads((REPO / "package.json").read_text())["version"]
    if version_file != pkg_version:
        fail(f"version drift: VERSION={version_file} != package.json={pkg_version}")
    else:
        ok(f"VERSION == package.json == {version_file}")
    if tag:
        tag_version = tag.lstrip("v")
        if tag_version != version_file:
            fail(f"version drift: git tag={tag_version} != VERSION={version_file}")
        elif tag_version != pkg_version:
            fail(f"version drift: git tag={tag_version} != package.json={pkg_version}")
        else:
            ok(f"git tag matches version triple ({tag_version})")


def main() -> int:
    ap = argparse.ArgumentParser(description="100x-dev repo consistency checker")
    ap.add_argument("--tag", default=os.environ.get("TAG", ""),
                    help="git tag (e.g. v2.0.4) to include in the version-triple check")
    args = ap.parse_args()

    counts = check_modules()
    counts["plugins"] = check_plugins()
    check_readme_counts(counts)
    check_evals()
    check_version_triple(args.tag or None)

    print("\n".join(f"  ✓ {n}" for n in notes))
    if failures:
        print("\n".join(f"  ✗ {f}" for f in failures), file=sys.stderr)
        print(f"\nmeta-check: {len(failures)} failure(s)", file=sys.stderr)
        return 1
    print("\nmeta-check: all checks passed ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
