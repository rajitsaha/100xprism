#!/usr/bin/env python3
"""Single parser for modules/<slug>/SKILL.md.

Used by all adapters. Outputs JSON to stdout with one of these subcommands:

  list                       — full manifest (all modules, sorted by category/name)
  emit-concat <out> [limit]  — write concatenated core bodies + on-demand index;
                               if <limit> set (chars), fall back to index-only mode
                               when over budget.
  emit-cursor <project_dir>  — write .cursor/rules/<slug>.mdc per module
  emit-claude-code           — write ~/.claude/skills/<slug>/* + ~/.claude/commands/<slug>.md
  emit-hooks [--sync]        — idempotently merge first-party hooks into
                               ~/.claude/settings.json from hooks/hooks.manifest.json.
                               Each hook is enabled per its manifest `default`, overridable
                               by its `toggle_env` env var (1/true=on, 0/false=off).
                               --sync only refreshes hooks already present (used on update).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODULES_DIR = REPO / "modules"

# Central model-routing map. Modules declare a bare alias (`model: haiku`) in
# frontmatter. Claude Code reads that alias natively and auto-resolves it to the
# latest version — so the alias is emitted verbatim there (see cmd_emit_claude_code,
# which copies frontmatter as-is). Every other adapter picks its own (often
# non-Claude) model, so they receive a vendor-neutral reasoning-tier hint instead of
# a Claude model name. `id`/`label` are the single source of truth for the deferred
# slash-command / documentation work; the emitters in this pass use `tier_hint`.
MODEL_ALIASES = {
    "haiku":  {"id": "claude-haiku-4-5",  "label": "Claude Haiku 4.5",  "tier_hint": "fast / low-cost (mechanical task)"},
    "sonnet": {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6", "tier_hint": "balanced (moderate reasoning)"},
    "opus":   {"id": "claude-opus-4-8",   "label": "Claude Opus 4.8",   "tier_hint": "most capable (deep reasoning)"},
}


def tier_annotation(model: str) -> str:
    """Vendor-neutral routing hint for non-Claude adapters. '' for unknown/empty alias."""
    info = MODEL_ALIASES.get(model.strip())
    return f"_Suggested model tier: {info['tier_hint']}_" if info else ""


def short_description(desc: str) -> str:
    """First sentence of a description, capped at 140 chars — for tight rule triggering."""
    d = desc.split(". ", 1)[0]
    if len(d) > 140:
        d = d[:137] + "..."
    return d


def render_command_alias(fm: dict, slug: str, body: str) -> str:
    """Slash-command alias that mirrors the skill's routing/guardrails in frontmatter.

    Commands and skills share a frontmatter schema, so the alias carries description,
    model routing, and allowed-tools instead of dropping them. argument-hint is emitted
    generically only when the body actually consumes positional input.
    """
    lines = ["---", f"description: {short_description(fm.get('description', ''))}"]
    model = fm.get("model", "").strip()
    if model:
        lines.append(f"model: {model}")
    allowed = fm.get("allowed-tools", "").strip()
    if allowed:
        lines.append(f"allowed-tools: {allowed}")
    if "$ARGUMENTS" in body or "$1" in body:
        lines.append("argument-hint: [arguments]")
    lines += ["---", "", f"Use the `{fm.get('name', slug)}` skill.", "", "$ARGUMENTS", ""]
    return "\n".join(lines)


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    fm_block = text[4:end]
    body = text[end + 5 :]
    fm: dict[str, str] = {}
    current_key: str | None = None
    for line in fm_block.splitlines():
        if not line.strip():
            continue
        if line.startswith(" ") and current_key:
            fm[current_key] = (fm[current_key] + " " + line.strip()).strip()
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            current_key = key.strip()
            fm[current_key] = val.strip()
    return fm, body


def list_modules() -> list[dict]:
    out: list[dict] = []
    for skill_md in sorted(MODULES_DIR.glob("*/SKILL.md")):
        fm, body = split_frontmatter(skill_md.read_text())
        out.append({
            "slug": skill_md.parent.name,
            "name": fm.get("name", skill_md.parent.name),
            "description": fm.get("description", ""),
            "category": fm.get("category", "uncategorized"),
            "tier": fm.get("tier", "on-demand"),
            "slash_command": fm.get("slash_command", ""),
            "allowed_tools": fm.get("allowed-tools", ""),
            "model": fm.get("model", ""),
            "body": body,
            "dir": str(skill_md.parent),
        })
    out.sort(key=lambda m: (m["tier"] != "core", m["category"], m["slug"]))
    return out


CATEGORY_ORDER = [
    "lifecycle", "quality", "engineering", "data", "design", "docs", "marketing", "uncategorized"
]


def category_sort_key(c: str) -> int:
    try:
        return CATEGORY_ORDER.index(c)
    except ValueError:
        return len(CATEGORY_ORDER)


def emit_index(modules: list[dict], indent: str = "") -> str:
    """One-line-per-module index, grouped by category. Used for on-demand listing."""
    lines: list[str] = []
    by_cat: dict[str, list[dict]] = {}
    for m in modules:
        by_cat.setdefault(m["category"], []).append(m)
    for cat in sorted(by_cat.keys(), key=category_sort_key):
        lines.append(f"{indent}**{cat.title()}** ({len(by_cat[cat])}):")
        for m in by_cat[cat]:
            slash = f" `{m['slash_command']}`" if m["slash_command"] else ""
            d = short_description(m["description"])
            tier = tier_annotation(m.get("model", ""))
            tier_suffix = f" {tier}" if tier else ""
            lines.append(f"{indent}- `{m['slug']}`{slash} — {d}{tier_suffix}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


HEADER = (
    "# 100x Dev — Modules\n"
    "# Generated by 100x-dev (https://github.com/rajitsaha/100x-dev)\n"
    "# Source of truth: modules/<slug>/SKILL.md. Edit there and regenerate.\n\n"
)


def render_concat(modules: list[dict]) -> str:
    """Full concat: inline core module bodies + index of on-demand modules."""
    out = [HEADER]
    core = [m for m in modules if m["tier"] == "core"]
    on_demand = [m for m in modules if m["tier"] != "core"]

    out.append("## Core modules (always-on)\n")
    for m in sorted(core, key=lambda x: (category_sort_key(x["category"]), x["slug"])):
        out.append(f"---\n\n## {m['slug']}\n")
        if m["slash_command"]:
            out.append(f"_Slash command: `{m['slash_command']}`_\n")
        tier = tier_annotation(m.get("model", ""))
        if tier:
            out.append(f"{tier}\n")
        out.append(m["body"].lstrip("\n").rstrip() + "\n\n")

    out.append("---\n\n## On-demand modules (invoke by name)\n\n")
    out.append(
        "These load only when triggered. Ask Claude to use the relevant module by "
        "name when the situation matches.\n\n"
    )
    out.append(emit_index(on_demand))
    return "".join(out)


def render_index_only(modules: list[dict]) -> str:
    """Index-only: every module gets one line. Used when over byte budget (Windsurf)."""
    out = [HEADER, "## Modules available\n\n"]
    out.append(
        "100x-dev installs modules in your global config (`~/.claude/skills/` or "
        "`.cursor/rules/`). When the user's request matches one of these, use it.\n\n"
    )
    out.append(emit_index(modules))
    return "".join(out)


def cmd_list():
    mods = list_modules()
    print(json.dumps(mods, indent=2))


def cmd_emit_concat(out_path: str, mode: str = "concat"):
    """mode: 'concat' (core bodies + on-demand index) or 'index' (one-liners only)."""
    mods = list_modules()
    if mode == "index":
        text = render_index_only(mods)
    else:
        text = render_concat(mods)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(text)
    print(f"wrote {out_path} ({len(text)} bytes, mode={mode})")


def cmd_emit_cursor(project_dir: str):
    rules_dir = Path(project_dir) / ".cursor" / "rules"
    if rules_dir.exists():
        # remove only files we previously wrote (those with our generation marker)
        for f in rules_dir.glob("*.mdc"):
            try:
                first = f.read_text().splitlines()[0:6]
                if any("100x-dev" in line for line in first):
                    f.unlink()
            except OSError:
                pass
    rules_dir.mkdir(parents=True, exist_ok=True)
    mods = list_modules()
    for m in mods:
        # Map tier to Cursor rule type: core modules are always in context;
        # on-demand modules are agent-requested via a tight first-sentence description.
        always_apply = "true" if m["tier"] == "core" else "false"
        cursor_fm = [
            "---",
            f"description: {short_description(m['description'])}",
            "globs:",
            f"alwaysApply: {always_apply}",
            "---",
            "",
            f"<!-- generated by 100x-dev from modules/{m['slug']}/SKILL.md -->",
            "",
        ]
        tier = tier_annotation(m.get("model", ""))
        if tier:
            cursor_fm += [tier, ""]
        text = "\n".join(cursor_fm) + m["body"].lstrip("\n")
        (rules_dir / f"{m['slug']}.mdc").write_text(text)
    print(f"wrote {len(mods)} files to {rules_dir}")


def cmd_emit_claude_code():
    home = Path(os.environ.get("HOME", str(Path.home())))
    skills_dir = home / ".claude" / "skills"
    commands_dir = home / ".claude" / "commands"
    skills_dir.mkdir(parents=True, exist_ok=True)
    commands_dir.mkdir(parents=True, exist_ok=True)

    skill_count = 0
    cmd_count = 0
    for module_dir in sorted(MODULES_DIR.glob("*")):
        if not module_dir.is_dir():
            continue
        slug = module_dir.name
        target = skills_dir / slug
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(module_dir, target)
        skill_count += 1

        # Slash command alias for any module with slash_command in frontmatter
        skill_md = target / "SKILL.md"
        fm, body = split_frontmatter(skill_md.read_text())
        slash = fm.get("slash_command", "").lstrip("/")
        if slash:
            (commands_dir / f"{slash}.md").write_text(render_command_alias(fm, slug, body))
            cmd_count += 1

    print(f"wrote {skill_count} skills + {cmd_count} slash command aliases")


HOOKS_DIR = REPO / "hooks"


def _hook_command(script: str) -> str:
    return f'python3 "{HOOKS_DIR / script}"'


def _hook_enabled(hook: dict) -> bool:
    """manifest `default`, overridden by the hook's `toggle_env` env var if set."""
    env = hook.get("toggle_env")
    if env and env in os.environ:
        val = os.environ[env].strip().lower()
        if val in ("1", "true", "yes", "on"):
            return True
        if val in ("0", "false", "no", "off", ""):
            return False
    return bool(hook.get("default"))


def _command_present(entries: list, command: str) -> bool:
    return any(
        h.get("command") == command
        for e in entries
        for h in e.get("hooks", [])
    )


def _strip_command(entries: list, command: str) -> list:
    """Remove our command from every entry; drop entries left with no hooks."""
    out = []
    for e in entries:
        kept = [h for h in e.get("hooks", []) if h.get("command") != command]
        if kept:
            out.append({**e, "hooks": kept})
    return out


def cmd_emit_hooks(sync: bool = False):
    """Idempotently merge first-party hooks into settings.json (declarative).

    For each manifest hook we strip any existing copy of its command, then re-add a
    single entry when it should be enabled — so re-running never duplicates entries and
    flipping a toggle off removes the hook. `sync` keeps only hooks already present
    (used on update, so we never silently enable a hook the user opted out of).
    """
    manifest = json.loads((HOOKS_DIR / "hooks.manifest.json").read_text())
    hooks_spec = manifest.get("hooks", [])

    settings_file = Path(
        os.environ.get("SETTINGS_FILE")
        or os.path.expanduser("~/.claude/settings.json")
    )
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        settings = json.loads(settings_file.read_text())
    except (OSError, json.JSONDecodeError):
        settings = {}

    settings_hooks = settings.setdefault("hooks", {})
    added, kept, removed = [], [], []

    for hook in hooks_spec:
        command = _hook_command(hook["script"])
        event = hook["event"]
        entries = settings_hooks.get(event, [])
        was_present = _command_present(entries, command)
        entries = _strip_command(entries, command)  # dedupe / declarative reset

        keep = was_present if sync else _hook_enabled(hook)
        if keep:
            entries.append({
                "matcher": hook["matcher"],
                "hooks": [{"type": "command", "command": command}],
            })
            (kept if was_present else added).append(hook["id"])
        elif was_present:
            removed.append(hook["id"])

        if entries:
            settings_hooks[event] = entries
        elif event in settings_hooks:
            del settings_hooks[event]

    settings_file.write_text(json.dumps(settings, indent=2))

    parts = []
    if added:
        parts.append(f"added {len(added)} ({', '.join(added)})")
    if kept:
        parts.append(f"refreshed {len(kept)}")
    if removed:
        parts.append(f"removed {len(removed)} ({', '.join(removed)})")
    print(f"hooks: {'; '.join(parts) if parts else 'no changes'} → {settings_file}")


def main(argv: list[str]):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd == "list":
        cmd_list()
    elif cmd == "emit-concat":
        cmd_emit_concat(argv[2], argv[3] if len(argv) > 3 else "")
    elif cmd == "emit-cursor":
        cmd_emit_cursor(argv[2])
    elif cmd == "emit-claude-code":
        cmd_emit_claude_code()
    elif cmd == "emit-hooks":
        cmd_emit_hooks(sync="--sync" in argv[2:])
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
