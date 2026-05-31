## 100x-dev Repo Review — Synthesis Report

### Executive Summary

The repo is a high-quality, prose-driven skill marketplace, but it was largely written for a small-context, single-threaded, pre-platform-feature era of Claude. The single highest-leverage observation across all 9 reviewers is that **the generator layer (`adapters/lib/modules.py`) is the fan-out point** — any capability taught there instantly propagates to all 64 modules across 7 platforms. Today it emits only prose and silently drops every modern affordance (model routing, hooks, Workflows, structured output, MCP).

**Top 5 themes (by leverage):**

1. **Model-routing modernization (P0).** 9 modules carry `<!-- model: haiku -->` HTML comments that *no code parses* — Claude Code's `model:` frontmatter is never emitted, so the intended Haiku-for-cheap / Opus-for-hard cost optimization is a complete no-op everywhere. Bare aliases also don't map to current IDs (claude-opus-4-8 / claude-sonnet-4-6 / claude-haiku-4-5). Fixing this in `modules.py` + a central alias map is a small change with repo-wide payoff. (Verified: modules.py line 60 reads only `allowed-tools`; 9 modules confirmed.)
2. **First-party hooks gap — enforce the gate (P0).** The headline promise "nothing ships without passing the gate" is honor-system prose. The only shipped hook is a SessionStart update-check (verified). A model that forgets `/gate`, or a user running raw `git commit`, ships unchecked. Hooks are now mature and `hookify` is already bundled; ship a PreToolUse gate-on-commit/push block.
3. **Correctness drift / versioning (P0).** package.json=2.0.1 vs VERSION=2.0.4 (verified) → npm-published version is 3 releases stale. plugins[]=10 but README/update.sh claim 12 (verified) → fresh installs never enable understand-anything / ui-ux-pro-max. gate says "four gates" but defines five (verified). No CI beyond release.yml; 32 evals.json files have no runner (verified).
4. **Exploit Workflow / subagent fan-out (P1).** orchestrate, gate, test, launch, the design pipeline, ad-creative, and the marketing research skills all run independent work serially. Deterministic Workflow + parallel subagents + structured-output return contracts now make fan-out cheap and auditable.
5. **Stale assumptions: hardcoded stack + stale external facts (P1).** architect/issue/fix-bugs hardcode GCP+Firebase+Stripe+npm+pytest; cloud-security is GCP-only with hardcoded us-central1; marketing skills assert fast-rotting stats (AI search backends, GA4 client-side, prices) as durable truths. A shared stack-detection helper and dated-stat caveats fix most of it.

### What changed in the model landscape that motivates this

The mid-2026 baseline (Opus 4.8 @ 1M context with extended/interleaved thinking, Sonnet 4.6, very-capable Haiku 4.5) plus mature platform features (the Workflow tool for deterministic fan-out, Task/Agent subagents, PreToolUse/PostToolUse/SessionStart hooks, skill `model:` frontmatter routing, structured output, native `/code-review`, `/simplify`, `/security-review`, MCP servers) collectively obsolete three patterns this repo leans on: (a) prose-stuffed single-context skills (1M context + native skill systems beat concat blobs), (b) "do NOT skip phases" advisory guardrails (hooks/Workflows enforce them), and (c) manual research/legwork rituals (agentic WebSearch/WebFetch + parallel subagents automate them). The repo's own `model:` comments and "throw more compute at it" subagent advice show intent to use these — the wiring just never landed.

---

## Theme 1 — Model-routing modernization (generator-layer) — **P0**

- **[P0 · M] Parse and emit model routing in `modules.py`.** `split_frontmatter`/`list_modules` never read a `model` field (verified line 60). Promote the 9 `<!-- model: X -->` comments to real frontmatter, add a `MODEL_ALIASES` map {opus→claude-opus-4-8, sonnet→claude-sonnet-4-6, haiku→claude-haiku-4-5}, inject `model:` into generated Claude Code skill frontmatter (strip the dead comment), and emit a visible "_Runs best on: <model>_" annotation for Cursor/AGENTS/GEMINI. A model bump then becomes a one-line edit. (Merges 4 duplicate findings.)
- **[P1 · S] Re-tier the 9 routed skills by reasoning load.** Don't blanket-haiku. Mechanical fixers (lint, update-claude-md rule-write, context-dump, connect) → Haiku 4.5; judgment scanners (security triage, techdebt semantic dedup, cloud-security exception calls, data-query multi-join SQL) → Sonnet 4.6; blocking go/no-go → Opus-Fast + extended thinking. Add a `sonnet` middle tier (none exists today).
- **[P2 · M] Slash-command aliases discard metadata.** `cmd_emit_claude_code` writes a flat "Use the `<name>` skill" body, dropping allowed-tools, model, and argument-hint. Emit command frontmatter (argument-hint derived from body, model from routing, allowed-tools) so commands honor the same routing/guardrails.
- **[P2 · S] Cursor .mdc emits empty globs + alwaysApply:false for all modules.** Map tier→rule type: core/always-on (commit, gate) → alwaysApply:true; on-demand → description-triggered. Use the truncated first-sentence description for tighter triggering.

## Theme 2 — First-party hooks gap (enforce the guarantees) — **P0**

- **[P0 · L] Ship a `hooks/` pack merged into settings.json by install.sh.** (1) PreToolUse on Bash `git commit|git push` that reads `~/.100x-dev/gate-cache` vs HEAD and blocks (exit 2) if not cached-passing — turns "nothing ships without passing the gate" from prose into enforcement. (2) PreToolUse secret-scan on Write/Edit. (3) optional PostToolUse lint-on-save. Make them opt-in toggles. Add `emit-hooks` to the generator so they're versioned/idempotent. (Merges 4 duplicate findings — this is the single biggest missed platform feature.)
- **[P1 · M] Ship the subagents permission-router hook as a real artifact.** The subagents skill documents "route permission requests to a model to auto-approve safe ones" but ships nothing. Implement it as an installable PreToolUse hook; route the safety scan to Haiku 4.5 (cheap), escalate ambiguous calls to Opus 4.8. Fix the stale `code.claude.com/docs` URL and mark `ctrl+b` as Claude-Code-specific.

## Theme 3 — Correctness drift & versioning hygiene — **P0**

- **[P0 · S] Bump package.json 2.0.1 → 2.0.4** (verified drift) and add a release-time guard in release.yml asserting VERSION == package.json.version == git tag before publish.
- **[P0 · S] Fix plugins[] 10-vs-12 drift.** Add `understand-anything` and `ui-ux-pro-max` to `plugins[]` in plugins.json (verified they're only in extraKnownMarketplaces). Refactor update.sh `run_plugin_updates()` to read the list FROM plugins.json instead of a hardcoded parallel 12-entry list; print failures instead of swallowing them in a "skipped" bucket. Make plugins.json the single source of truth. (Merges 3 duplicate findings.)
- **[P1 · S] Reconcile gate "four gates" wording** (verified: body says "four", defines Gate 5, pr says "5 gates"). Use "all gates" and make the count dynamic since Gate 4/5 are conditional.
- **[P1 · S] Reconcile README/banner counts** (12 plugins, postcard alt-text). Generate counts from modules.py + plugins.json at release; fail release on mismatch. Add a "Models" note naming the assumed frontier baseline.
- **[P1 · M] Add a CI meta job** (runs on every PR): parse all 64 module frontmatter and fail on any parse error; assert module/slash/skill/plugin counts match README; validate every evals.json; assert the version triple. Cheap, deterministic, catches every drift above.

## Theme 4 — Eval harness (verify skills trigger & produce good output) — **P0/P1**

- **[P0 · L] Wire up the 32 dormant evals.json** (verified count). Add an `/eval` module + CI job that fans out each module's eval cases as parallel subagents, has Haiku 4.5 grade each assertion with structured output (pass/fail + reason), and emits a per-module trigger-accuracy + output-quality scorecard. Run on changed modules per-PR, nightly on all 64. skill-creator (bundled) already supports benchmarking — only wiring is missing.
- **[P1 · S] Add a deterministic trigger-overlap lint.** Parse every description, flag module pairs whose trigger phrases overlap (the many `*-cro` skills, conversion-copy vs copywriting, systems-architect vs enterprise-design). Catches mis-fires without an LLM.

## Theme 5 — Exploit Workflow / subagent fan-out — **P1**

- **[P1 · M] orchestrate: add a real Workflow fan-out phase.** It mentions subagents but describes sequential delegation. Add: spec → parallel implementation subagents (one per workstream) → gather structured diffs → independent reviewer subagent (`/code-review`) → merge/verify gate. Reference dispatching-parallel-agents / subagent-driven-development.
- **[P1 · M] gate + test + launch: parallelize independent phases.** gate runs Gate 1–5 serially; security/build/cloud-security are independent → fan out as parallel subagents returning structured `{gate,status,findings[],severity}` verdicts, reduce into the summary box. test: parallelize frontend/backend/Python unit layers before the shared-Docker integration phase. launch: fan out lint+security+build after the test gate. Cache each verdict on a tree hash.
- **[P1 · S] Standardize structured-output return contracts for subagents.** Document in the subagents skill that exploration/research/review subagents return a schema (findings[], files[], risks[], confidence) not prose, so the parent acts deterministically. Reframe context-isolation: with 1M context, isolate for parallelism/adversarial independence, not token savings. Add model-routing note (Haiku breadth / Opus depth).
- **[P1 · S] Adversarial red-team subagent as a shared primitive.** One reusable pattern (fresh subagent, zero authoring context, hostile prompt, structured findings) reused by grill-me (PR review), architect (design critique), issue (root-cause challenge), orchestrate (verification gate). Removes self-review bias. Wire grill-me Mode A to `/code-review`.
- **[P1 · L] End-to-end design pipeline as a Workflow.** systems-architect → (visual-system-architect ‖ interaction-engineer ‖ conversion-copy) → figma-translator, passing structured output between stages. Turns 5 manual copy-paste steps into one command.
- **[P2 · M] ad-creative & paid-ads: subagent fan-out + live-data loop.** ad-creative "100+ variations" should fan out one subagent per angle/platform + a consolidation/dedup pass. paid-ads should pull live performance via Google Ads MCP/CLIs, reason with extended thinking, emit a ranked scale/pause/retarget action list.

## Theme 6 — Stale stack assumptions (cross-stack-ify) — **P1**

- **[P1 · L] Shared stack-detection helper (in `_lib`) for architect/issue/fix-bugs.** All three hardcode GCP+Firebase+Stripe and npm/pytest. Detect cloud provider, test runner, CI system, and instruction file once; parameterize their cloud/log/test commands. Gate the literal `gcloud logging ... --project=<project>` behind "if GCP detected" and resolve project from gcloud config. Fix architect's `<!-- model: opus -->` comment and the malformed YAML block-scalar description that breaks triggering. (Merges architect/issue/fix-bugs stack findings.)
- **[P1 · L] cloud-security multi-cloud split.** Gate 5 detects AWS but cloud-security only scans GCP and hardcodes `us-central1` (verified pattern). Split into gcp/aws/azure modules sharing a common code-level reference; Gate 5 routes to detected provider; iterate all Cloud Run regions; key SSL on `sslMode` (requireSsl fallback); unify the instruction-file detection list. Extract shared static patterns (secrets, eval, SQLi, CORS) so security and cloud-security stop running near-duplicate greps.
- **[P1 · M] test: create the missing `e2e-patterns.md`** (verified: ref exists, `modules/test/docs/` does not). Create `modules/test/references/e2e-patterns.md` with the promised 4c–4g templates, or inline them. Make coverage thresholds (95%/90%) configurable with a loop-iteration cap; detect Postgres/Redis versions instead of hardcoding `postgres:16`/`redis:7`.
- **[P1 · S] commit/release: parameterize the `Co-Authored-By` trailer.** Both hardcode `Claude Sonnet 4.6` while release runs on Opus 4.8 → mis-attribution + per-bump drift. Reference the running model or use a stable generic identity. Replace release's blocking `sleep 10/20` with a Monitor/poll-until loop.
- **[P2 · M] commit/push gate-cache keyed on HEAD is unsafe.** Passes stale results for dirty trees and post-`git pull --rebase` HEADs. Key on a tree hash (`git write-tree`) + clean-tree check; invalidate on lockfile change.

## Theme 7 — Triggering / overlap cleanup — **P1/P2**

- **[P1 · S] architect malformed frontmatter description** (verified). The `> **Scope:**` block scalar leaves zero natural-language triggers. Replace with a keyword-packed one-liner; move the Scope note to the body.
- **[P1 · M] Merge/dedup overlapping skills.** systems-architect ≈ enterprise-design (merge or make a thin alias with distinct niche). conversion-copy ⇄ copywriting (add trigger list + cross-reference; decouple from "Figma Make"). spec ⇄ grill-me Mode C (make Mode C a pointer to /spec; fix spec's `/commit` handoff → `/orchestrate`). churn-prevention ⇄ email-sequence dunning/win-back (split strategy vs copy ownership).
- **[P2 · S] Pricing routing triangle.** Add "for plan structure/packaging, see pricing-strategy" to page-cro so plan-structure problems aren't mistaken for page-layout problems.
- **[P2 · M] Factor duplicated CRO "Experiment Ideas" into one shared reference**; reconcile page-cro's two reference files; defer methodology to ab-test-setup.

## Theme 8 — Stale external facts in marketing (dated caveats) — **P1**

- **[P1 · M] ai-seo: fix outdated search-backend claims + undated stats.** "ChatGPT = Bing index" and "Claude = Brave Search backend" are stale → replace with capability-based, dated guidance (OAI-SearchBot, first-party retrieval). Tag headline stats (~45% AI Overviews, +58% click reduction, Princeton GEO %) with source years + a "re-verify before quoting" guardrail. Reconcile the +40% vs +132% citation-boost discrepancy between SKILL.md and the reference file.
- **[P1 · H/M] analytics-tracking: modernize GA4 framing.** Client-side gtag/GTM as default is dated. Add server-side vs client-side decision section + Measurement Protocol/GTM server container; replace "consent mode" with Consent Mode v2 basic/advanced; drop "IP anonymization" (UA concept); add Meta CAPI / Google Enhanced Conversions; make Output Format stack-agnostic.
- **[P2 · M] ab-test-setup: add Bayesian/sequential + low-traffic path.** Pure frequentist "47k/variant" is unactionable for the SaaS/B2B audience these skills target. Add Bayesian (probability-to-beat) / sequential (mSPRT, always-valid p-values) that solve the peeking problem the skill warns about; lead tools with PostHog/GrowthBook; soften Optimizely.
- **[P2 · S] Dated-stat / dated-example sweep.** Hedge referral LTV/churn stats, FTC Click-to-Cancel legal claim, cold-email reply-rate trend, paid-ads 2024Q1 naming examples, launch-strategy dollar figures, ad-creative per-unit prices (→ relative tiers + dated banner) and the gemini-2.5-flash-image / "Nano Banana Pro" naming mismatch.

## Theme 9 — Agentic upgrade of manual marketing rituals — **P1/P2**

- **[P1 · M] ai-seo automated AI-visibility audit.** Replace the manual "run 10–20 queries by hand into a spreadsheet" with WebFetch robots.txt parsing + WebSearch + parallel subagents auto-populating the citation table. Position the spreadsheet as the no-tools fallback. Optionally wire a scheduled agent for weekly share-of-AI-voice trending.
- **[P1 · M] cold-email: let the model do the research.** Given prospect domains/LinkedIn URLs, fan out one subagent per prospect via WebSearch/WebFetch to gather funding/hiring/tech-stack/recent-post signals and draft Level-4 personalization at batch scale, keeping the "sounds human" guardrails.
- **[P2 · M] competitor-alternatives & programmatic-seo agentic paths.** competitor-alternatives: agent-driven live pricing/review-sentiment refresh into the centralized YAML (flag pricing as must-verify-live). programmatic-seo: per-page unique-content generation via subagent fan-out + an embedding/n-gram near-duplicate gate that enforces the skill's own #1 anti-thin-content principle.
- **[P2 · M] pricing-strategy & revops compute leverage.** pricing-strategy: ingest Van Westendorp/MaxDiff CSVs and compute intersection bands / utility scores via code execution. revops: derive lead-scoring weights + MQL threshold from a closed-won/closed-lost export instead of hand-assigning points.

## Theme 10 — Adapter parity & MCP-first data access — **P1/P2**

- **[P1 · L] Per-module emitters for Codex/Gemini (not concat blobs).** `_run_concat` stuffs all bodies into one flat file (AGENTS.md/GEMINI.md/.windsurfrules/copilot-instructions.md), a small-context relic that forfeits description-based auto-trigger and model hints. Give Codex/Gemini native per-module command/skill emitters analogous to emit-cursor; re-evaluate the Windsurf byte-budget index-only fallback now that context is large. Thin wrappers (codex/gemini/copilot/antigravity.sh) expose no MCP/skill frontmatter; finalize the provisional Antigravity format.
- **[P1 · M] MCP-first integration in connect + warehouse access in db/data-query.** connect hardcodes a 27-service roster with brew/npm installs and a deprecated `gh auth login --with-token`, and has zero MCP path despite the description promising "MCP/API". Add an MCP verification path (verify via configured MCP server, CLI fallback), make the roster data-driven. data-query lists only BigQuery/psql/mysql/sqlite3 while sibling /db supports Snowflake/Databricks/Athena/Presto — delegate /query to the same db-engines/* files and add the MCP branch it promises. Fix db's hardcoded `~/.claude/commands/db-engines` path (breaks on non-Claude adapters).
- **[P1 · M] data-query accuracy: load full schema + route SQL to a thinking model.** Haiku + one-table sampling fails on multi-join SQL. With 1M context, load information_schema + FKs + sample rows; route multi-table SQL authoring to Sonnet/Opus+thinking. Add a read-only guardrail (reject non-SELECT, require LIMIT/dry-run) and structured-output result envelope.
- **[P2 · S] Repo-grounded design generators.** visual-system-architect / interaction-engineer / enterprise-design take only abstract inputs (brand name, personality) and regenerate from scratch. Add a Step 0 that ingests tailwind.config/CSS vars/tokens/component lib so they extend the existing brand. Add real model frontmatter (replace `<!-- model: opus -->`).

## Theme 11 — Plugin set rationalization — **P2**

- **[P2 · M] Pin third-party plugins via a committed lockfile (SHA) + bump workflow.** All plugin/marketplace refs float to default-branch HEAD (verified bare refs); claude-mem/ui-ux-pro-max ship MCP servers + session hooks executing in users' environments. Add plugins.lock.json with resolved SHAs, diff old→new in update.sh, scheduled bump PRs. At minimum pin the two highest-risk third-party repos.
- **[P2 · S] Drop now-native-duplicated plugins.** code-simplifier ≈ native `/simplify` + first-party techdebt; pr-review-toolkit ≈ native `/code-review` + grill-me/pr; security-guidance ≈ native `/security-review` + security/cloud-security. Route to native commands; keep only genuinely additive subagents with documented justification.
- **[P2 · M] Re-justify claude-mem; clarify design-plugin lanes.** claude-mem's compression premise is weakened by 1M context + native memory (residual value = durable cross-session semantic recall); if kept, make Bun a hard prerequisite and pin to SHA. Define lanes for the 3-way design overlap (frontend-design = component code; ui-ux-pro-max = design-system planning; first-party = spec/blueprint/Figma).
- **[P1 · L] Add missing high-value plugins/skills.** Promote the existing deep-research harness to the pinned set and wire it into seo-audit/ai-seo/competitor-alternatives/content-strategy/pricing-strategy "gather + verify current facts" phases (fact/freshness-sensitive, marketing-heavy repo). Add a skill-eval plugin run in CI; evaluate MCP server plugins (Stripe/Supabase/BigQuery) so connect/data-query get structured tool-use.

## Net-new capability ideas (P1/P2)

- **[P1] Runtime AI-personalization layer** for save offers / paywalls / onboarding / email — feed per-user context (cancel reason, just-blocked feature, inferred role, usage timeline) to a model that returns tailored copy within hardcoded business guardrails (price floors, max discount, approved claims). Biggest unlock for the CRO cluster — turns "design one static flow" into "design a flow that adapts per user."
- **[P1] LLM-as-analyst over raw analytics/event streams** — ingest funnel/cohort exports and produce the diagnosis (biggest drop-off, churn-signal accounts, segment differences) instead of telling users to read dashboards. Pair with data-query for the pull; replaces churn-prevention's hand-weighted health score.
- **[P2] Cross-flow lifecycle orchestration** — a meta-skill that fans out the 11 CRO/lifecycle skills in parallel then reconciles them into one coherent journey with shared event names and non-colliding triggers (paywall vs popup vs upgrade-email not all firing at once).
- **[P2] emit-workflow generator command** — for modules with ordered `## Phase N` bodies (launch/release/gate/commit/push), emit a Workflow definition so phase order is runtime-enforced rather than "do NOT skip phases" prose.
---

## Appendix A — Critique & Sequencing (adversarial pass)

The synthesis above was independently fact-checked. **All load-bearing claims verified:** `package.json`=2.0.1 vs `VERSION`=2.0.4 vs tag `v2.0.4`; `modules.py` parses only `allowed-tools` (never `model`); exactly **9** `<!-- model: X -->` comments (7 haiku, 2 opus, **0 sonnet** → missing middle tier); `plugins[]`=10 vs claimed 12; gate says "four" but defines five; **32** `evals.json` with no runner; only `release.yml` is active repo CI; `test/SKILL.md:384` references a non-existent `../docs/e2e-patterns.md`; push gate-cache keys on bare `HEAD` with no clean-tree check.

**Corrections / additions the synthesis missed or under-specified:**

1. **Cost/latency/safety budget is absent (P1).** Every routing recommendation pushes work toward bigger models + parallel fan-out, which multiplies $/run and latency. Re-tiering "by reasoning load" is an unfalsifiable judgment call without evidence. **The eval harness (Theme 4) must measure quality-per-dollar per tier and gate the routing decisions.** Adopt a default-cheap → escalate-on-uncertainty pattern with fan-out concurrency caps.
2. **Second distinct plugin bug (P1).** Beyond the 10-vs-12 drift: marketplace key is `ui-ux-pro-max-skill` in `plugins.json` but `update.sh` iterates `ui-ux-pro-max` — a name mismatch that makes updates **silently no-op**. Fix must reconcile the key name, not just add 2 entries to `plugins[]`.
3. **`github-actions/ci.yml` already exists** as a user-facing project *template* (lint/unit/e2e). New repo-self-CI (meta job, eval runner, trigger lint) must be a **separate** workflow — don't confuse or duplicate the template.
4. **Non-Claude adapter routing is documentation-only.** Codex/Gemini/Cursor/Copilot/Windsurf/Antigravity pick their own model; a `_Runs best on: <model>_` annotation is a hint, not functional routing. Scope accordingly — don't over-build.
5. **Frontmatter migration blast radius.** Promoting `<!-- model -->` to real `model:` frontmatter changes what every adapter emitter sees; all 7 emitters must tolerate/strip the key so it doesn't leak into prose blobs. Run the meta/eval CI **before** this lands.

**Sequencing (more important than raw priority labels):**

- **Ship standalone immediately, zero blast radius:** bump `package.json` → 2.0.4.
- **Build first as a prerequisite gate:** the eval harness + trigger-overlap lint + meta CI (Theme 4). It must exist *before* model-routing (Theme 1) and fan-out (Theme 5) ship, because those are the highest-blast-radius edits.
- **Promote to P1 and attach to the hooks theme:** the push gate-cache `HEAD`-keying fix (was P2). Shipping the gate-on-commit hook on top of a cache that trusts stale/dirty-tree results hardens a broken foundation. Switch the cache key to a tree-hash + clean-tree check, landing with or before the hook.
