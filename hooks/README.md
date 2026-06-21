# 100xprism — First-party enforcing hooks

These hooks turn the repo's headline guarantees from prose into enforcement. They are
**opt-in**: `install.sh` offers a *Hooks* component with a per-hook toggle, and you can
re-run the installer (or `100xprism update`) any time to change the selection. Nothing
here runs unless you enable it.

All hooks are plain `python3` scripts (no third-party deps) that read the Claude Code
hook event from stdin. They live at `~/100xprism/hooks/` after install and are wired into
`~/.claude/settings.json` by the generator command `python3 adapters/lib/modules.py
emit-hooks` — the same idempotent-merge pattern used for plugins.

| Hook | Event · matcher | Default | What it does |
|------|-----------------|---------|--------------|
| `pretooluse-gate.py` | PreToolUse · `Bash` | **on** | Blocks `git commit` / `git push` unless `/gate` recorded a pass for the current tree. |
| `pretooluse-secret-scan.py` | PreToolUse · `Write\|Edit\|MultiEdit` | **on** | Blocks writes containing obvious credentials (API keys, private keys, high-entropy secrets). |
| `posttooluse-lint.py` | PostToolUse · `Write\|Edit\|MultiEdit` | off | Advisory lint-on-save; never blocks. |
| `permission-router.py` | PreToolUse · `Bash` | off | Auto-approves known read-only commands; optionally routes ambiguous ones to a model. |

## The gate ↔ commit contract

`pretooluse-gate.py` and `/gate` share a cache under `~/.100xprism/gate-cache/`. The cache
key is a sha256 over **HEAD + tracked diff + untracked status**, so a recorded pass is
invalidated the instant the tree changes or HEAD moves. Flow:

1. You run `/gate`. Its final step (when ALL gates pass) calls
   `python3 ~/100xprism/hooks/gate-pass.py`, which records the current tree token.
2. You commit. `pretooluse-gate.py` recomputes the token and compares. Match → allow;
   otherwise it blocks and tells you to re-run `/gate`.

This closes the "stale cache for a dirty tree / post-rebase HEAD" hole: editing *any*
tracked or untracked file after the gate re-arms the block.

## Runtime escape hatches

- `HOOK_SECRET_SCAN=off` — disable the secret scan for the current session.
- `HOOK_LINT_ON_SAVE=off` — silence lint-on-save.
- `HOOK_ROUTER_MODEL=claude-haiku-4-5` — enable the router's optional model tier (needs
  the `claude` CLI); leave unset to use the deterministic allowlist only.

## Adding / changing hooks

Edit `hooks.manifest.json` (the single source of truth), then re-run `emit-hooks`. The
merge is idempotent and declarative: enabled hooks are ensured present, disabled hooks
are removed — re-running never duplicates entries.

Hook docs: <https://docs.claude.com/en/docs/claude-code/hooks>
