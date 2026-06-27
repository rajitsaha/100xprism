#!/usr/bin/env python3
"""Project-local Codex wrapper for first-party 100xprism hooks."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# This allowlist is intentionally baked into generated projects. The resolved
# install manifest must also contain the hook, but this prevents a later or
# tampered install from introducing a new executable into an already-trusted
# project hook config.
ALLOWED_HOOKS = __ALLOWED_HOOKS_JSON__


def _candidate_homes() -> list[Path]:
    candidates: list[Path] = []
    for name in ("DEV_100X_HOME", "HUNDRED_X_HOME"):
        value = os.environ.get(name)
        if value:
            candidates.append(Path(value).expanduser())

    home = os.environ.get("HOME")
    if home:
        candidates.append(Path(home).expanduser() / "100xprism")

    project_root = Path(os.environ.get("CODEX_PROJECT_ROOT", os.getcwd())).resolve()
    for parent in [project_root, *project_root.parents]:
        candidates.append(parent)

    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _manifest_scripts(root: Path):
    manifest = root / "hooks" / "hooks.manifest.json"
    try:
        data = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return {
        hook.get("script")
        for hook in data.get("hooks", [])
        if isinstance(hook, dict) and hook.get("script")
    }


def _resolve_hook(script: str):
    found_install = False
    for root in _candidate_homes():
        scripts = _manifest_scripts(root)
        if scripts is None:
            continue
        found_install = True
        hook = root / "hooks" / script
        if script in scripts and hook.is_file():
            return hook, found_install
    return None, found_install


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: run-hook.py <hook-script>", file=sys.stderr)
        return 2

    script = sys.argv[1]
    if script not in ALLOWED_HOOKS:
        print(f"100xprism Codex hook rejected unknown hook: {script}", file=sys.stderr)
        return 2

    hook, found_install = _resolve_hook(script)
    if hook is None:
        if found_install:
            print(
                f"100xprism Codex hook found a 100xprism install, but it does not provide hook {script}.\\n"
                "Update the generated Codex hooks or reinstall 100xprism, then retry.",
                file=sys.stderr,
            )
        else:
            print(
                "100xprism Codex hook could not find the 100xprism install.\\n"
                "Set DEV_100X_HOME=/path/to/100xprism or run `100xprism install`, then retry.",
                file=sys.stderr,
            )
        return 2

    return subprocess.run([sys.executable, str(hook)]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
