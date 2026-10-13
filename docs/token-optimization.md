# Token optimization

How 100xPrism keeps its standing context small, how to change that, and how to measure
it. For the cost dashboard itself, see [USAGE.md › Monitoring token usage](USAGE.md#monitoring-token-usage).

---

## Where tokens go

Every turn re-sends a fixed block — system prompt, tool schemas, skill and plugin
descriptions, instruction files — before anything you type. Providers bill it in four
buckets:

| Bucket | What it is | Relative $/token (Anthropic list price) |
|:--|:--|:--|
| **cache read** | Fixed context and history served from the prompt cache | 0.1× |
| **cache write** | Context written to the cache when it is new or changed | 1.25× (5-minute cache) |
| **input** | New uncached text: your message, fresh tool results | 1× |
| **output** | What the model generates | ~5× |

Cache reads are cheap per token but usually the largest volume, so cost tracks
**fixed-context size × number of turns**. Shrinking the fixed block is the lever.

---

## Description vs body

An installed module charges you in two ways:

- its **description** — in the always-on index, re-sent every turn
- its **body** — read only when the module is invoked

So 100xPrism decides which modules earn a permanent index slot. Each gets a retention
class from `retention_of()` in `adapters/lib/modules.py` (override per module with
`retention:` in frontmatter):

| Class | Why it is kept | Count |
|:--|:--|--:|
| `must` | Deterministic procedure or house policy a model won't reproduce unprompted — what `gate` runs, the order `release` runs it in, the protocol `pair-loop` speaks | 12 |
| `profile` | Useful in matching repos. Includes **every module that owns a slash command**, so the command resolves | 18 |
| `resolver` | General expertise a capable model already has (marketing, design). Never installed; one row in the catalog | 38 |

Modules that aren't installed are copied to a catalog directory **outside** what the
tool indexes (`~/.100xprism/100xprism-catalog/`, `.cursor/100xprism-catalog/`,
`.agents/100xprism-catalog/`, `.pi/100xprism-catalog/`) and listed in a generated
`100x-resolver` skill with an exact path for each. One description stands in for all of
them.

---

## Modes

| Scope | File → key | Values |
|:--|:--|:--|
| User (Claude Code, Hermes) | `~/.100xprism/config.json` → `skills` (or `PRISM_SKILLS`) | `must` (default) · `profile` · `all` |
| Project (Cursor, Codex, Pi) | `<project>/.100xprism.json` → `profiles` (or `PRISM_PROFILES`) | unset/`[]` = must · profile list = widen · `["all"]` = every module |

Unknown values fail closed to `must`. Change modes with `optimize` (alias `slim`); it
re-emits immediately and also applies to the current directory as a project:

```bash
100xprism optimize                    # must
100xprism optimize --skills=profile   # must + slash-command modules; detect project profiles
100xprism optimize --skills=all       # every module
100xprism optimize --all-projects     # every tracked project
100xprism optimize --dry-run          # show changes only
```

`optimize` also warns about instruction files over ~5 KB (`CLAUDE.md`, `AGENTS.md`,
`.cursorrules`), which are re-sent every turn.

---

## What each mode costs

Estimated always-on description footprint: indexed description characters ÷ 4. This is
deterministic and checked in CI (`npm run context:check`, budgets in
`config/context-budgets.json`). It is **not** provider-billed usage.

| Tool | `all` | `profile` | `must` | CI budget (`must`) |
|:--|--:|--:|--:|--:|
| Claude Code | ~4,834 | ~1,685 | **~592** | 800 |
| Pi | ~4,834 | ~1,685 | **~592** | 800 |
| Codex | ~4,834 | ~1,666 | **~573** | 800 |
| Cursor | ~2,057 | ~821 | **~377** | 500 |
| Indexed entries | 68 | 31 | 13 | |

Cursor is lower because its rule index uses only the first sentence of each description.
Cursor also loads the full **body** of any `alwaysApply: true` rule, so only `gate`
(`tier: core`) is always-on. `commit`, `push` and `branch` were moved off in v3.1: they
are invoked explicitly, and the `gate-on-commit` hook enforces the gate regardless.

Reproduce these numbers:

```bash
node ~/100xprism/scripts/context-footprint.js --json
```

---

## Measuring your own setup

```bash
100xprism audit           # standing-context estimate + plugins, hooks, aliases, drift
100xprism audit --json    # same, machine-readable
100xprism tokens --print  # real token counters from local transcripts
```

`audit` measures what 100xPrism controls in the current directory and in `~/.claude`.
Your other plugins, MCP servers and instruction files add to the fixed block too. Inside
Claude Code, `/context` shows the live window and `/cost` the session total.

---

## Does a smarter model make this unnecessary?

Partly, and the retention classes are drawn on that line. What a capable model already
knows — "act as a senior X, here is a framework" — became `resolver`. What it cannot
know is which commands *this* repo's gate runs, that `user_id` is TEXT not UUID here, or
that the Stripe webhook must register `express.raw()` before `express.json()`. Better
models retire **procedure**; they do not retire **project facts** or **determinism**.

---

## Practical habits

1. Keep `CLAUDE.md` to facts a model can't infer; move reference material to `docs/`
   behind a router table (see [USAGE.md › Project configuration](USAGE.md#project-configuration)).
2. Disable plugins and MCP servers you don't use in a given project; prefer project
   scope over user scope for niche ones.
3. Avoid overlapping tools — two memory systems, two browser stacks, or two review
   plugins each pay their descriptions every turn.
4. `/clear` between unrelated tasks; push large exploration into subagents.
