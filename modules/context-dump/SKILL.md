---
name: context-dump
description: Sync 7 days of GitHub activity (commits, PRs, issues, reviews) into a structured context dump. Optionally pull from Slack, GDrive, or Asana if configured. Use at the start of a session to get fully caught up before coding.
category: docs
tier: on-demand
slash_command: /context
allowed-tools: Bash(git:*) Bash(gh:*) Read Grep Glob WebFetch
model: haiku
---

# Context — Session Orientation Dump

Pull 7 days of git and GitHub activity into a structured summary. Run at the start of a session before making changes.

## How to use
- `/context` — full 7-day dump
- `/context focus: <area>` — limit to specific feature area or last PR

---

## Phase 1 — Gather activity

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT"

# Recent commits
git log --since="7 days ago" --oneline --all

# Open PRs
gh pr list --state open

# Merged PRs (last 10)
gh pr list --state merged --limit 10

# Open issues
gh issue list --state open

# CI health
gh run list --limit 3
```

---

## Phase 2 — Produce summary

```
## Context Dump — [DATE]

### What shipped this week
- [merged PRs, 1-line summary of what changed]

### What's in flight
- [open PRs, status, blockers]

### Open issues
- [bugs / features, prioritized by recency]

### Recent commits (7 days)
- [key commits grouped by feature area]

### CI health
- [pass/fail, any flaky tests]

### Suggested next actions
- [based on above, what needs attention first]
```
