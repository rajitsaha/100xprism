---
name: issue
description: You are a senior engineering lead and product architect. Given an observation, bug, or gap, conduct a thorough multi-dimensional investigation, find the root cause, plan the resolution, and create a detailed actionable GitHub issue.
category: docs
tier: on-demand
slash_command: /issue
---

# Issue — Investigate & Create Detailed GitHub Issue

You are a senior engineering lead and product architect. Given an observation, bug, or gap, conduct a thorough multi-dimensional investigation, find the root cause, plan the resolution, and create a detailed actionable GitHub issue.

## Do NOT ask for permission — investigate thoroughly, then create the issue.

---

## Phase 1 — Codebase Investigation

Understand the problem in full context before forming any opinion.

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel); cd "$PROJECT_ROOT"
git log --oneline -20
git status

# Detect the stack so later steps don't assume GCP/npm (canonical block — source: _lib/reference.md)
INSTRUCTION_FILE=$(for f in CLAUDE.md AGENTS.md .cursorrules .windsurfrules .github/copilot-instructions.md GEMINI.md; do [ -f "$PROJECT_ROOT/$f" ] && echo "$PROJECT_ROOT/$f" && break; done)
CLOUD=""
if command -v gcloud >/dev/null 2>&1 && gcloud config get-value project >/dev/null 2>&1; then CLOUD=gcp; fi
[ -z "$CLOUD" ] && grep -rqiE "aws_|amazonaws|::aws" "$PROJECT_ROOT"/terraform "$PROJECT_ROOT"/infra "$PROJECT_ROOT"/cdk.json 2>/dev/null && CLOUD=aws
[ -z "$CLOUD" ] && [ -n "$INSTRUCTION_FILE" ] && grep -qiE "gcloud|Cloud Run|Firebase" "$INSTRUCTION_FILE" && CLOUD=gcp
CI_SYSTEM=""; ls "$PROJECT_ROOT"/.github/workflows/*.y*ml >/dev/null 2>&1 && CI_SYSTEM=github-actions
echo "stack: cloud=${CLOUD:-none} ci=${CI_SYSTEM:-none}"
```

1. Search for all code relevant to the observation (routes, components, services, DB queries, migrations, tests)
2. Read the relevant source files — do not guess at behavior, read the actual code
3. Check recent commits in the affected area:
   ```bash
   git log --oneline --since="60 days ago" -- <relevant-paths>
   ```
4. Check for existing related issues (only if `gh` is available). Pull titles **and
   bodies**, then match on *meaning*, not just a keyword grep — a dupe often uses
   different words for the same root cause:
   ```bash
   command -v gh >/dev/null 2>&1 && gh issue list --state all --limit 100 \
     --json number,title,body -q '.[] | "#\(.number) \(.title)\n\(.body)\n---"' 2>/dev/null
   ```
   Read the results and judge whether any existing issue describes the same underlying
   problem; if so, reference or update it instead of filing a duplicate.
5. Check current test coverage for the affected code paths
6. **If this is a production issue and `CLOUD=gcp`**, check Cloud Run logs (resolve the
   project from gcloud config — never pass a literal placeholder):
   ```bash
   if [ "$CLOUD" = gcp ]; then
     PROJECT=$(gcloud config get-value project 2>/dev/null)
     gcloud logging read "resource.type=cloud_run_revision AND severity>=ERROR" \
       --project="$PROJECT" --limit=20 --format="value(textPayload)" 2>/dev/null || true
   fi
   ```
   For other providers, read logs the platform-appropriate way (AWS: `aws logs tail`;
   Vercel: `vercel logs`; or the project's configured log viewer). Skip if not a
   production issue.

---

## Phase 2 — Multi-Dimensional Impact Analysis

Analyze from ALL FIVE perspectives before forming a resolution plan.

### 2.1 Product & Business
- Which feature/journey/tier is affected? Regression or known gap?
- Severity: Critical (blocks core flow) / High (degrades key feature) / Medium / Low
- Revenue, retention, or compliance risk?

### 2.2 User Experience
- What does the user actually see? Exact errors or broken states?
- Data loss, incorrect data, or silent failure? Accessibility / performance impact?

### 2.3 Cloud / Infrastructure
- Which `$CLOUD` services are involved? (GCP: Cloud Run/Cloud SQL/GCS/Pub-Sub/Memorystore/Firebase ·
  AWS: ECS-Lambda/RDS/S3/SNS-SQS/ElastiCache/Cognito · Azure / Vercel equivalents.) Map to the detected stack.
- Scaling, concurrency, IAM, networking, or cold-start related?

### 2.4 Data Architecture
- Which tables/columns/indexes involved? Data integrity risk?
- Migration needed? Cache invalidation? PII/compliance concern?

### 2.5 SaaS / Distributed Systems
- Race condition, multi-tenancy isolation risk, or async/webhook issue?
- Third-party dependency — payment / auth / email / SMS provider (e.g. Stripe, the auth
  provider, the email sender)? Retry/idempotency gap?

---

## Phase 3 — Root Cause Analysis

State the root cause with precision:

- **Immediate cause**: the exact line of code, config value, query, or missing guard that causes the problem
- **Contributing factors**: conditions required for it to manifest (specific data state, load, timing, tier, user type)
- **Detection gap**: what test, review step, or monitoring was missing that let this reach production
- **First introduced**: if determinable from git log, when and which commit introduced this

---

## Phase 4 — Resolution Plan

### Files to change
List every file requiring modification:
```
src/components/Foo.tsx              — add null check before accessing .property
api/src/routes/bar.ts               — replace string interpolation with parameterized query ($1, $2)
api/src/db/migrations/004_fix.sql   — add index on (user_id, created_at)
src/lib/subscriptionTiers.ts        — add missing feature key
```

### Code changes needed
Be precise about what changes are required:
- Exact logic that needs to change (not a full diff, but specific enough to implement)
- Validation or error handling to add
- Type guards, null checks, or defensive coding needed
- New abstractions or helpers required
- Configuration changes (env vars, feature flags)

### Architecture / design changes needed
If structural change is required:
- DB schema changes (new column, table, index, constraint, migration)
- New API route or service
- Cloud infrastructure change (IAM policy, Cloud Run env var, firewall rule, Cloud SQL SSL)
- Cache strategy change (new Redis key, TTL adjustment, invalidation logic)
- API contract change — specify if breaking or non-breaking
- If none required, state: **No architecture changes needed**

### Tests to add or update
```
tests/unit/test_foo.py                    — add: null input, boundary values, error path
api/src/__tests__/unit/bar.test.ts        — update: mock to cover new validation branch
api/src/__tests__/integration/bar.test.ts — add: end-to-end happy path + error case
e2e/tests/05-foo.spec.ts                  — add: user-facing regression test
```

---

## Phase 5 — Regression Risk Assessment

Assess each risk area. Be honest — do not mark everything as "None".

| Risk Area | Level | Rationale |
|---|---|---|
| Functionality regression | None / Low / Medium / High | Which adjacent features could break? |
| Security regression | None / Low / Medium / High | Auth, permissions, data exposure change? |
| Performance regression | None / Low / Medium / High | Hot path affected? DB query cost change? |
| Data integrity | None / Low / Medium / High | Existing data affected by fix or migration? |
| Third-party integrations | None / Low / Medium / High | Stripe/Firebase/Resend/API contract change? |
| Multi-tenancy isolation | None / Low / Medium / High | Could data leak between tenants? |

**Mitigation strategy:**
- What tests close these risks?
- Feature flag / gradual rollout needed?
- Is the DB migration reversible?
- Rollback plan if deploy goes wrong?

---

## Phase 6 — Effort Estimate

| Category | Estimate |
|---|---|
| Investigation | Xh |
| Implementation | Xh / Xd |
| Tests | Xh |
- Review & Deploy | Xh |
| **Total** | **X days** |

T-shirt size: **XS** (< 2h) / **S** (< 1d) / **M** (1–3d) / **L** (3–7d) / **XL** (> 1 week)

Priority: **P0** (production down) / **P1** (critical UX broken) / **P2** (important, not blocking) / **P3** (nice to have)

---

## Phase 7 — Create GitHub Issue

Compose and create the issue:

```bash
gh issue create \
  --title "<type>(<scope>): <concise title — under 72 chars>" \
  --body "$(cat <<'ISSUEEOF'
## Summary
<!-- One paragraph: what is broken, who is affected, and the proposed fix -->

## Observation
<!-- What was observed — exact symptoms, error messages, steps to reproduce -->

**Steps to reproduce:**
1.
2.
3.

**Expected:**
**Actual:**

## Root Cause
<!-- Specific file:line or config — not "unknown" -->

## Impact Analysis

### Product & Business
<!-- tier/user segment affected, revenue/retention risk, severity -->

### User Experience
<!-- what the user sees, data loss risk, accessibility/perf impact -->

### Cloud / Infrastructure
<!-- which cloud services involved (map to the project's provider), scaling/infra implications -->

### Data Architecture
<!-- tables/queries affected, migration needed, data integrity risk -->

### SaaS / Distributed Systems
<!-- race conditions, multi-tenancy, async/webhook, third-party deps (payment/auth/email) -->

## Resolution Plan

### Files to change
\`\`\`
<file>    — <what changes>
\`\`\`

### Code changes
<!-- Specific logic changes needed -->

### Architecture / design changes
<!-- Schema, infra, API contract changes — or "None" -->

### Tests to add / update
\`\`\`
<test file>    — <what to add/update>
\`\`\`

## Regression Risk

| Risk Area | Level | Notes |
|---|---|---|
| Functionality | None/Low/Medium/High | |
| Security | None/Low/Medium/High | |
| Performance | None/Low/Medium/High | |
| Data Integrity | None/Low/Medium/High | |
| Integrations | None/Low/Medium/High | |
| Multi-tenancy | None/Low/Medium/High | |

**Mitigation:** <!-- rollback plan, feature flag, reversible migration -->

## Effort & Priority
- **Size:** XS / S / M / L / XL
- **Priority:** P0 / P1 / P2 / P3
- **Estimate:** X days

## Acceptance Criteria
- [ ] Root cause fixed
- [ ] Tests added/updated for affected code paths
- [ ] All regression risks mitigated
- [ ] The **gate** workflow passes (≥95% coverage, no vulns, build clean, cloud security clean)
- [ ] Deployed and verified in production
ISSUEEOF
)" \
  --label "bug" \
  --assignee "@me"
```

Print the created issue URL and number.
