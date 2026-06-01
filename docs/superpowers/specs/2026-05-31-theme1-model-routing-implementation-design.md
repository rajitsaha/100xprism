# Theme 1 — Model-routing modernization (generator layer) — Implementation Design

**Date:** 2026-05-31
**Scope:** P0 ("Parse and emit model routing in `modules.py`") + P1 ("Re-tier the 9
routed skills by reasoning load") from `2026-05-31-100x-powerup-review-design.md` → Theme 1.
**Deferred (out of scope):** P2 slash-command metadata, P2 Cursor ruletype/globs.

## Problem

9 modules carry a `<!-- model: X -->` HTML comment in their body that **no code
parses**. The generator (`adapters/lib/modules.py`) never reads or emits a `model`
field, so the intended Haiku-for-cheap / Opus-for-hard routing is a no-op on every
platform. The fan-out point is the generator: any field taught there propagates to
all modules across all adapters.

Verified facts:
- `modules.py` `list_modules()` parses only `name/description/category/tier/slash_command/allowed-tools` — never `model` (line 60).
- 9 modules have `<!-- model: X -->` comments: 7 `haiku` (`connect`, `context-dump`, `data-query`, `lint`, `security`, `techdebt`, `update-claude-md`), 2 `opus` (`architect`, `enterprise-design`). Zero `sonnet`.
- No SKILL.md currently has a `model:` frontmatter key.

## Key decision — bare aliases, not pinned IDs

The source review proposed mapping aliases to **pinned full IDs**
(`haiku → claude-haiku-4-5`) and emitting those into Claude Code frontmatter. This
was based on the premise that "bare aliases don't map to current IDs."

**That premise is wrong for Claude Code** (verified against official docs): Claude
Code skill/command frontmatter supports `model:`, and bare aliases `haiku` / `sonnet`
/ `opus` are valid and *preferred* — they auto-resolve to the latest version per
provider. Pinning to a full ID would freeze the version and forfeit auto-updates.

**Decision:** store and emit the **bare alias**.

### Sub-decision — non-Claude adapters get a tier hint, not a Claude model name

Model routing is **functional only on Claude Code**. Codex (GPT), Gemini CLI
(Gemini), Antigravity (Gemini-based), Cursor/Copilot/Windsurf (user- or tool-chosen
model) all select their own model — our field cannot switch it. Emitting a concrete
Claude model name (`_Runs best on: Claude Haiku 4.5_`) into their config files would
be misleading, since those users may run GPT/Gemini and cannot select a Claude model.

The signal worth propagating cross-vendor is the **reasoning tier** the alias encodes
(`haiku` = mechanical/cheap, `sonnet` = mid, `opus` = heavy reasoning), not the exact
model. So:

- **Claude Code** emits the functional `model:` frontmatter (bare alias) — real routing.
- **All other adapters** emit a model-agnostic hint:
  `_Suggested model tier: fast / low-cost (mechanical task)_`.

`MODEL_ALIASES` therefore carries both a Claude `id`/`label` (Claude Code use) and a
vendor-neutral `tier_hint` (everyone else).

## Changes

### 1. Source migration — 9 SKILL.md files

For each of the 9 modules: add `model: <alias>` to the YAML frontmatter and delete
the dead `<!-- model: <alias> -->` line from the body. No tier changes (re-tiering is
the deferred P1 item). `architect`/`enterprise-design` → `opus`; the other 7 → `haiku`.

### 2. `modules.py` — parse

`list_modules()` adds `"model": fm.get("model", "")` to each module dict.
`split_frontmatter` already handles top-level scalar keys, so no parser change.

### 3. `modules.py` — central alias map + annotation helper

```python
MODEL_ALIASES = {
    "haiku":  {"id": "claude-haiku-4-5",  "label": "Claude Haiku 4.5",  "tier_hint": "fast / low-cost (mechanical task)"},
    "sonnet": {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6", "tier_hint": "balanced (moderate reasoning)"},
    "opus":   {"id": "claude-opus-4-8",   "label": "Claude Opus 4.8",   "tier_hint": "most capable (deep reasoning)"},
}

def tier_annotation(model: str) -> str:
    """Vendor-neutral hint for non-Claude adapters. '' for unknown/empty alias."""
    info = MODEL_ALIASES.get(model.strip())
    return f"_Suggested model tier: {info['tier_hint']}_" if info else ""
```

A `sonnet` entry is included now (cheap, future-proofs the deferred P1 re-tier) even
though no module uses it yet. The Claude `id`/`label` fields are unused by the
emitters in this P0 slice (Claude Code routes via frontmatter, not the annotation);
they are retained as the single source of truth for the deferred slash-command /
documentation work.

### 4. Emitters

- **Claude Code** (`cmd_emit_claude_code`): **no change.** `shutil.copytree` already
  copies the SKILL.md frontmatter verbatim; CC reads the bare `model:` alias natively.
  This is the only adapter with functional routing. The win is free once step 1 lands.
- **Cursor** (`cmd_emit_cursor`): inject `tier_annotation(m["model"])` as a line in
  the `.mdc` body when non-empty (Cursor ignores our frontmatter and picks its own model).
- **Concat** (`render_concat`): emit `tier_annotation` in the core-module block
  (alongside the existing slash-command line). `emit_index()`: append `tier_annotation`
  to the on-demand one-liner when non-empty. This covers AGENTS.md (Codex) / GEMINI.md
  (Gemini CLI) / Windsurf / Copilot / Antigravity, which all flow through the
  concat/index path and run non-Claude models.

### 5. Self-test — `test/modules-frontmatter.test.js`

New `node:test` file (matches existing `test/*.test.js`, no new deps), runnable via
`node --test test/`:

1. Run `python3 adapters/lib/modules.py list`; parse JSON.
2. Assert the 9 expected modules carry a `model` that is a key of the alias set
   `{haiku, sonnet, opus}`; assert the expected alias per module.
3. Regression guard: assert **no** module `body` contains the substring `<!-- model:`.
4. Run `emit-cursor` into a temp dir and `emit-concat` to a temp file; assert the
   `Suggested model tier:` annotation appears for a known-routed module (e.g. `lint`)
   and that no Claude model name leaks into the non-Claude output.

## P1 — Re-tiering by reasoning load

Introduces the `sonnet` middle tier. Re-tiering is a cost-affecting judgment call;
the appendix of the source review flags it as unfalsifiable without eval evidence
(Theme 4). We adopt the spec's recommended split and will let the eval harness
validate quality-per-dollar later.

| Skill | Tier | Why |
| --- | --- | --- |
| `lint`, `update-claude-md`, `context-dump`, `connect` | `haiku` | mechanical: tool-driven fixes, rule formatting, activity aggregation, CLI auth |
| `security`, `techdebt`, `data-query` | `sonnet` | judgment: vuln/secret triage, semantic dedup, multi-join SQL authoring (Theme 10 notes haiku fails multi-join) |
| `architect`, `enterprise-design` | `opus` | deep architectural reasoning / full blueprints |

Net change vs P0: `security`, `techdebt`, `data-query` moved `haiku → sonnet`. The
test's `EXPECTED_MODELS` is updated to match, and the concat test now asserts both the
`haiku` and `sonnet` tier hints emit. `effort: high` on the opus skills was considered
and deferred (separate behavior change, out of this slice).

## Non-goals / blast-radius notes

- Non-Claude adapter routing is documentation-only — the annotation is a hint, not
  functional routing. Scoped accordingly.
- All adapter emitters consume `body` (frontmatter stripped), so adding `model:` to
  frontmatter cannot leak the key into prose blobs.
- Slash-command frontmatter and Cursor ruletype/globs remain deferred (P2).
