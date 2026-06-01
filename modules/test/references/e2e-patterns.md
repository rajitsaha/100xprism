# E2E Test Patterns

Reference patterns used by the `/test` workflow. All patterns follow the real-over-mocked principle: create real users via the API, run against a real Docker stack, verify persistence in the DB.

---

## Auth Fixture

**`e2e/fixtures/api.ts`** — create test users and get tokens via real API calls:

```typescript
import { APIRequestContext } from '@playwright/test'

export async function createTestUser(
  request: APIRequestContext,
  overrides?: Partial<{ email: string; password: string; name: string }>
) {
  const email = overrides?.email ?? `test-${Date.now()}@example.com`
  const password = overrides?.password ?? 'TestPass123!'
  const name = overrides?.name ?? 'Test User'
  const res = await request.post('/api/auth/register', { data: { email, password, name } })
  if (!res.ok()) throw new Error(`User creation failed: ${await res.text()}`)
  return { email, password, name, id: (await res.json()).id }
}

export async function getAuthToken(
  request: APIRequestContext,
  email: string,
  password: string
): Promise<string> {
  const res = await request.post('/api/auth/login', { data: { email, password } })
  if (!res.ok()) throw new Error(`Login failed: ${await res.text()}`)
  return (await res.json()).token
}
```

**`e2e/fixtures/auth.ts`** — real auth fixture (actual login, not mocked JWT):

```typescript
import { test as base } from '@playwright/test'
import { createTestUser, getAuthToken } from './api'

type AuthFixtures = {
  authToken: string
  authenticatedPage: import('@playwright/test').Page
  testUser: { email: string; password: string; name: string; id: string }
}

export const test = base.extend<AuthFixtures>({
  testUser: async ({ request }, use) => {
    const user = await createTestUser(request)
    await use(user)
  },
  authToken: async ({ request, testUser }, use) => {
    const token = await getAuthToken(request, testUser.email, testUser.password)
    await use(token)
  },
  authenticatedPage: async ({ page, authToken }, use) => {
    await page.goto('/')
    await page.evaluate((token) => localStorage.setItem('authToken', token), authToken)
    await page.reload()
    await use(page)
  },
})

export { expect } from '@playwright/test'
```

---

## Smoke Tests

Run these first — fast gate before the full suite.

**`e2e/smoke/health.spec.ts`**:

```typescript
import { test, expect } from '@playwright/test'

test('API /health returns 200', async ({ request }) => {
  const res = await request.get('/health')
  expect(res.status()).toBe(200)
  expect((await res.json()).status).toBe('ok')
})

test('API /health/db confirms DB connection', async ({ request }) => {
  const res = await request.get('/health/db')
  expect(res.status()).toBe(200)
  expect((await res.json()).database).toBe('connected')
})

test('frontend loads without JS errors', async ({ page }) => {
  const errors: string[] = []
  page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()) })
  await page.goto('/')
  await expect(page).toHaveTitle(/your app name/i)
  expect(errors).toHaveLength(0)
})

test('login page renders', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByRole('button', { name: /sign in/i })).toBeVisible()
})
```

---

## Auth Flow Tests

**`e2e/auth/login.spec.ts`**:

```typescript
import { test, expect } from '../fixtures/auth'

test('user can register and land on dashboard', async ({ page, request }) => {
  const email = `e2e-${Date.now()}@example.com`
  const password = 'TestPass123!'

  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: /create account/i }).click()

  await expect(page).toHaveURL(/\/dashboard/)
  await expect(page.getByText(/welcome/i)).toBeVisible()

  // Confirm user exists in DB
  const token = await (await request.post('/api/auth/login', { data: { email, password } })).json()
  expect(token.token).toBeTruthy()
})

test('user can log in with real credentials', async ({ page, testUser }) => {
  await page.goto('/login')
  await page.getByLabel('Email').fill(testUser.email)
  await page.getByLabel('Password').fill(testUser.password)
  await page.getByRole('button', { name: /sign in/i }).click()

  await expect(page).toHaveURL(/\/dashboard/)
  await expect(page.getByText(testUser.name)).toBeVisible()
})

test('invalid credentials show error', async ({ page }) => {
  await page.goto('/login')
  await page.getByLabel('Email').fill('nobody@example.com')
  await page.getByLabel('Password').fill('wrongpassword')
  await page.getByRole('button', { name: /sign in/i }).click()

  await expect(page.getByRole('alert')).toContainText(/invalid/i)
  await expect(page).toHaveURL(/\/login/)
})
```

---

## CRUD Tests

Browser creates entity → API confirms it persisted. No mock return values.

**`e2e/[entity]/crud.spec.ts`** (adapt to your entity):

```typescript
import { test, expect } from '../fixtures/auth'

test('create entity via UI and verify persistence', async ({
  authenticatedPage: page, request, authToken,
}) => {
  await page.goto('/dashboard/entities')
  await page.getByRole('button', { name: /new/i }).click()

  const name = `Test Entity ${Date.now()}`
  await page.getByLabel('Name').fill(name)
  await page.getByRole('button', { name: /create/i }).click()

  await expect(page.getByText(name)).toBeVisible()

  // Confirm it actually persisted — not just UI state
  const res = await request.get('/api/entities', {
    headers: { Authorization: `Bearer ${authToken}` },
  })
  const items = await res.json()
  expect(items.find((i: { name: string }) => i.name === name)).toBeDefined()
})

test('edit entity updates DB record', async ({
  authenticatedPage: page, request, authToken,
}) => {
  const createRes = await request.post('/api/entities', {
    headers: { Authorization: `Bearer ${authToken}` },
    data: { name: 'Original Name' },
  })
  const item = await createRes.json()

  await page.goto(`/dashboard/entities/${item.id}`)
  await page.getByLabel('Name').clear()
  await page.getByLabel('Name').fill('Updated Name')
  await page.getByRole('button', { name: /save/i }).click()

  await expect(page.getByText('Updated Name')).toBeVisible()

  const fetchRes = await request.get(`/api/entities/${item.id}`, {
    headers: { Authorization: `Bearer ${authToken}` },
  })
  expect((await fetchRes.json()).name).toBe('Updated Name')
})

test('delete entity removes from DB', async ({
  authenticatedPage: page, request, authToken,
}) => {
  const createRes = await request.post('/api/entities', {
    headers: { Authorization: `Bearer ${authToken}` },
    data: { name: 'To Be Deleted' },
  })
  const item = await createRes.json()

  await page.goto('/dashboard/entities')
  await page.getByTestId(`entity-row-${item.id}`).getByRole('button', { name: /delete/i }).click()
  await page.getByRole('button', { name: /confirm/i }).click()

  await expect(page.getByText('To Be Deleted')).not.toBeVisible()

  const fetchRes = await request.get(`/api/entities/${item.id}`, {
    headers: { Authorization: `Bearer ${authToken}` },
  })
  expect(fetchRes.status()).toBe(404)
})
```

---

## Running E2E

**Start full stack:**

```bash
if [ -f docker-compose.e2e.yml ] && [ -f docker-compose.yml ]; then
  COMPOSE_CMD="docker compose -f docker-compose.yml -f docker-compose.e2e.yml"
elif [ -f docker-compose.test.yml ]; then
  COMPOSE_CMD="docker compose -f docker-compose.test.yml"
else
  COMPOSE_CMD="docker compose"
fi
$COMPOSE_CMD up -d --build --wait
```

**Wait for app health:**

```bash
for i in $(seq 1 12); do
  curl -sf http://localhost:3000/health 2>/dev/null && break ||
  curl -sf http://localhost:8000/health 2>/dev/null && break ||
  { echo "Attempt $i/12 — waiting..."; sleep 5; }
done
```

**Run smoke first, then full suite:**

```bash
# 1. Smoke gate (fast)
npx playwright test e2e/smoke/ --config=playwright.e2e.config.ts

# 2. Full suite
npx playwright test --config=playwright.e2e.config.ts

# Against staging or prod
BASE_URL=https://staging.example.com npx playwright test --config=playwright.e2e.config.ts
```

**Python projects:**

```bash
BASE_URL="${E2E_BASE_URL:-http://localhost:8000}" \
  ./venv/bin/pytest tests/e2e/ -v --tb=short
```

**Teardown:**

```bash
$COMPOSE_CMD down -v 2>/dev/null || true
```

---

## What to Mock (and What Not To)

```python
# ✅ Mock this — genuinely unreachable locally
monkeypatch.setattr("stripe.PaymentIntent.create", AsyncMock(return_value={"id": "pi_test"}))
monkeypatch.setattr("sendgrid.send", AsyncMock(return_value=None))

# ❌ Never mock this — use a real Docker DB instead
monkeypatch.setattr("db.session.add", MagicMock())     # masks constraint violations
monkeypatch.setattr("db.session.commit", MagicMock())  # masks transaction failures
monkeypatch.setattr("redis_client.get", MagicMock())   # masks TTL/serialisation bugs
```
