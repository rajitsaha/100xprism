---
name: test
description: You are a senior test engineer. Auto-detect all test layers (unit, integration, frontend, backend, E2E/system), run them all, write more if coverage is below threshold, and loop until everything passes.
category: quality
tier: on-demand
slash_command: /test
---

# Test — Run All Tests: Unit → Integration → E2E

You are a senior test engineer. Auto-detect all test layers (unit, integration, frontend, backend, E2E/system), run them all, write more if coverage is below threshold, and loop until everything passes.

## Do NOT ask for permission. Do NOT stop until coverage is met.

---

## Testing philosophy

**Prefer real implementations over mocks.**

| Layer | Environment | What to mock |
|---|---|---|
| Unit | In-process only | External APIs that cannot run locally (Stripe, Firebase Auth, Resend, Twilio, cloud SDKs) |
| Integration | Real DB + real app via Docker | Only payment gateways and third-party external APIs |
| E2E / System | Full stack via `docker compose up` | Nothing — zero mocks |

**Never mock:** the database, internal services, business logic, utilities, or pure functions.  
**Only mock:** services that are genuinely unreachable locally (payment processors, auth providers, email senders, external SaaS APIs).

---

## How to use

- `/test` — all layers for files changed since last commit
- `/test --all` — full pass across the entire codebase
- `/test <file>` — target a specific source file
- `/test --unit` — unit tests only
- `/test --integration` — integration tests only (spins up Docker services)
- `/test --e2e` — E2E/system tests only (full docker compose stack)
- `/test --e2e staging` — E2E against staging environment
- `/test --e2e prod` — E2E against production

---

## Coverage thresholds (unit + integration — not E2E)

These are **defaults, used only when the project does not declare its own.** Check first
and honor a project-declared threshold:

```bash
# Project-declared thresholds win over the defaults below. Look, in order, at:
#   - the instruction file (CLAUDE.md/AGENTS.md/…): a line like "coverage: lines 80, branches 70"
#   - jest/vitest config: coverageThreshold.global.{lines,functions,statements,branches}
#   - pyproject.toml / .coveragerc: [tool.coverage.report] fail_under
grep -hiE "coverage(Threshold)?|fail_under" CLAUDE.md AGENTS.md GEMINI.md jest.config.* vitest.config.* vite.config.* pyproject.toml .coveragerc 2>/dev/null | head
```

| Metric | Default threshold (override if the project declares one) |
|---|---|
| Lines | ≥ 95% |
| Functions | ≥ 95% |
| Statements | ≥ 95% |
| Branches | ≥ 90% |

**The coverage loop does not exit until ALL thresholds (project-declared or the defaults
above) are met AND zero unit/integration tests fail — or the iteration cap is reached
(see Phase 3).**

---

## Phase 0 — Docker test environment setup

Before running integration or E2E tests, check if Docker services are required and start them.

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT"
```

### Detect test service requirements

```bash
# Check for docker-compose test config
TEST_COMPOSE=""
for f in docker-compose.test.yml docker-compose.testing.yml docker-compose.yml compose.yml; do
  [ -f "$PROJECT_ROOT/$f" ] && TEST_COMPOSE="$f" && break
done

# Check for service dependencies in pyproject.toml or package.json
grep -qE "postgres|redis|mysql|mongodb|elasticsearch" \
  "$PROJECT_ROOT/pyproject.toml" \
  "$PROJECT_ROOT/requirements*.txt" \
  "$PROJECT_ROOT/package.json" \
  "$PROJECT_ROOT/api/package.json" 2>/dev/null && NEEDS_SERVICES=true || NEEDS_SERVICES=false

echo "Test compose file: ${TEST_COMPOSE:-none}"
echo "Needs services: $NEEDS_SERVICES"
```

### Start test services (if needed)

```bash
# Auto-detect and start required services
TEST_COMPOSE=$(ls docker-compose.test.yml docker-compose.testing.yml docker-compose.yml compose.yml 2>/dev/null | head -1 || true)
NEEDS_SERVICES=$(grep -qE "postgres|redis|mysql|mongodb|elasticsearch" \
  "$PROJECT_ROOT/pyproject.toml" "$PROJECT_ROOT/requirements"*.txt "$PROJECT_ROOT/package.json" 2>/dev/null && echo true || echo false)

if [ -n "$TEST_COMPOSE" ]; then
  docker compose -f "$TEST_COMPOSE" up -d --wait 2>/dev/null || true
elif $NEEDS_SERVICES; then
  # Match the version the project already declares — don't pin a version it doesn't use.
  # Look in any compose file for the image tag; fall back to a current LTS only if absent.
  PG_IMAGE=$(grep -hoE "postgres:[0-9.]+(-[a-z0-9]+)?" "$PROJECT_ROOT"/docker-compose*.y*ml "$PROJECT_ROOT"/compose*.y*ml 2>/dev/null | head -1)
  REDIS_IMAGE=$(grep -hoE "redis:[0-9.]+(-[a-z0-9]+)?" "$PROJECT_ROOT"/docker-compose*.y*ml "$PROJECT_ROOT"/compose*.y*ml 2>/dev/null | head -1)
  : "${PG_IMAGE:=postgres:16}"; : "${REDIS_IMAGE:=redis:7}"   # defaults only when undeclared
  docker run -d --name test-postgres \
    -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test \
    -p 5432:5432 "$PG_IMAGE" 2>/dev/null || true
  grep -qE "redis" "$PROJECT_ROOT/package.json" "$PROJECT_ROOT/pyproject.toml" 2>/dev/null && \
    docker run -d --name test-redis -p 6379:6379 "$REDIS_IMAGE" 2>/dev/null || true
  sleep 3 && docker exec test-postgres pg_isready -U test 2>/dev/null || sleep 3
fi
```

**Run migrations against test DB:**
```bash
# Django
[ -f manage.py ] && ./venv/bin/python manage.py migrate --settings=config.settings.test 2>/dev/null || true
# Alembic
[ -f alembic.ini ] && ./venv/bin/alembic upgrade head 2>/dev/null || true
# Prisma
[ -f prisma/schema.prisma ] && npx prisma migrate deploy 2>/dev/null || true
# Custom
[ -f scripts/migrate.sh ] && bash scripts/migrate.sh 2>/dev/null || true
```

**Verify services are healthy before proceeding:**
```bash
docker compose ps 2>/dev/null || docker ps --filter "name=test-" 2>/dev/null
```

**GATE: Required services must be running before integration or E2E tests.**

### Cleanup (run after all tests complete)

```bash
# Stop test compose
[ -n "$TEST_COMPOSE" ] && docker compose -f "$TEST_COMPOSE" down -v 2>/dev/null || true
# Stop standalone containers
docker rm -f test-postgres test-redis 2>/dev/null || true
```

---

## Step 1 — Detect test stack

```bash
ls package.json api/package.json requirements.txt pyproject.toml 2>/dev/null
cat package.json 2>/dev/null | grep -E '"vitest"|"jest"' || true
cat api/package.json 2>/dev/null | grep '"jest"' || true
ls e2e/ tests/e2e/ playwright.config.* e2e/playwright.config.* 2>/dev/null || true
```

Determine which layers apply:
- **Frontend unit/integration (Vitest)**: root `package.json` has `vitest`
- **Backend unit/integration (Jest)**: `api/package.json` has `jest`
- **Python unit/integration (pytest)**: `pyproject.toml` or `requirements.txt` present
- **E2E/System (Playwright)**: `playwright.config.*` found anywhere
- **E2E/System (pytest)**: `tests/e2e/` directory found in Python project

---

## Phase 1 — Unit Tests

Run smallest-scope tests first to get fast feedback.

The detected unit layers (**Frontend Vitest / Backend Jest / Python pytest**) are
independent — they run in separate processes and share no state — so **fan them out** per
the `subagents` skill ladder (Workflow tool → parallel subagents → serial fallback)
instead of running them one after another. Each layer returns
`{ layer, status, passed, failed, coverage }`; the parent collects them. This is safe
*before* Phase 2 only — the integration phase shares one Docker DB and stays serial.

### Frontend unit (Vitest):
```bash
cd "$PROJECT_ROOT"
npm run test:unit 2>&1
```

### Backend unit (Jest):
```bash
cd "$PROJECT_ROOT/api"
npm run test:unit 2>&1
```

### Python unit (pytest):
```bash
cd "$PROJECT_ROOT"
./venv/bin/python -m pytest tests/unit/ -v --tb=short -q
```

**Test patterns:**
- Pure functions, hooks, utilities, lib modules
- Every code path: success, error, edge cases, empty input, boundary values
- Mock ONLY genuinely unreachable external services: Stripe, Firebase Auth, Resend, Twilio, AWS SES
- Do NOT mock the database — unit tests that need DB state should use the real test DB started in Phase 0
- Do NOT mock internal services, business logic, or utilities

---

## Phase 2 — Integration Tests

Run against real services started in Phase 0. No mocking of internal infrastructure.

### Frontend integration (Vitest):
```bash
cd "$PROJECT_ROOT"
npm run test:integration 2>&1
```

### Backend integration (Jest + supertest):
```bash
cd "$PROJECT_ROOT/api"
# Set test DB URL — real Docker DB from Phase 0
DATABASE_URL="${TEST_DATABASE_URL:-postgresql://test:test@localhost:5432/test}" \
REDIS_URL="${TEST_REDIS_URL:-redis://localhost:6379}" \
npm run test:integration 2>&1
```

### Python integration (pytest):
```bash
cd "$PROJECT_ROOT"
DATABASE_URL="${TEST_DATABASE_URL:-postgresql+asyncpg://test:test@localhost:5432/test}" \
REDIS_URL="${TEST_REDIS_URL:-redis://localhost:6379}" \
./venv/bin/python -m pytest tests/integration/ -v --tb=short -q
```

**Test patterns:**
- Full HTTP request → response through the real app against a real DB
- Multi-component flows, context providers, routing, auth state
- Real DB reads and writes — assert actual persisted state, not mock return values
- Mock ONLY payment gateways (Stripe) and third-party external APIs (email providers, SMS)
- Do NOT mock: your own DB, Redis, internal queues, internal services

---

## Phase 3 — Coverage loop (unit + integration)

Run coverage for all detected stacks and loop until thresholds are met:

### Vitest (frontend):
```bash
cd "$PROJECT_ROOT"
npm run test:coverage 2>&1
```

### Jest (backend):
```bash
cd "$PROJECT_ROOT/api"
DATABASE_URL="${TEST_DATABASE_URL:-postgresql://test:test@localhost:5432/test}" \
npm run test:coverage 2>&1
```

### pytest (Python):
```bash
cd "$PROJECT_ROOT"
DATABASE_URL="${TEST_DATABASE_URL:-postgresql+asyncpg://test:test@localhost:5432/test}" \
./venv/bin/python -m pytest tests/unit/ tests/integration/ --cov=. --cov-report=term-missing -q
```

**Loop logic (bounded — never spin forever):**
1. Parse coverage output — find files below threshold, find failing tests
2. If all thresholds met AND zero failures → **exit loop ✅**
3. Otherwise, if fewer than **MAX_ITERATIONS (default 6)** iterations have run:
   - For each uncovered file: read it, write tests targeting uncovered lines/branches
   - For each failing test: fix the test or the underlying code
   - Re-run from top of loop
4. **If the cap is reached without converging → STOP and escalate.** Report the remaining
   gap (which files/metrics are short, which tests still fail) and ask the user whether to
   keep going, lower a threshold, or investigate a stuck test. Do not loop indefinitely on
   an unreachable target (e.g. coverage blocked by an untestable external dependency).

**Rules inside the loop:**
- Read the source file before writing tests — understand all code paths
- Test ALL paths: success, error, edge cases, auth failures, DB errors, empty state
- Write integration tests that assert real DB state — not mock return values
- Never skip, xfail, or comment-out failing tests — fix the code or the test
- Each iteration targets the files with lowest coverage first

---

## Phase 4 — E2E / System Tests (Docker full-stack)

Run after unit + integration pass. Spins up the complete application stack via Docker and runs real browser or API tests against it. **Zero mocks.**

### 4a. Split Playwright configs

Use **two separate Playwright config files** — one for component/browser-unit tests that don't need the full stack, and one for true E2E against docker compose:

```
playwright.config.ts          ← component tests (no server required)
playwright.e2e.config.ts      ← full-stack E2E (requires docker compose)
e2e/
  fixtures/
    auth.ts                   ← real auth fixture (creates user, gets JWT)
    api.ts                    ← API helper (authenticated HTTP client)
  smoke/
    health.spec.ts            ← health + smoke tests
  auth/
    login.spec.ts             ← real browser login flow
  agents/
    crud.spec.ts              ← real CRUD tests via browser
```

**`playwright.config.ts`** (component tests — no docker required):
```typescript
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './src',
  testMatch: '**/*.spec.ts',
  use: { baseURL: 'http://localhost:5173' },
  webServer: {
    command: 'npm run dev',
    port: 5173,
    reuseExistingServer: !process.env.CI,
  },
})
```

**`playwright.e2e.config.ts`** (full-stack E2E — requires `docker compose up`):
```typescript
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  timeout: 60_000,
  retries: process.env.CI ? 2 : 0,
  reporter: [['html', { open: 'never' }], ['list']],
  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
})
```

### 4b. Docker Compose E2E override file

Create `docker-compose.e2e.yml` to override production compose settings for E2E testing (seed data, test credentials, exposed ports):

```yaml
# docker-compose.e2e.yml
# Extend your base docker-compose.yml for E2E tests
# Usage: docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d

services:
  api:
    environment:
      - NODE_ENV=test
      - DATABASE_URL=postgresql://test:test@db:5432/testdb
      - REDIS_URL=redis://redis:6379
      - JWT_SECRET=e2e-test-secret-not-for-production
      - SEED_TEST_DATA=true          # trigger seed on startup
    ports:
      - "8000:8000"

  web:
    environment:
      - VITE_API_URL=http://localhost:8000
    ports:
      - "3000:3000"

  db:
    image: postgres:16          # match your base docker-compose.yml version — don't drift the test DB
    environment:
      POSTGRES_USER: test
      POSTGRES_PASSWORD: test
      POSTGRES_DB: testdb
    ports:
      - "5432:5432"
    tmpfs:
      - /var/lib/postgresql/data   # ephemeral — fast, discarded after tests

  redis:
    image: redis:7               # match your base docker-compose.yml version
    ports:
      - "6379:6379"
```

**Detection and start:**
```bash
cd "$PROJECT_ROOT"

# Prefer e2e override, fall back to test compose, fall back to main
if [ -f docker-compose.e2e.yml ] && [ -f docker-compose.yml ]; then
  COMPOSE_CMD="docker compose -f docker-compose.yml -f docker-compose.e2e.yml"
  echo "Starting E2E stack with override: docker-compose.e2e.yml"
elif [ -f docker-compose.test.yml ]; then
  COMPOSE_CMD="docker compose -f docker-compose.test.yml"
else
  COMPOSE_CMD="docker compose"
fi

$COMPOSE_CMD up -d --build --wait
$COMPOSE_CMD ps
```

### 4c–4g. Fixtures, smoke tests, auth E2E, CRUD E2E, running

See **[references/e2e-patterns.md](references/e2e-patterns.md)** for complete templates:
- API helper + real auth fixture
- Docker Compose E2E override file
- Smoke / health tests
- Real auth browser flows
- Real CRUD tests (browser → API → DB assertion)
- How to start the full stack and run in order

---

## When writing new tests

### Integration test — Python example (real DB, no mocks):
```python
import pytest
import httpx

@pytest.mark.asyncio
async def test_create_agent_persists(async_client: httpx.AsyncClient, db_session):
    # Act — call real API against real test DB
    response = await async_client.post("/api/agents", json={"name": "test-agent"})
    assert response.status_code == 201
    agent_id = response.json()["id"]

    # Assert — verify it actually persisted in the real DB
    row = await db_session.execute("SELECT name FROM agents WHERE id = $1", agent_id)
    assert row["name"] == "test-agent"
```

### Integration test — JS/TS example (real DB via supertest):
```typescript
it("POST /agents persists to database", async () => {
  const res = await request(app).post("/agents").send({ name: "test-agent" })
  expect(res.status).toBe(201)

  // Assert real DB state — not a mock return value
  const row = await db.query("SELECT name FROM agents WHERE id = $1", [res.body.id])
  expect(row.rows[0].name).toBe("test-agent")
})
```

### Unit test — mock only external APIs:
```python
# ✅ Correct: mock only the external payment API
async def test_create_subscription(monkeypatch):
    monkeypatch.setattr("stripe.Subscription.create", AsyncMock(return_value={"id": "sub_test"}))
    result = await billing_service.create_subscription(user_id=1, plan="pro")
    assert result.stripe_subscription_id == "sub_test"

# ❌ Wrong: don't mock the DB
async def test_create_subscription(monkeypatch):
    monkeypatch.setattr("db.session.add", MagicMock())  # Never do this
```

---

## Output at each coverage iteration

```
=== Test Iteration N ===
Frontend:  lines X% | functions X% | branches X%
Backend:   lines X% | functions X% | branches X%
Python:    lines X% | functions X% | branches X%
Failing:   N tests
Action:    [what's being written / fixed]
```

## Final output

```
=== /test Complete ===
Docker env:  ✅ services running | skipped (not needed)
Unit:        ✅ X passed
Integration: ✅ X passed (real DB)
Frontend:    lines ✅ X% | functions ✅ X% | branches ✅ X%
Backend:     lines ✅ X% | functions ✅ X% | branches ✅ X%
E2E:         ✅ X passed (full-stack Docker) | ⚠️ X failed (non-blocking) | skipped
Failures:    0 ✅
New files:   [list]
Status:      COVERAGE MET ✅
```
