---
name: gate
description: **MANDATORY before every commit and push.** All gates must pass — Gates 1–3 always run; Gates 4 (Docker) and 5 (Cloud) run only when applicable. Do NOT proceed if any gate fails — fix the issue first.
category: quality
tier: core
slash_command: /gate
---

# Gate — Pre-Commit Quality Gate

**MANDATORY before every commit and push.** All gates must pass — Gates 1–3 always run; Gates 4 (Docker) and 5 (Cloud) run only when applicable. Do NOT proceed if any gate fails — fix the issue first.

## Do NOT ask for permission. Do NOT skip gates. Do NOT continue past a failing gate.

---

## Gate 1 — Test Coverage (≥ 95%)

Run the **test** workflow. Run ALL test layers (unit, integration, E2E if configured).

Requirements:
- Lines ≥ 95% | Functions ≥ 95% | Statements ≥ 95% | Branches ≥ 90%
- Zero failing tests

**If coverage is below threshold or tests fail → STOP. Fix tests. Re-run gate.**

```
Gate 1: Tests ✅ PASSED | ❌ FAILED — do not proceed
```

---

## Gate 2 — Security (No Critical, No High)

Run the **security** workflow. Scan all package managers found in this project.

Requirements:
- **Zero critical vulnerabilities**
- **Zero high vulnerabilities**
- No real secrets in tracked files

Known exceptions accepted **only if explicitly documented** in the project's `/security` override (e.g. install-time-only transitive deps). When in doubt, treat as blocking.

**If critical or high vulnerabilities exist → STOP. Fix vulnerabilities. Re-run gate.**

```
Gate 2: Security ✅ PASSED | ❌ FAILED — do not proceed
```

---

## Gate 3 — Local Build

Detect and run all applicable builds:

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT"
```

**npm frontend** (if `package.json` + `vite`/`webpack`/`next` detected):
```bash
npm run build
```

**npm backend** (if `api/package.json` detected):
```bash
cd api && npm run build
```

**Python** (if `pyproject.toml` detected):
```bash
./venv/bin/python -m build 2>/dev/null || ./venv/bin/python -c "import py_compile, glob; [py_compile.compile(f, doraise=True) for f in glob.glob('**/*.py', recursive=True) if 'venv' not in f]"
```

Requirements:
- Zero compiler errors
- Zero TypeScript type errors (for TS projects)

**If any build fails → STOP. Fix compiler errors. Re-run gate.**

```
Gate 3: Build ✅ PASSED | ❌ FAILED — do not proceed
```

---

## Gate 4 — Docker Build (if Dockerfile present)

```bash
if [ -f "$PROJECT_ROOT/Dockerfile" ]; then
  docker build -t $(basename "$PROJECT_ROOT"):gate-check . --quiet
  echo "Docker build: ✅"
else
  echo "Docker build: skipped (no Dockerfile)"
fi
```

If the project has a `docker-compose.yml`, also verify it starts cleanly:
```bash
if [ -f "$PROJECT_ROOT/deploy/docker-compose.yml" ] || [ -f "$PROJECT_ROOT/docker-compose.yml" ]; then
  COMPOSE_FILE=${PROJECT_ROOT}/deploy/docker-compose.yml
  [ -f "$COMPOSE_FILE" ] || COMPOSE_FILE=${PROJECT_ROOT}/docker-compose.yml
  docker compose -f "$COMPOSE_FILE" config --quiet && echo "Compose config: ✅"
fi
```

**If Docker build fails → STOP. Fix the Dockerfile or dependency issue. Re-run gate.**

```
Gate 4: Docker ✅ PASSED | skipped (no Dockerfile) | ❌ FAILED — do not proceed
```

---

## Gate 5 — Cloud Security & Data Privacy (if cloud project)

Detect whether this project deploys to a cloud provider:

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
IS_CLOUD_PROJECT=false

# Detect project instruction file
INSTRUCTION_FILE=$(for f in CLAUDE.md AGENTS.md .cursorrules .windsurfrules .github/copilot-instructions.md GEMINI.md; do [ -f "$PROJECT_ROOT/$f" ] && echo "$PROJECT_ROOT/$f" && break; done)

# GCP: check for gcloud config, Terraform, or project instruction file references
if [ -n "$INSTRUCTION_FILE" ] && grep -qE "gcloud|GCP_PROJECT|GOOGLE_CLOUD_PROJECT|Cloud Run|Cloud SQL|Firebase" \
  "$INSTRUCTION_FILE" "$PROJECT_ROOT/.env.example" 2>/dev/null; then
  IS_CLOUD_PROJECT=true
fi

# AWS: check for aws-cli config or CDK/Terraform
if ls "$PROJECT_ROOT/terraform/" "$PROJECT_ROOT/infra/" "$PROJECT_ROOT/cdk.json" 2>/dev/null | grep -qE "aws|cdk"; then
  IS_CLOUD_PROJECT=true
fi

echo "Cloud project: $IS_CLOUD_PROJECT"
```

**If `IS_CLOUD_PROJECT=true`:** Run the **cloud-security** workflow. Run the full cloud security and data privacy scan.

Requirements:
- **Zero CRITICAL findings** (public data exposure, open credentials, public storage, SQL injection)
- **Zero HIGH findings** (missing SSL, overprivileged IAM, PII in logs, missing auth headers, eval() usage)
- MEDIUM/LOW findings: reported, non-blocking, must be tracked

**If any CRITICAL or HIGH finding → STOP. Fix before committing. Cloud misconfigurations can expose user data.**

**If `IS_CLOUD_PROJECT=false`:** Gate 5 is skipped.

```
Gate 5: Cloud Security ✅ PASSED | ❌ FAILED — do not proceed | skipped (local only)
```

---

## Gate summary output

```
╔══════════════════════════════════════════════════════╗
║               QUALITY GATE RESULTS                   ║
╠══════════════════════════════════════════════════════╣
║ Gate 1 Tests:          ✅ PASSED  (FE 97% | BE 96%) ║
║ Gate 2 Security:       ✅ PASSED  (0 critical, 0 high) ║
║ Gate 3 Build:          ✅ PASSED  (FE ✅ | BE ✅)   ║
║ Gate 4 Docker:         ✅ PASSED  | skipped          ║
║ Gate 5 Cloud/Privacy:  ✅ PASSED  | skipped          ║
╠══════════════════════════════════════════════════════╣
║ STATUS: ✅ ALL GATES PASSED — safe to commit         ║
╚══════════════════════════════════════════════════════╝
```

If ANY gate fails:
```
╔══════════════════════════════════════════════════════╗
║               QUALITY GATE RESULTS                   ║
╠══════════════════════════════════════════════════════╣
║ Gate 1 Tests:          ❌ FAILED  (BE 88%)           ║
║ Gate 2 Security:       ✅ PASSED                     ║
║ Gate 3 Build:          ✅ PASSED                     ║
║ Gate 4 Docker:         skipped                       ║
║ Gate 5 Cloud/Privacy:  ❌ FAILED  (1 CRITICAL)       ║
╠══════════════════════════════════════════════════════╣
║ STATUS: ❌ GATE FAILED — fix issues before commit    ║
╚══════════════════════════════════════════════════════╝
```

**Do NOT commit or push until the gate summary shows ALL GATES PASSED.**
