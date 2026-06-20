#!/usr/bin/env python3
"""Reconcile ~/.claude/settings.json with plugins/plugins.json.

Unlike a naive add-only sync, this ADDS newly-declared plugins and REMOVES ones
100x-dev previously installed but has since dropped from plugins.json (e.g. a
deduplicated/removed plugin) — without ever touching plugins the user enabled
themselves, and without flipping an entry the user explicitly turned on/off.

"Managed" plugins (the set 100x-dev owns) are tracked in a sidecar state file so
settings.json stays clean. On the very first run (no state yet) the managed set
is seeded from the current intersection of declared ∧ enabled, so nothing is
removed until a subsequent run observes an actual drop.

Usage:
  sync_plugins.py --settings <settings.json> --plugins <plugins.json>
                  [--state <state.json>] [--session-hook <command>]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", required=True)
    ap.add_argument("--plugins", required=True)
    ap.add_argument("--state", default="")
    ap.add_argument("--session-hook", default="")
    args = ap.parse_args()

    settings_file = Path(args.settings)
    # State lives beside settings.json so it tracks that specific install (and so
    # tests against a temp settings file stay self-contained).
    state_file = Path(args.state) if args.state else settings_file.parent / ".100x-dev-plugins.json"
    repo_data = _load(Path(args.plugins), {})
    settings = _load(settings_file, {})
    if not isinstance(settings, dict):
        settings = {}

    desired = list(repo_data.get("plugins", []))
    desired_set = set(desired)
    enabled = settings.setdefault("enabledPlugins", {})

    state = _load(state_file, {})
    first_run = "managed" not in state
    managed = set(state.get("managed", []))
    if first_run:
        # Seed: only claim plugins we can see are both declared and already enabled.
        # Nothing is removed on this run.
        managed = {p for p in desired if p in enabled}

    added = 0
    for p in desired:
        if p not in enabled:          # never flip an existing True/False
            enabled[p] = True
            added += 1

    removed = []
    for p in sorted(managed):
        if p not in desired_set:       # we installed it before; it's gone now
            if enabled.pop(p, None) is not None:
                removed.append(p)

    # We now own exactly the declared set.
    new_state = {"managed": sorted(desired_set)}

    # Merge marketplaces (additive — never drop a marketplace the user may rely on).
    extra = repo_data.get("extraKnownMarketplaces", {})
    if extra:
        settings.setdefault("extraKnownMarketplaces", {}).update(extra)

    # Optionally ensure the SessionStart update-check hook is present (idempotent).
    if args.session_hook:
        hooks = settings.setdefault("hooks", {})
        session_start = hooks.setdefault("SessionStart", [])
        present = any(
            h.get("command") == args.session_hook
            for entry in session_start
            for h in entry.get("hooks", [])
        )
        if not present:
            session_start.append({"matcher": "", "hooks": [{"type": "command", "command": args.session_hook}]})
            print("  Added SessionStart update-check hook ✓")

    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(settings, indent=2) + "\n")
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(new_state, indent=2) + "\n")

    bits = []
    if added:
        bits.append(f"added {added}")
    if removed:
        bits.append(f"removed {len(removed)} ({', '.join(removed)})")
    if bits:
        print(f"  Plugins: {', '.join(bits)} ✓")
    else:
        print("  Plugins: settings already up to date ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
