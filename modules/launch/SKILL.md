---
name: launch
description: You are a release engineer. Execute each phase in order. Each must fully complete before advancing. Do NOT ask for permission. Stop only if something is truly unfixable.
category: data
tier: on-demand
slash_command: /launch
---

# Launch — Pre-flight Pipeline: Docker → Test → Lint → Security → Build → Commit → Push → Cleanup

You are a release engineer. Execute each phase in order. Each must fully complete before advancing. Do NOT ask for permission. Stop only if something is truly unfixable.

---

## Phase 0 — Docker Build & Smoke Test (if applicable)

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
ls "$PROJECT_ROOT/Dockerfile" 2>/dev/null && echo "HAS_DOCKERFILE" || echo "NO_DOCKERFILE"
```

**Skip this phase if no Dockerfile exists.**

If Dockerfile exists:

### 0a. Build images
Detect from `docker-compose.yml` whether there are multiple services. Build all:
```bash
cd "$PROJECT_ROOT"
docker build -t $(basename $PROJECT_ROOT)-api:local . 2>&1
# If dashboard/frontend Dockerfile exists:
docker build -t $(basename $PROJECT_ROOT)-dashboard:local ./dashboard 2>&1 || true
```
Fix any build errors. Iterate until all images build successfully.

### 0b. Start stack
```bash
# Use docker-compose.yml or compose.yml if present
COMPOSE_FILE=$(ls docker-compose.yml compose.yml deploy/docker-compose.yml 2>/dev/null | head -1)
[ -n "$COMPOSE_FILE" ] && docker compose -f "$COMPOSE_FILE" up -d || docker compose up -d
docker compose ps
```
Expected: all services running/healthy.

### 0c. Run migrations (if applicable)
```bash
docker compose run --rm migrate 2>/dev/null || true
```

### 0d. Smoke test
Read the project instruction file or `README.md` for health endpoint. Fall back to common defaults:
```bash
curl -s http://localhost:8000/health 2>/dev/null || \
curl -s http://localhost:3000/health 2>/dev/null || \
curl -s http://localhost:8080/health 2>/dev/null || echo "No health endpoint found"
```

### 0e. Cleanup
```bash
docker compose down 2>/dev/null || true
```

**GATE: All images build, containers healthy, smoke test passes.**

---

## Phase 1 — Tests

Run the **test** workflow. The test workflow will:
1. Auto-start required Docker services (DB, Redis, etc.) for integration tests
2. Run unit tests against real services — no DB mocks
3. Run integration tests against a real Docker DB
4. Run E2E tests against the full docker compose stack
5. Loop until all thresholds are met with zero failures

Thresholds: Lines ≥ 95% | Functions ≥ 95% | Statements ≥ 95% | Branches ≥ 90%

**GATE: The test workflow reports "COVERAGE MET ✅" with zero failures.**

---

## Phase 2 — Lint

Run the **lint** workflow. Fix all errors across frontend, backend, and type checks.

**GATE: Zero lint errors remaining.**

---

## Phase 3 — Security

Run the **security** workflow. Fix critical/high vulnerabilities. Confirm no real secrets in source.

**GATE: No critical/high vulns (outside documented known exceptions) AND no real secrets.**

---

## Phase 4 — Build

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT"
```

Detect and run applicable builds:
- **npm frontend**: `npm run build`
- **npm backend**: `cd api && npm run build`
- **Python**: `./venv/bin/python -m build 2>/dev/null || true`

Fix any compiler errors. Re-build only the failing target.

**GATE: All applicable builds succeed with zero errors.**

Phases 1–4 constitute a full gate pass. Write the gate cache so commit and push skip re-running it:
```bash
echo "$(git rev-parse HEAD)" > ~/.100x-dev/gate-cache
```

---

## Phase 5 — Commit

Run the **commit** workflow. Stage, write, and create a conventional commit.

---

## Phase 6 — Push & Deploy

Run the **push** workflow. Push, handle hooks, monitor CI/CD, auto-fix failures if needed.

---

## Phase 6b — Deployment Verification

After CI/CD passes and deployment completes, run the full verification pipeline.

```bash
# Detect project instruction file
INSTRUCTION_FILE=$(for f in CLAUDE.md AGENTS.md .cursorrules .windsurfrules .github/copilot-instructions.md GEMINI.md; do [ -f "$PROJECT_ROOT/$f" ] && echo "$PROJECT_ROOT/$f" && break; done)
```

### Step 1 — Health checks

Read health endpoint URLs from the project instruction file, README, or use common defaults:

```bash
# From project instruction file
[ -n "$INSTRUCTION_FILE" ] && grep -E "https?://[^ ]*/health" "$INSTRUCTION_FILE" 2>/dev/null | head -3

# Common defaults to try
# /health, /healthz, /api/health, /status
```

Hit each endpoint. Retry up to 5 times with 10-second intervals (deployment may still be rolling out). Confirm HTTP 200 and a healthy response body.

**If health checks fail after 5 retries → trigger rollback (Step 4).**

### Step 2 — Smoke tests

If E2E or smoke tests exist, run a targeted subset against production:

```bash
# Detect smoke test locations
ls tests/smoke/ e2e/smoke/ tests/critical/ 2>/dev/null || true
```

Detection patterns:
- Directories: `tests/smoke/`, `e2e/smoke/`, `tests/critical/`
- Tagged tests: `@smoke`, `@critical`, `mark.smoke`
- If no smoke tests exist, skip this step gracefully

Run detected smoke tests against the production URL configured in the project instruction file:

```bash
# Look for production URL in project instruction file
[ -n "$INSTRUCTION_FILE" ] && grep -E "https?://[^ ]+" "$INSTRUCTION_FILE" 2>/dev/null | grep -iE "prod|production|live" | head -1
```

**If smoke tests fail → trigger rollback (Step 4).**

### Step 3 — Metrics check

If a monitoring URL is configured in the project instruction file:

```bash
[ -n "$INSTRUCTION_FILE" ] && grep -iE "monitoring|grafana|datadog|newrelic" "$INSTRUCTION_FILE" 2>/dev/null | head -1
```

If found:
- Note the monitoring URL for manual review
- Check for error rate information if accessible via API
- Flag if error rate appears elevated compared to normal

If no monitoring URL configured, skip this step gracefully.

### Step 4 — Auto-rollback (on failure)

If any verification step fails:

```bash
echo "Deployment verification FAILED. Rolling back..."

# Revert the last commit (safe — creates a new commit, not destructive)
git revert HEAD --no-edit

# Push the revert
git push origin "$(git branch --show-current)"
```

After rollback:
1. Re-run health checks to confirm rollback succeeded
2. Report which verification step failed and why
3. Provide full diagnosis

```
╔══════════════════════════════════════════════════════╗
║           DEPLOYMENT FAILED — ROLLED BACK             ║
╠══════════════════════════════════════════════════════╣
║ Health:      ✅ PASSED / ❌ FAILED                    ║
║ Smoke tests: ✅ PASSED / ❌ FAILED (details)          ║
║ Metrics:     ✅ NORMAL / ⚠️ ELEVATED / skipped        ║
║ Action:      Auto-reverted commit <hash>              ║
║ Rollback:    ✅ Health confirms rollback OK            ║
╠══════════════════════════════════════════════════════╣
║ STATUS: ROLLED BACK — human review required           ║
║ Diagnosis:   [what failed and why]                    ║
╚══════════════════════════════════════════════════════╝
```

If rollback is set to `manual` in the project instruction file (`rollback: manual`), report the failure but do NOT auto-revert. Wait for human decision.

### Verification output (on success)

```
╔══════════════════════════════════════════════════════╗
║           DEPLOYMENT VERIFIED                         ║
╠══════════════════════════════════════════════════════╣
║ Health:      ✅ All endpoints responding (200)        ║
║ Smoke tests: ✅ N/N passed | skipped                  ║
║ Metrics:     ✅ Error rate normal | skipped            ║
╠══════════════════════════════════════════════════════╣
║ STATUS: DEPLOYED & VERIFIED ✅                        ║
╚══════════════════════════════════════════════════════╝
```

---

## Phase 7 — Post-launch cleanup

### 7a. Close related GitHub issues
Scan commit messages from this launch for issue references:
```bash
git log $(git rev-parse HEAD~10 2>/dev/null || git rev-list --max-parents=0 HEAD)..HEAD \
  --format='%s %b' 2>/dev/null | grep -oE '#[0-9]+' | sort -u
```
For each referenced issue that is still open:
```bash
gh issue close <N> --comment "Resolved in $(git log -1 --format='%h') — $(git log -1 --format='%s')" 2>/dev/null || true
```
Skip issues already closed or in different repos.

### 7b. Update ROADMAP.md (if exists)
```bash
[ -f "$PROJECT_ROOT/ROADMAP.md" ] || exit 0
OPEN=$(gh issue list --state open --json number 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null)
CLOSED=$(gh issue list --state closed --json number --limit 1000 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null)
echo "Open: $OPEN | Closed: $CLOSED"
```
Update the issue count summary line in `ROADMAP.md` if counts changed. Update `Last updated` date.

### 7c. Update project instruction file (if features changed)
If new features were implemented or bugs fixed, update the feature audit table. Update `Last updated` date.

### 7d. Commit doc updates (if any changed)
```bash
git diff --name-only ROADMAP.md CLAUDE.md AGENTS.md .cursorrules .windsurfrules GEMINI.md 2>/dev/null | grep -q . && \
  git add ROADMAP.md CLAUDE.md AGENTS.md .cursorrules .windsurfrules GEMINI.md 2>/dev/null && \
  git commit -m "docs: update issue tracker counts and documentation after launch" && \
  git push origin main || true
```

---

## Summary output

```
=== Launch Summary ===
Phase 0 Docker:     ✅ Built + healthy | skipped (no Dockerfile)
Phase 1 test:       ✅ COVERAGE MET (XX%)
Phase 2 lint:       ✅ PASSED
Phase 3 security:   ✅ PASSED
Phase 4 Build:      ✅ CLEAN
Phase 5 commit:     <short-hash> <message> | Review ✅ no critical issues | ⚠️ N minor notes
Phase 6 Push:       ✅ CI/CD passed | Health ✅ | Smoke ✅ | Metrics ✅
Phase 7 Cleanup:    Issues closed: #N, #M ✅ | Docs updated ✅ | no changes
Status:             LAUNCHED ✅
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Docker build fails | Fix Python/dependency or TypeScript errors, iterate |
| Coverage below 95% | `/test` loops automatically — let it finish |
| Test fails after fix | Re-run only that suite |
| Build fails with TS errors | Run `npm run typecheck` to isolate first |
| Pre-push hook fails | Fix → NEW commit → push again. Never `--no-verify` |
| Push rejected (non-fast-forward) | `git pull --rebase origin main` then push |
| `gh issue close` fails | Issue may be in a different repo — check `gh repo view` |
| ROADMAP counts don't match | Re-run `gh issue list` and reconcile manually |
