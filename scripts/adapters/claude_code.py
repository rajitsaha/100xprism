"""Claude Code cost adapter — the only adapter with real per-message token data."""
from . import Usage  # type: ignore  # set after __init__ defines Usage

TOOL = "claude-code"


def iter_dir_days(file_summaries):
    """Yield one Usage per (transcript dir, day) from already-parsed transcript
    summaries (build()'s cache values). Keeps a single transcript parser."""
    for s in file_summaries:
        projdir = s.get("projdir") or ""
        for day, d in (s.get("by_day") or {}).items():
            yield Usage(projdir, day, d.get("input", 0), d.get("output", 0),
                        d.get("cache_read", 0), d.get("cache_write", 0), TOOL)
