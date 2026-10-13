# Roadmap

What's open, from the [issue tracker](https://github.com/rajitsaha/100xprism/issues).
Shipped work is in the [changelog](CHANGELOG.md).

## Open

| Issue | Title | Priority |
|:--|:--|:--|
| [#28](https://github.com/rajitsaha/100xprism/issues/28) | Parameterize the Co-Authored-By trailer; replace blocking sleeps in commit/release | P1 |
| [#29](https://github.com/rajitsaha/100xprism/issues/29) | Refresh stale external facts in marketing skills with dated, capability-based guidance | P1 |
| [#30](https://github.com/rajitsaha/100xprism/issues/30) | Agentic research paths for marketing/SEO/sales skills (web search + subagent fan-out) | P1 |
| [#31](https://github.com/rajitsaha/100xprism/issues/31) | Unify data access: delegate `/query` to db-engines, add MCP paths | P1 |
| [#32](https://github.com/rajitsaha/100xprism/issues/32) | Cross-cloud security: split `cloud-security` by provider, de-hardcode region | P1 |
| [#33](https://github.com/rajitsaha/100xprism/issues/33) | Resolve skill triggering overlaps; consolidate near-duplicate skills | P1 |
| [#34](https://github.com/rajitsaha/100xprism/issues/34) | Per-module emitters + MCP-aware integration for the Codex and `connect` adapters | P1 |
| [#35](https://github.com/rajitsaha/100xprism/issues/35) | Pin third-party plugins via a lockfile; rationalize plugins vs native commands | P2 |
| [#36](https://github.com/rajitsaha/100xprism/issues/36) | Runtime AI personalization + LLM-as-analyst for the CRO/lifecycle cluster | P2 |
| [#37](https://github.com/rajitsaha/100xprism/issues/37) | `emit-workflow`: generate platform Workflow definitions from phased modules | P2 |
| [#38](https://github.com/rajitsaha/100xprism/issues/38) | Repo-grounded design generators with model frontmatter and a project Step 0 | P2 |

Also planned: background token collection on Linux and Windows (macOS only today).

## Done from the 2026-05 review

Closed: version/count drift + CI meta job (#23), eval harness (#24), model routing in
the generator (#21), enforcing hooks + `emit-hooks` (#22), subagent fan-out for
`orchestrate`/`gate`/`test`/`launch` (#25), shared stack detection (#26), e2e patterns
and configurable thresholds (#27), native Windows adapter (#54).

Original review: [2026-05-31-100x-powerup-review-design.md](https://github.com/rajitsaha/100xprism/blob/37e3a1c/docs/superpowers/specs/2026-05-31-100x-powerup-review-design.md) (archived; planning docs are no longer tracked).

## Guardrails for every change

- **Cost and latency.** Default cheap, escalate on uncertainty, cap fan-out concurrency.
- **Single source of truth.** Counts and lists come from `modules/`, `plugins.json` and
  `modules.py`; CI (`scripts/meta-check.py`, `context-footprint.js`) catches drift.
- **Every adapter.** A module change must work in Claude Code, Cursor, Codex, Pi and
  Hermes — see [AGENTS.md](AGENTS.md).
