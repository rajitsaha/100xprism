# _lib — Shared Workflow Conventions

<!-- Reference only — maintainer source of truth. This is reference.md, NOT SKILL.md, so
     it is never globbed by adapters/lib/modules.py, never emitted to any platform, and
     never counted as a module. Skills embed the relevant block verbatim (each skill is
     emitted standalone to 7 platforms, so a sourced/cat'd runtime file is not reliably
     present). Keep the canonical copy here; update embedders when it changes. -->

## Standard preamble (paste into every workflow's first bash block)

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel); cd "$PROJECT_ROOT"
INSTRUCTION_FILE=$(for f in CLAUDE.md AGENTS.md .cursorrules .windsurfrules .github/copilot-instructions.md GEMINI.md; do [ -f "$PROJECT_ROOT/$f" ] && echo "$PROJECT_ROOT/$f" && break; done)
```

## Stack detection (paste where a workflow needs the cloud/test/CI stack)

Detect the project's stack **once**, then branch on the variables — never hardcode
GCP/Firebase/Stripe/npm. Detection is lightweight (config + lockfiles + IaC +
instruction-file greps); **no network calls**. Empty string = "not detected / not
applicable" → skip that provider's section rather than emitting a broken command.

```bash
# --- Cloud provider: gcp | aws | azure | vercel | "" ---------------------------------
CLOUD=""
if command -v gcloud >/dev/null 2>&1 && gcloud config get-value project >/dev/null 2>&1; then CLOUD=gcp; fi
[ -z "$CLOUD" ] && ls "$PROJECT_ROOT"/.vercel >/dev/null 2>&1 && CLOUD=vercel
[ -z "$CLOUD" ] && grep -rqiE "aws_|amazonaws|cdk\.json|::aws" "$PROJECT_ROOT"/terraform "$PROJECT_ROOT"/infra "$PROJECT_ROOT"/cdk.json 2>/dev/null && CLOUD=aws
[ -z "$CLOUD" ] && grep -rqiE "azurerm|azure-|microsoft\.web" "$PROJECT_ROOT"/terraform "$PROJECT_ROOT"/infra 2>/dev/null && CLOUD=azure
# Fall back to whatever the instruction file names, if anything.
if [ -z "$CLOUD" ] && [ -n "$INSTRUCTION_FILE" ]; then
  grep -qiE "gcloud|Cloud Run|Cloud SQL|Firebase|GCP_PROJECT" "$INSTRUCTION_FILE" && CLOUD=gcp
  [ -z "$CLOUD" ] && grep -qiE "\baws\b|lambda|dynamodb|ecs|fargate" "$INSTRUCTION_FILE" && CLOUD=aws
  [ -z "$CLOUD" ] && grep -qiE "vercel" "$INSTRUCTION_FILE" && CLOUD=vercel
fi

# --- Test runner: jest | vitest | pytest | "go test" | "cargo test" | "" --------------
TEST_RUNNER=""
if [ -f "$PROJECT_ROOT/package.json" ]; then
  grep -q '"vitest"' "$PROJECT_ROOT"/package.json "$PROJECT_ROOT"/*/package.json 2>/dev/null && TEST_RUNNER=vitest
  [ -z "$TEST_RUNNER" ] && grep -q '"jest"' "$PROJECT_ROOT"/package.json "$PROJECT_ROOT"/*/package.json 2>/dev/null && TEST_RUNNER=jest
fi
[ -z "$TEST_RUNNER" ] && { [ -f "$PROJECT_ROOT/pyproject.toml" ] || ls "$PROJECT_ROOT"/requirements*.txt >/dev/null 2>&1; } && TEST_RUNNER=pytest
[ -z "$TEST_RUNNER" ] && [ -f "$PROJECT_ROOT/go.mod" ] && TEST_RUNNER="go test"
[ -z "$TEST_RUNNER" ] && [ -f "$PROJECT_ROOT/Cargo.toml" ] && TEST_RUNNER="cargo test"

# --- Package manager (JS): pnpm | yarn | npm | "" -------------------------------------
JS_PM=""
[ -f "$PROJECT_ROOT/pnpm-lock.yaml" ] && JS_PM=pnpm
[ -z "$JS_PM" ] && [ -f "$PROJECT_ROOT/yarn.lock" ] && JS_PM=yarn
[ -z "$JS_PM" ] && [ -f "$PROJECT_ROOT/package-lock.json" ] && JS_PM=npm

# --- CI system: github-actions | gitlab | circle | "" ---------------------------------
CI_SYSTEM=""
ls "$PROJECT_ROOT"/.github/workflows/*.y*ml >/dev/null 2>&1 && CI_SYSTEM=github-actions
[ -z "$CI_SYSTEM" ] && [ -f "$PROJECT_ROOT/.gitlab-ci.yml" ] && CI_SYSTEM=gitlab
[ -z "$CI_SYSTEM" ] && [ -d "$PROJECT_ROOT/.circleci" ] && CI_SYSTEM=circle

echo "stack: cloud=${CLOUD:-none} test=${TEST_RUNNER:-none} js_pm=${JS_PM:-none} ci=${CI_SYSTEM:-none}"
```

## Autonomy banner
Add to any workflow that operates without user prompting:
```
## Do NOT ask for permission — [action]. Do NOT stop until done.
```

## GATE line format
```
**GATE: [Condition that must be true before proceeding.]**
```

## SaaS credentials
When a workflow requires cloud or SaaS credentials, reference the **connect** workflow:
- Run `/connect` to see status of all services
- Run `/connect <service>` to install CLI + authenticate a specific service
- Credentials are stored in `.env` (copy `.env.example` to start)
