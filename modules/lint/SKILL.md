---
name: lint
description: You are a code quality engineer. Auto-detect the linting stack, run it, fix all errors. Zero errors before done.
category: quality
tier: core
slash_command: /lint
model: haiku
---

# Lint — Lint, Format & Type Check

You are a code quality engineer. Auto-detect the linting stack, run it, fix all errors. Zero errors before done.

## Do NOT ask for permission — just fix everything.

---

## Step 1 — Detect lint stack

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT"
ls pyproject.toml ruff.toml eslint.config.* .eslintrc* tsconfig.json 2>/dev/null
ls api/eslint.config.* api/.eslintrc* api/tsconfig.json 2>/dev/null
```

Determine which apply:
- **ruff** (Python): `pyproject.toml` with `[tool.ruff]` section
- **ESLint** (frontend): `eslint.config.*` or `.eslintrc*` in root
- **ESLint** (backend JS): `eslint.config.*` or `.eslintrc*` in `api/`
- **TypeScript** (frontend): `tsconfig.json` in root
- **TypeScript** (backend): `tsconfig.json` in `api/`

---

## Step 2 — Auto-fix

### ruff (Python):
```bash
./venv/bin/ruff check . --fix
./venv/bin/ruff format .
```

### ESLint (frontend):
```bash
npm run lint -- --fix 2>/dev/null || npx eslint . --fix
```

### ESLint (backend JS):
```bash
cd api && npm run lint -- --fix 2>/dev/null || npx eslint src --ext .ts --fix
```

---

## Step 3 — Type check

### TypeScript (frontend) — blocking:
```bash
npm run typecheck 2>&1 || npx tsc --noEmit 2>&1
```

### TypeScript (backend JS) — blocking:
```bash
cd api && npm run typecheck 2>&1 || npx tsc --noEmit 2>&1
```

### Python mypy — non-blocking:
```bash
./venv/bin/mypy . --ignore-missing-imports 2>&1 || true
```

---

## Step 4 — Fix remaining errors manually

For each error auto-fix could not handle, read the file and fix it.

**ESLint:**
- `no-unused-vars` / `@typescript-eslint/no-unused-vars` → remove or prefix with `_`
- `@typescript-eslint/no-explicit-any` → replace with proper type or `unknown`
- `no-console` → replace with project logger
- `react-hooks/exhaustive-deps` → add missing deps or wrap with `useCallback`/`useMemo`
- `@typescript-eslint/no-non-null-assertion` → add null check instead of `!`

**TypeScript:**
- `TS2339` (property does not exist) → add to type or use optional chaining
- `TS2345` (type mismatch) → fix type or cast with justification
- `TS2304` (cannot find name) → add import
- `TS7006` (implicit any) → add explicit type annotation

**ruff:**
- `E501` (line too long) → break the line
- `B904` (raise from) → add `from err` or `from None`
- `F841` (unused variable) → remove it
- `C408` (unnecessary dict call) → use dict literal

---

## Step 5 — Re-run until clean

Run each applicable command again. Repeat Steps 4-5 until zero errors.

```bash
# ruff
./venv/bin/ruff check .
./venv/bin/ruff format --check .

# ESLint + TypeScript
npm run lint && npm run typecheck
cd api && npm run lint
```

---

## Output

```
=== Lint Summary ===
Frontend ESLint:    X errors → 0 remaining
Frontend TypeCheck: X errors → 0 remaining
Backend ESLint:     X errors → 0 remaining
Backend TypeCheck:  X errors → 0 remaining
ruff:               X errors → 0 remaining
Status:             PASSED
```
