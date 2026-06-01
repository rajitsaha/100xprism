#!/usr/bin/env python3
"""PostToolUse(Write|Edit) — advisory lint-on-save (optional, opt-in).

After Claude writes or edits a file, run the project's linter on just that file if one
is available, and surface any complaints. This is ADVISORY: it always exits 0 so it
never blocks the edit — the blocking quality bar is /gate. Disabled by default; enable
it from install.sh's hooks menu.

Runs nothing if no matching linter is installed, so it's a no-op on minimal machines.
Set HOOK_LINT_ON_SAVE=off to silence at runtime.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from hooklib import event_cwd, read_event, tool_input  # noqa: E402


def _run(cmd: list[str], cwd: str) -> str:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (r.stdout + r.stderr).strip() if r.returncode != 0 else ""


def lint(path: str, cwd: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".py":
        if shutil.which("ruff"):
            return _run(["ruff", "check", path], cwd)
        return _run([sys.executable, "-m", "py_compile", path], cwd)
    if ext in (".js", ".jsx", ".ts", ".tsx"):
        if Path(cwd, "node_modules", ".bin", "eslint").exists():
            return _run([str(Path(cwd, "node_modules", ".bin", "eslint")), path], cwd)
    if ext in (".sh", ".bash") and shutil.which("shellcheck"):
        return _run(["shellcheck", path], cwd)
    return ""


def main() -> int:
    if os.environ.get("HOOK_LINT_ON_SAVE", "").lower() == "off":
        return 0
    event = read_event()
    path = tool_input(event).get("file_path", "")
    if not isinstance(path, str) or not path or not Path(path).exists():
        return 0
    out = lint(path, event_cwd(event))
    if out:
        sys.stderr.write(f"⚠ 100x-dev lint-on-save ({Path(path).name}):\n{out}\n")
    return 0  # advisory only — never block


if __name__ == "__main__":
    sys.exit(main())
