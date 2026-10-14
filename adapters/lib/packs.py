#!/usr/bin/env python3
"""Manage optional third-party skill packs declared in packs/packs.json.

Packs are opt-in. Nothing here installs anything unless the user explicitly runs
`add`, and detection is strictly read-only: it reports which declared packs look
relevant to the current project and stops there.

Ownership is tracked per config entry, not per platform — see the state file shape
in https://github.com/rajitsaha/100xprism/blob/131564c/docs/superpowers/plans/2026-08-03-skill-packs.md
(archived). If the user already had a plugin listed, we did not add it and will
never remove it.

Subcommands:
  detect  — which declared packs match the current project (read-only)
  status  — every declared pack, its detection result, and its install state
  add     — install a pack
  remove  — reverse what we recorded inserting
  sync    — re-apply opted-in packs; drop packs no longer declared
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping

REPO = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = REPO / "packs" / "packs.json"
PLATFORMS = ("claude-code", "codex", "cursor")


def load_registry(path: Path) -> dict:
    data = json.loads(path.read_text())
    if data.get("schema") != 1:
        raise SystemExit(f"packs.py: unsupported registry schema {data.get('schema')!r}")
    return data.get("packs", {})


def _load_json_object(path: Path, label: str) -> dict:
    """Read a JSON object, or abort. Absent is fine; present-but-unusable is not.

    Both callers write their file back, so collapsing an unreadable file to {} would
    destroy it. That applies to the pack state as much as to settings.json: the state
    file is the only record of what we may remove, and losing it orphans every entry
    we installed.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"packs.py: refusing to rewrite {label} at {path} — it exists but could not "
            f"be read ({exc}). Fix or move the file, then retry."
        )
    if not isinstance(data, dict):
        # Valid JSON of the wrong shape (a list, null, a bare string) is still
        # somebody's file. Never silently replace it with an object.
        raise SystemExit(
            f"packs.py: refusing to rewrite {label} at {path} — expected a JSON object, "
            f"found {type(data).__name__}. Fix or move the file, then retry."
        )
    return data


def load_state(path: Path) -> dict:
    """Our sidecar record of what we installed and may therefore remove.

    Strict, because every caller writes it back. Read-only commands use
    peek_state instead — reporting should not fail over a damaged sidecar.

    Nested shapes are validated too, and for the same reason lib/uninstall.js does it:
    `owned.plugins` holding a string would be iterated character by character, popping
    single-letter keys out of the user's enabledPlugins. A record we cannot trust is
    never acted on and never overwritten.
    """
    state = _load_json_object(path, "the pack state file")

    def reject(detail: str):
        raise SystemExit(
            f"packs.py: refusing to act on the pack state file at {path} — {detail}. "
            "Fix or move the file, then retry."
        )

    packs = state.get("packs", {})
    if not isinstance(packs, dict):
        reject(f"'packs' should be an object, found {type(packs).__name__}")
    for slug, entry in packs.items():
        if not isinstance(entry, dict):
            reject(f"the record for '{slug}' should be an object, found {type(entry).__name__}")
        owned = entry.get("owned", {})
        if not isinstance(owned, dict):
            reject(f"'{slug}.owned' should be an object, found {type(owned).__name__}")
        plugins = owned.get("plugins", [])
        if not isinstance(plugins, list) or any(not isinstance(p, str) for p in plugins):
            # Elements matter as much as the container: a non-string entry is either an
            # unhashable dict key (a crash) or a coerced key that could match something
            # the user owns.
            reject(f"'{slug}.owned.plugins' should be a list of strings")
        marketplace = owned.get("marketplace")
        if marketplace is not None and not isinstance(marketplace, str):
            reject(f"'{slug}.owned.marketplace' should be a string, found {type(marketplace).__name__}")
        platforms = entry.get("platforms", {})
        if not isinstance(platforms, dict):
            reject(f"'{slug}.platforms' should be an object, found {type(platforms).__name__}")
        for platform, value in platforms.items():
            # An unrecognised value would normalise to "no obligation", silently
            # discarding a recorded mutation and then deleting the record with it.
            values = [value] if isinstance(value, str) else value
            if not isinstance(values, list):
                reject(f"'{slug}.platforms.{platform}' should be a string or list, "
                       f"found {type(value).__name__}")
            unknown = [v for v in values if v not in OBLIGATIONS]
            if unknown:
                reject(f"'{slug}.platforms.{platform}' has unrecognised obligation(s) {unknown}; "
                       f"expected some of {list(OBLIGATIONS)}")

        uninstall = entry.get("uninstall", {})
        if not isinstance(uninstall, dict):
            reject(f"'{slug}.uninstall' should be an object, found {type(uninstall).__name__}")
        for platform, commands in uninstall.items():
            # A string here would be iterated into single characters, each then run as
            # a shell command.
            if not isinstance(commands, list) or any(not isinstance(c, str) for c in commands):
                reject(f"'{slug}.uninstall.{platform}' should be a list of command strings")
    return state


def peek_state(path: Path) -> dict:
    """Best-effort read for display only. Never feeds a write."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_settings(path: Path) -> dict:
    """The user's settings.json."""
    return _load_json_object(path, "settings.json")


def write_json(path: Path, data: dict) -> None:
    """Write atomically: a crash mid-write must not truncate the user's settings.

    Note the ORDER callers use — settings first, then the state sidecar. If the process
    dies between the two, we have inserted config without recording ownership: a leak,
    which is recoverable by hand. The reverse order would leave a record claiming we own
    entries we never inserted, and removal would then delete the user's own config.
    Leak over wrongful deletion, always.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    try:
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)  # atomic within a filesystem
    finally:
        if tmp.exists():
            tmp.unlink()


EMPTY_OWNED = {"plugins": [], "marketplace": None}


def merge_owned(a: dict, b: dict) -> dict:
    """Union two ownership records; a non-null marketplace wins over null."""
    plugins = list(dict.fromkeys(list(a.get("plugins", [])) + list(b.get("plugins", []))))
    return {"plugins": plugins, "marketplace": a.get("marketplace") or b.get("marketplace")}


# Obligations are a SET, not a scale. `installed` and `cli` are independent mutations —
# we inserted settings entries, and separately the pack's own CLI wrote files we do not
# track. A platform can carry both, so ranking them and keeping the winner would silently
# drop one. `unavailable` is the absence of obligation and is never stored.
OBLIGATIONS = ("installed", "cli", "manual")


def as_obligations(value) -> list[str]:
    """Normalise a stored status to a list. Accepts the legacy single-string form."""
    if isinstance(value, str):
        return [value] if value in OBLIGATIONS else []
    if isinstance(value, list):
        return [v for v in value if v in OBLIGATIONS]
    return []


def merge_platforms(previous: dict, current: dict) -> dict:
    """Union of obligations per platform. A retry can only ever add obligation."""
    merged: dict[str, list[str]] = {}
    for source in (previous, current):
        for platform, value in (source or {}).items():
            existing = merged.get(platform, [])
            merged[platform] = existing + [o for o in as_obligations(value) if o not in existing]
    # Drop platforms that ended up owing nothing, so `unavailable` never persists as a
    # phantom entry that later reads have to special-case.
    return {p: obligations for p, obligations in merged.items() if obligations}


def claude_install(pack: dict, settings: dict) -> dict | None:
    """Add the pack's marketplace + plugins. Returns ONLY what this call inserted.

    An entry that already exists — an enabled plugin, an explicitly disabled one, or a
    marketplace the user configured — is left exactly as-is and is NOT claimed. That
    record is what makes removal safe.
    """
    block = pack.get("install", {}).get("claude-code")
    if not block:
        return None

    owned = {"plugins": [], "marketplace": None}

    marketplace = block.get("marketplace")
    if marketplace:
        marketplaces = settings.setdefault("extraKnownMarketplaces", {})
        if marketplace["name"] not in marketplaces:
            marketplaces[marketplace["name"]] = {"source": marketplace["source"]}
            owned["marketplace"] = marketplace["name"]

    enabled = settings.setdefault("enabledPlugins", {})
    for plugin in block.get("plugins", []):
        if plugin not in enabled:
            enabled[plugin] = True
            owned["plugins"].append(plugin)

    return owned


def claude_remove(owned: dict, settings: dict) -> list[str]:
    """Reverse an ownership record. Never consults the registry — a pack dropped from
    packs.json must still be removable."""
    enabled = settings.setdefault("enabledPlugins", {})
    removed = [p for p in owned.get("plugins", []) if enabled.pop(p, None) is not None]

    name = owned.get("marketplace")
    if name and not any(p.rsplit("@", 1)[-1] == name for p in enabled):
        settings.get("extraKnownMarketplaces", {}).pop(name, None)
    return removed


def which(binary: str) -> bool:
    """Is `binary` on PATH? PRISM_PACKS_WHICH overrides the answer, for tests."""
    override = os.environ.get("PRISM_PACKS_WHICH")
    if override:
        try:
            return bool(json.loads(override).get(binary, False))
        except ValueError:
            pass
    return shutil.which(binary) is not None


def run_command(command: str) -> tuple[int, str]:
    """Run a declared install/uninstall command.

    When PRISM_PACKS_RUNNER_LOG is set the command is recorded and NOT executed —
    this is how tests exercise install paths without touching the network.
    """
    log = os.environ.get("PRISM_PACKS_RUNNER_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(command + "\n")
        # PRISM_PACKS_RUNNER_FAIL names one command that should report failure, so
        # tests can exercise partial-install and failed-uninstall paths.
        if os.environ.get("PRISM_PACKS_RUNNER_FAIL") == command:
            return 1, "simulated failure"
        return 0, ""
    proc = subprocess.run(shlex.split(command), capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stderr or proc.stdout).strip()


def install_pack(pack: dict, settings: dict, messages: list[str]) -> tuple[dict[str, str], dict]:
    """Install a pack, preferring the upstream's own multi-agent CLI.

    Returns ({platform: status}, ownership record), where status is one of:
      installed   — 100xprism performed it and can reverse it
      cli         — the pack's own CLI did it; not ours to reverse
      manual      — the user must run a command themselves
      unavailable — no usable path for that platform
    """
    install = pack.get("install", {})
    platforms: dict[str, str] = {}
    owned = dict(EMPTY_OWNED)
    cli = install.get("cli")

    # 1. The upstream CLI, when present, covers every platform in one command.
    if install.get("preferred") == "cli" and cli and which(cli["requires"]):
        code, err = run_command(cli["command"])
        if code == 0:
            for platform in cli.get("covers", []):
                platforms[platform] = ["cli"]
            messages.append(f"ran `{cli['command']}` — covers {', '.join(cli.get('covers', []))}")
            return platforms, owned
        messages.append(f"`{cli['command']}` failed ({err or f'exit {code}'}); falling back per platform")

    # 2. Per-platform blocks.
    for platform in PLATFORMS:
        block = install.get(platform)
        if not block:
            platforms[platform] = []
            continue

        if platform == "claude-code":
            claimed = claude_install(pack, settings)
            if claimed is None:
                platforms[platform] = []
            else:
                platforms[platform] = ["installed"]
                owned = merge_owned(owned, claimed)
            continue

        if block.get("commands"):
            binary = block["commands"][0].split()[0]
            if not which(binary):
                platforms[platform] = []
                continue
            # A command that already succeeded has mutated the user's system. Once that
            # happens the platform must be recorded `installed` even if a later command
            # fails — `unavailable` carries no removal transition, which would strand
            # the completed mutation with nothing able to reverse it.
            ran_any = False
            failed_at = ""
            for command in block["commands"]:
                code, err = run_command(command)
                if code != 0:
                    messages.append(f"{platform}: `{command}` failed ({err or f'exit {code}'})")
                    failed_at = command
                    break
                ran_any = True
            if failed_at and ran_any:
                platforms[platform] = ["installed"]
                messages.append(
                    f"{platform}: partially installed — earlier steps succeeded before "
                    f"`{failed_at}` failed. `/pack remove` will reverse what it can."
                )
            elif failed_at:
                platforms[platform] = []
            else:
                platforms[platform] = ["installed"]
            continue

        if block.get("manual"):
            platforms[platform] = ["manual"]
            messages.append(f"{platform}: run this in your agent yourself — {', '.join(block['manual'])}")

    # 3. Nothing worked for some platform: surface the pack's own hint.
    if cli and cli.get("hint") and any(not v for v in platforms.values()):
        messages.append(cli["hint"])
    return platforms, owned


def remove_pack(entry: dict, settings: dict, messages: list[str]) -> bool:
    """Reverse a pack from its recorded state. Registry-independent by design.

    Every recorded status has a transition here — a shell-installed platform is never
    silently forgotten, even when the pack declares no inverse command.

    Returns False when a declared inverse command failed. The caller must then KEEP the
    state entry: the installation is still there, and discarding the record would leave
    it with nothing able to remove it.
    """
    platforms = {p: as_obligations(v) for p, v in (entry.get("platforms") or {}).items()}
    declared = entry.get("uninstall") or {}
    ok = True

    # Reverse our own config first, driven by the OWNERSHIP RECORD rather than by the
    # platform label. `owned` is the truth of what we inserted; a status can legitimately
    # change across re-adds (a later CLI install marks claude-code `cli`), and keying off
    # the label would skip the reversal and orphan entries we put there.
    owned = entry.get("owned", EMPTY_OWNED)
    if owned.get("plugins") or owned.get("marketplace"):
        claude_remove(owned, settings)
        # Checkpoint immediately: if a later platform fails and the user retries, we must
        # not "reverse" these a second time — by then the user may have re-added them.
        entry["owned"] = dict(EMPTY_OWNED)
    entry["platforms"] = platforms

    # Each platform can owe several independent obligations; discharge them one at a
    # time and drop each from the record as it completes, so a retry resumes rather
    # than repeating.
    for platform, obligations in sorted(platforms.items()):
        for how in list(obligations):
            if how == "installed" and platform == "claude-code":
                # The one platform we install in-process; the ownership reversal above
                # already discharged it. Any other obligation it carries still applies.
                obligations.remove(how)
                continue

            if how == "installed":
                remaining = list(declared.get(platform) or [])
                if not remaining:
                    messages.append(
                        f"{platform}: 100xprism ran the upstream installer, but this pack declares "
                        "no uninstall command — remove it with the upstream tooling. Skill files on "
                        "disk were left untouched."
                    )
                    obligations.remove(how)
                    continue
                failed = False
                for command in list(remaining):
                    code, err = run_command(command)
                    if code != 0:
                        messages.append(
                            f"{platform}: `{command}` failed ({err or f'exit {code}'}) — keeping the "
                            "pack record so you can retry."
                        )
                        failed = True
                        ok = False
                        break
                    # Checkpoint per command: a retry must not re-run what already worked.
                    remaining.remove(command)
                declared[platform] = remaining
                if not failed:
                    obligations.remove(how)
            else:  # cli / manual — not ours to reverse, but the user must be told.
                messages.append(
                    f"{platform}: installed outside 100xprism ({how}) — remove it with the "
                    "upstream tooling. Skill files on disk were left untouched."
                )
                obligations.remove(how)

    entry["platforms"] = {p: o for p, o in platforms.items() if o}
    entry["uninstall"] = declared
    return ok


def project_root(start: Path) -> Path:
    """Git toplevel of `start`, or `start` itself when not in a repository."""
    try:
        r = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return start
    if r.returncode == 0 and r.stdout.strip():
        return Path(r.stdout.strip())
    return start


def pack_matches(pack: dict, root: Path, env: Mapping[str, str]) -> bool:
    """OR over files / env / contains. Root-only: never descends into subdirectories."""
    detect = pack.get("detect", {})
    for name in detect.get("files", []):
        if (root / name).is_file():
            return True
    for var in detect.get("env", []):
        if env.get(var):
            return True
    for entry in detect.get("contains", []):
        target = root / entry["file"]
        if not target.is_file():
            continue
        try:
            text = target.read_text(errors="replace")
        except OSError:
            continue
        if re.search(entry["pattern"], text, re.MULTILINE):
            return True
    return False


def settings_path(args) -> Path:
    return Path(args.settings) if args.settings else Path.home() / ".claude" / "settings.json"


def state_path(args) -> Path:
    if args.state:
        return Path(args.state)
    return settings_path(args).parent / ".100xprism-packs.json"


def describe(packs: dict, root: Path, env: Mapping[str, str], state: dict) -> list[dict]:
    installed = state.get("packs", {})
    return [
        {
            "slug": slug,
            "title": pack.get("title", slug),
            "description": pack.get("description", ""),
            "source": pack.get("source", ""),
            "detected": pack_matches(pack, root, env),
            "platforms": installed.get(slug, {}).get("platforms", {}),
        }
        for slug, pack in sorted(packs.items())
    ]


def render(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        if row["platforms"]:
            # Don't label the row "installed" wholesale — a platform may owe only a
            # `manual` step the user still has to take. Obligations are a list, so join
            # them: a bare f-string would print Python's list repr to the terminal.
            note = "per platform: " + ", ".join(
                f"{platform}={'+'.join(as_obligations(value))}"
                for platform, value in sorted(row["platforms"].items())
                if as_obligations(value)
            )
        elif row["detected"]:
            note = f"detected here — run `/pack add {row['slug']}` to install"
        else:
            note = "not installed"
        lines.append(f"  {row['slug']:<14} {row['title']}\n    {note}")
    # Empty means empty. install.sh/update.sh test this output for non-emptiness before
    # printing a suggestions header — a placeholder string would show that header on
    # every run, including the overwhelmingly common no-match case.
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="packs.py")
    ap.add_argument("command", choices=["detect", "status", "add", "remove", "sync"])
    ap.add_argument("slug", nargs="?", default="")
    ap.add_argument("--packs", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--project", default=".")
    ap.add_argument("--settings", default="")
    ap.add_argument("--state", default="")
    ap.add_argument("--json", action="store_true")
    return ap


def main() -> int:
    args = build_parser().parse_args()
    packs = load_registry(Path(args.packs))

    if args.command in ("detect", "status"):
        # Read-only: tolerate a damaged sidecar rather than refuse to report.
        root = project_root(Path(args.project).resolve())
        rows = describe(packs, root, os.environ, peek_state(state_path(args)))
        if args.command == "detect":
            rows = [r for r in rows if r["detected"]]
        if args.json:
            print(json.dumps({"packs": rows}, indent=2))
        else:
            rendered = render(rows)
            if rendered:
                print(rendered)
            elif args.command == "status":
                print("  No packs installed. Run `/pack` after adding one to packs.json.")
        return 0

    state = load_state(state_path(args))

    settings_file = settings_path(args)
    settings = load_settings(settings_file)
    state.setdefault("schema", 1)
    installed = state.setdefault("packs", {})
    messages: list[str] = []

    if args.command == "add":
        if args.slug not in packs:
            print(f"packs.py: unknown pack '{args.slug}'", file=sys.stderr)
            return 1
        platforms, owned = install_pack(packs[args.slug], settings, messages)
        previous = installed.get(args.slug, {})
        merged_platforms = merge_platforms(previous.get("platforms", {}), platforms)
        merged_owned = merge_owned(previous.get("owned", EMPTY_OWNED), owned)

        # Nothing installed, nothing owed, nothing owned — recording state here would
        # claim an install that never happened and leave `/pack` reporting it as present.
        nothing_happened = (
            not merged_owned.get("plugins")
            and not merged_owned.get("marketplace")
            and not any(merged_platforms.values())
        )
        if nothing_happened:
            for line in messages:
                print(f"  {line}", file=sys.stderr)
            print(f"packs.py: '{args.slug}' could not be installed on any platform", file=sys.stderr)
            return 1

        # Inverse commands are copied from the registry so removal survives the pack
        # being dropped — but only for platforms THIS add actually reinstalled. A
        # previous removal may have checkpointed a shortened list after a mid-array
        # failure; restoring the full list for a platform we did not touch would make a
        # later removal re-run commands that already succeeded.
        declared = dict(previous.get("uninstall") or {})
        for platform in PLATFORMS:
            reinstalled = "installed" in as_obligations(platforms.get(platform))
            if reinstalled or platform not in declared:
                block = packs[args.slug].get("install", {}).get(platform) or {}
                declared[platform] = list(block.get("uninstall", []))

        installed[args.slug] = {
            # Union of obligations: a retry can add obligation but never drop one, so a
            # mutation recorded by an earlier attempt keeps its removal transition.
            "platforms": merged_platforms,
            # Union with any prior record so a second `add` — which inserts nothing —
            # cannot erase what the first one claimed.
            "owned": merged_owned,
            "uninstall": declared,
        }

    elif args.command == "remove":
        entry = installed.get(args.slug)
        if entry is None:
            print(f"packs.py: pack '{args.slug}' is not installed", file=sys.stderr)
            return 1
        removed_cleanly = remove_pack(entry, settings, messages)
        if removed_cleanly:
            installed.pop(args.slug, None)
        else:
            write_json(settings_file, settings)
            write_json(state_path(args), state)
            for line in messages:
                print(f"  {line}", file=sys.stderr)
            return 1

    elif args.command == "sync":
        for slug in sorted(installed):
            entry = installed[slug]
            if slug not in packs:
                # Same removal path as an explicit `/pack remove`: a dropped pack still
                # has non-Claude obligations, and reporting "removed" without running
                # them would be a lie.
                if remove_pack(entry, settings, messages):
                    installed.pop(slug, None)
                    messages.append(f"{slug}: no longer declared — reversed what 100xprism owned")
                else:
                    messages.append(f"{slug}: no longer declared, but removal failed — record kept")
            elif "installed" in as_obligations((entry.get("platforms") or {}).get("claude-code")):
                owned = claude_install(packs[slug], settings)
                if owned:
                    entry["owned"] = merge_owned(entry.get("owned", EMPTY_OWNED), owned)

    write_json(settings_file, settings)
    write_json(state_path(args), state)
    if args.json:
        print(json.dumps({"pack": args.slug, "messages": messages}, indent=2))
    else:
        for line in messages:
            print(f"  {line}")
        # `sync` runs on every install/update, so it stays silent unless it actually
        # did something. Only an explicit add/remove always warrants the notice.
        if args.command in ("add", "remove") or messages:
            print("  Restart your agent to pick up the change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
