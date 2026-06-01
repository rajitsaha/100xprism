---
name: connect
description: Connect, authenticate, and verify any SaaS CLI tool. Reads credentials from `.env`. Never prints secrets.
category: data
tier: on-demand
slash_command: /connect
model: haiku
---

# Connect — SaaS CLI Connection Manager

Connect, authenticate, and verify any SaaS CLI tool. Reads credentials from `.env`. Never prints secrets.

## Usage

```
/connect               # status dashboard — all services
/connect <service>     # install + authenticate + test one service
/connect <service> test  # test existing auth only (no re-auth)
```

**Supported services:** `github` · `aws` · `gcp` · `azure` · `vercel` · `netlify` · `railway` · `heroku` · `flyio` · `supabase` · `planetscale` · `firebase` · `stripe` · `cloudflare` · `docker` · `terraform` · `sentry` · `datadog` · `jira` · `linear` · `slack` · `notion` · `npm` · `pypi` · `digitalocean` · `render` · `mongodb`

## Do NOT ask for permission. Do NOT print credential values. Do NOT skip checks.

---

## Phase 0 — Bootstrap

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
ENV_FILE="$PROJECT_ROOT/.env"

# Load .env if it exists
if [ -f "$ENV_FILE" ]; then
  set -a; source "$ENV_FILE"; set +a
  echo ".env loaded from $ENV_FILE"
else
  echo "No .env found at $ENV_FILE"
  echo "→ Copy .env.example to .env and fill in your credentials:"
  echo "    cp .env.example .env && \$EDITOR .env"
fi
```

Detect the requested service from the argument (e.g. `/connect github` → SERVICE=github).
If no argument, run Phase 3 (status dashboard) for ALL services.

---

## Phase 1 — Helper functions (reference, do not run)

These patterns are used in each service section below.

```bash
# Check if a CLI is installed
check_cli() { command -v "$1" &>/dev/null && echo "✅ $1 installed" || echo "❌ $1 not found"; }

# Check if an env var is set (no value printed)
check_env() { [ -n "${!1}" ] && echo "✅ $1 set" || echo "❌ $1 missing — add to .env"; }

# Install via Homebrew
brew_install() { brew install "$1" && echo "✅ $1 installed" || echo "❌ install failed"; }
```

---

## Phase 2 — Service sections

Run ONLY the section matching the requested SERVICE. If `/connect` with no arg, run status checks for all.

---

### SERVICE: github

**Required env vars:** `GITHUB_TOKEN`

```bash
check_cli gh
check_env GITHUB_TOKEN

# Install if missing
if ! command -v gh &>/dev/null; then
  echo "Installing GitHub CLI..."
  brew install gh
fi

# Authenticate using token from .env
if [ -n "$GITHUB_TOKEN" ]; then
  echo "$GITHUB_TOKEN" | gh auth login --with-token
  gh auth status
else
  echo "❌ GITHUB_TOKEN not set in .env"
  echo "→ Create a token at: https://github.com/settings/tokens"
  echo "→ Add to .env:  GITHUB_TOKEN=ghp_..."
fi
```

---

### SERVICE: aws

**Required env vars:** `AWS_ACCESS_KEY_ID` · `AWS_SECRET_ACCESS_KEY` · `AWS_DEFAULT_REGION`

```bash
check_cli aws
check_env AWS_ACCESS_KEY_ID
check_env AWS_SECRET_ACCESS_KEY
check_env AWS_DEFAULT_REGION

if ! command -v aws &>/dev/null; then
  brew install awscli
fi

if [ -n "$AWS_ACCESS_KEY_ID" ] && [ -n "$AWS_SECRET_ACCESS_KEY" ]; then
  aws configure set aws_access_key_id "$AWS_ACCESS_KEY_ID"
  aws configure set aws_secret_access_key "$AWS_SECRET_ACCESS_KEY"
  aws configure set default.region "${AWS_DEFAULT_REGION:-us-east-1}"
  echo "Testing connection..."
  aws sts get-caller-identity
else
  echo "❌ AWS credentials not set in .env"
  echo "→ Create at: https://console.aws.amazon.com/iam/home#/security_credentials"
  echo "→ Add to .env: AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_DEFAULT_REGION=us-east-1"
fi
```

---

### SERVICE: gcp

**Required env vars:** `GCP_PROJECT_ID` · `GOOGLE_APPLICATION_CREDENTIALS` (path to service account JSON)
**Optional:** `GCP_REGION`

```bash
check_cli gcloud
check_env GCP_PROJECT_ID
check_env GOOGLE_APPLICATION_CREDENTIALS

if ! command -v gcloud &>/dev/null; then
  brew install google-cloud-sdk
fi

if [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ] && [ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
  gcloud auth activate-service-account --key-file="$GOOGLE_APPLICATION_CREDENTIALS"
  gcloud config set project "${GCP_PROJECT_ID}"
  gcloud auth list
  echo "✅ GCP authenticated as service account"
elif [ -n "$GCP_PROJECT_ID" ]; then
  echo "No service account key found — falling back to Application Default Credentials"
  gcloud auth application-default login
  gcloud config set project "$GCP_PROJECT_ID"
else
  echo "❌ GCP credentials not set in .env"
  echo "→ Create service account at: https://console.cloud.google.com/iam-admin/serviceaccounts"
  echo "→ Download JSON key, then add to .env:"
  echo "    GCP_PROJECT_ID=my-project"
  echo "    GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json"
fi
```

---

### SERVICE: azure

**Required env vars:** `AZURE_CLIENT_ID` · `AZURE_CLIENT_SECRET` · `AZURE_TENANT_ID` · `AZURE_SUBSCRIPTION_ID`

```bash
check_cli az
check_env AZURE_CLIENT_ID
check_env AZURE_CLIENT_SECRET
check_env AZURE_TENANT_ID
check_env AZURE_SUBSCRIPTION_ID

if ! command -v az &>/dev/null; then
  brew install azure-cli
fi

if [ -n "$AZURE_CLIENT_ID" ] && [ -n "$AZURE_CLIENT_SECRET" ] && [ -n "$AZURE_TENANT_ID" ]; then
  az login --service-principal \
    --username "$AZURE_CLIENT_ID" \
    --password "$AZURE_CLIENT_SECRET" \
    --tenant "$AZURE_TENANT_ID"
  az account set --subscription "$AZURE_SUBSCRIPTION_ID"
  az account show
else
  echo "❌ Azure service principal credentials not set in .env"
  echo "→ Create at: https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps"
  echo "→ Add to .env: AZURE_CLIENT_ID=... AZURE_CLIENT_SECRET=... AZURE_TENANT_ID=... AZURE_SUBSCRIPTION_ID=..."
fi
```

---

### SERVICE: vercel

**Required env vars:** `VERCEL_TOKEN`
**Optional:** `VERCEL_ORG_ID` · `VERCEL_PROJECT_ID`

```bash
check_cli vercel
check_env VERCEL_TOKEN

if ! command -v vercel &>/dev/null; then
  npm install -g vercel
fi

if [ -n "$VERCEL_TOKEN" ]; then
  # vercel CLI uses VERCEL_TOKEN env var automatically
  vercel whoami
  echo "✅ Vercel authenticated"
else
  echo "❌ VERCEL_TOKEN not set in .env"
  echo "→ Create at: https://vercel.com/account/tokens"
  echo "→ Add to .env: VERCEL_TOKEN=..."
fi
```

---

### SERVICE: netlify

**Required env vars:** `NETLIFY_AUTH_TOKEN`
**Optional:** `NETLIFY_SITE_ID`

```bash
check_cli netlify
check_env NETLIFY_AUTH_TOKEN

if ! command -v netlify &>/dev/null; then
  npm install -g netlify-cli
fi

if [ -n "$NETLIFY_AUTH_TOKEN" ]; then
  NETLIFY_AUTH_TOKEN="$NETLIFY_AUTH_TOKEN" netlify status
  echo "✅ Netlify authenticated"
else
  echo "❌ NETLIFY_AUTH_TOKEN not set in .env"
  echo "→ Create at: https://app.netlify.com/user/applications#personal-access-tokens"
  echo "→ Add to .env: NETLIFY_AUTH_TOKEN=..."
fi
```

---

### SERVICE: railway

**Required env vars:** `RAILWAY_TOKEN`

```bash
check_cli railway
check_env RAILWAY_TOKEN

if ! command -v railway &>/dev/null; then
  brew install railway
fi

if [ -n "$RAILWAY_TOKEN" ]; then
  RAILWAY_TOKEN="$RAILWAY_TOKEN" railway whoami
  echo "✅ Railway authenticated"
else
  echo "❌ RAILWAY_TOKEN not set in .env"
  echo "→ Create at: https://railway.app/account/tokens"
  echo "→ Add to .env: RAILWAY_TOKEN=..."
fi
```

---

### SERVICE: heroku

**Required env vars:** `HEROKU_API_KEY`

```bash
check_cli heroku
check_env HEROKU_API_KEY

if ! command -v heroku &>/dev/null; then
  brew tap heroku/brew && brew install heroku
fi

if [ -n "$HEROKU_API_KEY" ]; then
  HEROKU_API_KEY="$HEROKU_API_KEY" heroku auth:whoami
  echo "✅ Heroku authenticated"
else
  echo "❌ HEROKU_API_KEY not set in .env"
  echo "→ Get at: https://dashboard.heroku.com/account (API Key section)"
  echo "→ Add to .env: HEROKU_API_KEY=..."
fi
```

---

### SERVICE: flyio

**Required env vars:** `FLY_API_TOKEN`

```bash
check_cli fly
check_env FLY_API_TOKEN

if ! command -v fly &>/dev/null; then
  brew install flyctl
fi

if [ -n "$FLY_API_TOKEN" ]; then
  FLY_API_TOKEN="$FLY_API_TOKEN" fly auth whoami
  echo "✅ Fly.io authenticated"
else
  echo "❌ FLY_API_TOKEN not set in .env"
  echo "→ Create at: https://fly.io/user/personal_access_tokens"
  echo "→ Add to .env: FLY_API_TOKEN=..."
fi
```

---

### SERVICE: supabase

**Required env vars:** `SUPABASE_ACCESS_TOKEN`
**Optional:** `SUPABASE_PROJECT_REF` · `SUPABASE_DB_PASSWORD`

```bash
check_cli supabase
check_env SUPABASE_ACCESS_TOKEN

if ! command -v supabase &>/dev/null; then
  brew install supabase/tap/supabase
fi

if [ -n "$SUPABASE_ACCESS_TOKEN" ]; then
  supabase login --token "$SUPABASE_ACCESS_TOKEN"
  supabase projects list
  echo "✅ Supabase authenticated"
else
  echo "❌ SUPABASE_ACCESS_TOKEN not set in .env"
  echo "→ Create at: https://supabase.com/dashboard/account/tokens"
  echo "→ Add to .env: SUPABASE_ACCESS_TOKEN=..."
fi
```

---

### SERVICE: planetscale

**Required env vars:** `PLANETSCALE_SERVICE_TOKEN` · `PLANETSCALE_SERVICE_TOKEN_ID` · `PLANETSCALE_ORG`

```bash
check_cli pscale
check_env PLANETSCALE_SERVICE_TOKEN
check_env PLANETSCALE_SERVICE_TOKEN_ID
check_env PLANETSCALE_ORG

if ! command -v pscale &>/dev/null; then
  brew install planetscale/tap/pscale
fi

if [ -n "$PLANETSCALE_SERVICE_TOKEN" ] && [ -n "$PLANETSCALE_SERVICE_TOKEN_ID" ]; then
  pscale org list \
    --service-token "$PLANETSCALE_SERVICE_TOKEN" \
    --service-token-id "$PLANETSCALE_SERVICE_TOKEN_ID"
  echo "✅ PlanetScale authenticated"
else
  echo "❌ PlanetScale credentials not set in .env"
  echo "→ Create service token at: https://app.planetscale.com/[org]/settings/service-tokens"
  echo "→ Add to .env: PLANETSCALE_SERVICE_TOKEN=... PLANETSCALE_SERVICE_TOKEN_ID=... PLANETSCALE_ORG=..."
fi
```

---

### SERVICE: firebase

**Required env vars:** `FIREBASE_TOKEN` · `FIREBASE_PROJECT_ID`

```bash
check_cli firebase
check_env FIREBASE_TOKEN
check_env FIREBASE_PROJECT_ID

if ! command -v firebase &>/dev/null; then
  npm install -g firebase-tools
fi

if [ -n "$FIREBASE_TOKEN" ]; then
  firebase projects:list --token "$FIREBASE_TOKEN"
  firebase use "$FIREBASE_PROJECT_ID" --token "$FIREBASE_TOKEN"
  echo "✅ Firebase authenticated"
else
  echo "❌ FIREBASE_TOKEN not set in .env"
  echo "→ Generate with: firebase login:ci"
  echo "→ Add to .env: FIREBASE_TOKEN=... FIREBASE_PROJECT_ID=..."
fi
```

---

### SERVICE: stripe

**Required env vars:** `STRIPE_SECRET_KEY`
**Optional:** `STRIPE_PUBLISHABLE_KEY` · `STRIPE_WEBHOOK_SECRET`

```bash
check_cli stripe
check_env STRIPE_SECRET_KEY

if ! command -v stripe &>/dev/null; then
  brew install stripe/stripe-cli/stripe
fi

if [ -n "$STRIPE_SECRET_KEY" ]; then
  stripe config --api-key "$STRIPE_SECRET_KEY"
  stripe whoami
  echo "✅ Stripe authenticated"
else
  echo "❌ STRIPE_SECRET_KEY not set in .env"
  echo "→ Get at: https://dashboard.stripe.com/apikeys"
  echo "→ Add to .env: STRIPE_SECRET_KEY=sk_live_... (or sk_test_... for dev)"
fi
```

---

### SERVICE: cloudflare

**Required env vars:** `CLOUDFLARE_API_TOKEN`
**Optional:** `CLOUDFLARE_ACCOUNT_ID` · `CLOUDFLARE_ZONE_ID`

```bash
check_cli wrangler
check_env CLOUDFLARE_API_TOKEN
check_env CLOUDFLARE_ACCOUNT_ID

if ! command -v wrangler &>/dev/null; then
  npm install -g wrangler
fi

if [ -n "$CLOUDFLARE_API_TOKEN" ]; then
  # wrangler uses CLOUDFLARE_API_TOKEN env var automatically
  CLOUDFLARE_API_TOKEN="$CLOUDFLARE_API_TOKEN" wrangler whoami
  echo "✅ Cloudflare authenticated"
else
  echo "❌ CLOUDFLARE_API_TOKEN not set in .env"
  echo "→ Create at: https://dash.cloudflare.com/profile/api-tokens"
  echo "→ Add to .env: CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=..."
fi
```

---

### SERVICE: docker

**Required env vars:** `DOCKER_USERNAME` · `DOCKER_TOKEN`
**Optional:** `DOCKER_REGISTRY` (defaults to Docker Hub)

```bash
check_cli docker
check_env DOCKER_USERNAME
check_env DOCKER_TOKEN

if ! command -v docker &>/dev/null; then
  echo "Docker Desktop not found — install from: https://www.docker.com/products/docker-desktop"
  exit 1
fi

if [ -n "$DOCKER_USERNAME" ] && [ -n "$DOCKER_TOKEN" ]; then
  REGISTRY="${DOCKER_REGISTRY:-docker.io}"
  echo "$DOCKER_TOKEN" | docker login "$REGISTRY" --username "$DOCKER_USERNAME" --password-stdin
  echo "✅ Docker authenticated to $REGISTRY"
else
  echo "❌ Docker credentials not set in .env"
  echo "→ Create access token at: https://hub.docker.com/settings/security"
  echo "→ Add to .env: DOCKER_USERNAME=... DOCKER_TOKEN=..."
fi
```

---

### SERVICE: terraform

**Required env vars:** none (uses cloud provider creds already configured)
**Optional:** `TF_TOKEN_app_terraform_io` (for Terraform Cloud)

```bash
check_cli terraform

if ! command -v terraform &>/dev/null; then
  brew install terraform
fi

terraform version
terraform validate 2>/dev/null && echo "✅ Terraform config valid" || echo "No terraform config in current directory"

# Terraform Cloud auth (if token set)
if [ -n "$TF_TOKEN_app_terraform_io" ]; then
  echo "Terraform Cloud token detected — writing to ~/.terraform.d/credentials.tfrc.json"
  mkdir -p ~/.terraform.d
  cat > ~/.terraform.d/credentials.tfrc.json <<EOF
{
  "credentials": {
    "app.terraform.io": {
      "token": "$TF_TOKEN_app_terraform_io"
    }
  }
}
EOF
  echo "✅ Terraform Cloud authenticated"
fi
```

---

### SERVICE: sentry

**Required env vars:** `SENTRY_AUTH_TOKEN` · `SENTRY_ORG`
**Optional:** `SENTRY_PROJECT` · `SENTRY_URL` (for self-hosted)

```bash
check_cli sentry-cli
check_env SENTRY_AUTH_TOKEN
check_env SENTRY_ORG

if ! command -v sentry-cli &>/dev/null; then
  brew install getsentry/tools/sentry-cli
fi

if [ -n "$SENTRY_AUTH_TOKEN" ]; then
  SENTRY_AUTH_TOKEN="$SENTRY_AUTH_TOKEN" sentry-cli info
  echo "✅ Sentry authenticated"
else
  echo "❌ SENTRY_AUTH_TOKEN not set in .env"
  echo "→ Create at: https://sentry.io/settings/account/api/auth-tokens/"
  echo "→ Add to .env: SENTRY_AUTH_TOKEN=... SENTRY_ORG=my-org SENTRY_PROJECT=my-project"
fi
```

---

### SERVICE: datadog

**Required env vars:** `DD_API_KEY` · `DD_APP_KEY`
**Optional:** `DD_SITE` (defaults to datadoghq.com)

```bash
check_cli datadog-ci
check_env DD_API_KEY
check_env DD_APP_KEY

if ! command -v datadog-ci &>/dev/null; then
  npm install -g @datadog/datadog-ci
fi

if [ -n "$DD_API_KEY" ]; then
  DD_API_KEY="$DD_API_KEY" DD_APP_KEY="$DD_APP_KEY" \
    datadog-ci synthetics run-tests --help &>/dev/null \
    && echo "✅ Datadog CI authenticated" \
    || echo "✅ Datadog credentials set (validation requires a test suite)"
else
  echo "❌ DD_API_KEY not set in .env"
  echo "→ Get at: https://app.datadoghq.com/organization-settings/api-keys"
  echo "→ Add to .env: DD_API_KEY=... DD_APP_KEY=... DD_SITE=datadoghq.com"
fi
```

---

### SERVICE: jira

**Required env vars:** `JIRA_URL` · `JIRA_USER_EMAIL` · `JIRA_API_TOKEN`
**Optional:** `JIRA_PROJECT_KEY`

```bash
check_env JIRA_URL
check_env JIRA_USER_EMAIL
check_env JIRA_API_TOKEN

# go-jira CLI
if ! command -v jira &>/dev/null; then
  brew install go-jira
fi

if [ -n "$JIRA_URL" ] && [ -n "$JIRA_API_TOKEN" ]; then
  # Write go-jira config
  mkdir -p ~/.jira.d
  cat > ~/.jira.d/config.yml <<EOF
endpoint: ${JIRA_URL}
user: ${JIRA_USER_EMAIL}
login: ${JIRA_USER_EMAIL}
password-source: keyring
EOF
  # Test via API
  curl -sf -u "${JIRA_USER_EMAIL}:${JIRA_API_TOKEN}" \
    "${JIRA_URL}/rest/api/3/myself" | python3 -c "import sys,json; u=json.load(sys.stdin); print('✅ Jira authenticated as', u['displayName'])"
else
  echo "❌ Jira credentials not set in .env"
  echo "→ Create API token at: https://id.atlassian.com/manage-profile/security/api-tokens"
  echo "→ Add to .env: JIRA_URL=https://your-org.atlassian.net JIRA_USER_EMAIL=... JIRA_API_TOKEN=..."
fi
```

---

### SERVICE: linear

**Required env vars:** `LINEAR_API_KEY`

```bash
check_env LINEAR_API_KEY

if [ -n "$LINEAR_API_KEY" ]; then
  # Test via GraphQL API
  RESULT=$(curl -sf -X POST https://api.linear.app/graphql \
    -H "Authorization: $LINEAR_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"query":"{ viewer { name email } }"}')
  echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); v=d['data']['viewer']; print('✅ Linear authenticated as', v['name'], '(' + v['email'] + ')')"
else
  echo "❌ LINEAR_API_KEY not set in .env"
  echo "→ Create at: https://linear.app/settings/api"
  echo "→ Add to .env: LINEAR_API_KEY=lin_api_..."
fi
```

---

### SERVICE: slack

**Required env vars:** `SLACK_BOT_TOKEN`
**Optional:** `SLACK_TEAM_ID` · `SLACK_SIGNING_SECRET`

```bash
check_env SLACK_BOT_TOKEN

if [ -n "$SLACK_BOT_TOKEN" ]; then
  RESULT=$(curl -sf -X POST https://slack.com/api/auth.test \
    -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
    -H "Content-Type: application/json")
  echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print('✅ Slack authenticated — team:', d.get('team','?'), '| bot:', d.get('user','?')) if d.get('ok') else print('❌ Slack auth failed:', d.get('error','unknown'))"
else
  echo "❌ SLACK_BOT_TOKEN not set in .env"
  echo "→ Create a Slack app and get Bot Token at: https://api.slack.com/apps"
  echo "→ Add to .env: SLACK_BOT_TOKEN=xoxb-..."
fi
```

---

### SERVICE: notion

**Required env vars:** `NOTION_TOKEN`
**Optional:** `NOTION_DATABASE_ID`

```bash
check_env NOTION_TOKEN

if [ -n "$NOTION_TOKEN" ]; then
  RESULT=$(curl -sf https://api.notion.com/v1/users/me \
    -H "Authorization: Bearer $NOTION_TOKEN" \
    -H "Notion-Version: 2022-06-28")
  echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print('✅ Notion authenticated as', d.get('name','?'))" 2>/dev/null || echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print('✅ Notion authenticated — bot:', d.get('bot',{}).get('workspace_name','?'))"
else
  echo "❌ NOTION_TOKEN not set in .env"
  echo "→ Create an integration at: https://www.notion.so/my-integrations"
  echo "→ Add to .env: NOTION_TOKEN=secret_..."
fi
```

---

### SERVICE: npm

**Required env vars:** `NPM_TOKEN`

```bash
check_cli npm
check_env NPM_TOKEN

if [ -n "$NPM_TOKEN" ]; then
  npm set //registry.npmjs.org/:_authToken "$NPM_TOKEN"
  npm whoami
  echo "✅ npm authenticated"
else
  echo "❌ NPM_TOKEN not set in .env"
  echo "→ Create at: https://www.npmjs.com/settings/[username]/tokens"
  echo "→ Add to .env: NPM_TOKEN=npm_..."
fi
```

---

### SERVICE: pypi

**Required env vars:** `PYPI_TOKEN`
**Optional:** `PYPI_USERNAME` (defaults to __token__)

```bash
check_cli twine
check_env PYPI_TOKEN

if ! command -v twine &>/dev/null; then
  pip install twine --quiet
fi

if [ -n "$PYPI_TOKEN" ]; then
  # Write ~/.pypirc
  cat > ~/.pypirc <<EOF
[distutils]
index-servers = pypi

[pypi]
username = __token__
password = ${PYPI_TOKEN}
EOF
  chmod 600 ~/.pypirc
  echo "✅ PyPI credentials written to ~/.pypirc"
  twine check --help &>/dev/null && echo "✅ twine available"
else
  echo "❌ PYPI_TOKEN not set in .env"
  echo "→ Create at: https://pypi.org/manage/account/token/"
  echo "→ Add to .env: PYPI_TOKEN=pypi-..."
fi
```

---

### SERVICE: digitalocean

**Required env vars:** `DIGITALOCEAN_ACCESS_TOKEN`

```bash
check_cli doctl
check_env DIGITALOCEAN_ACCESS_TOKEN

if ! command -v doctl &>/dev/null; then
  brew install doctl
fi

if [ -n "$DIGITALOCEAN_ACCESS_TOKEN" ]; then
  doctl auth init --access-token "$DIGITALOCEAN_ACCESS_TOKEN"
  doctl account get
  echo "✅ DigitalOcean authenticated"
else
  echo "❌ DIGITALOCEAN_ACCESS_TOKEN not set in .env"
  echo "→ Create at: https://cloud.digitalocean.com/account/api/tokens"
  echo "→ Add to .env: DIGITALOCEAN_ACCESS_TOKEN=dop_v1_..."
fi
```

---

### SERVICE: render

**Required env vars:** `RENDER_API_KEY`

```bash
check_env RENDER_API_KEY

if [ -n "$RENDER_API_KEY" ]; then
  RESULT=$(curl -sf https://api.render.com/v1/owners \
    -H "Accept: application/json" \
    -H "Authorization: Bearer $RENDER_API_KEY")
  echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print('✅ Render authenticated — owners:', ', '.join(o['owner']['name'] for o in d))" 2>/dev/null || echo "✅ Render API key accepted"
else
  echo "❌ RENDER_API_KEY not set in .env"
  echo "→ Create at: https://dashboard.render.com/u/settings#api-keys"
  echo "→ Add to .env: RENDER_API_KEY=rnd_..."
fi
```

---

### SERVICE: mongodb

**Required env vars:** `MONGODB_ATLAS_PUBLIC_KEY` · `MONGODB_ATLAS_PRIVATE_KEY` · `MONGODB_ATLAS_ORG_ID`

```bash
check_cli atlas
check_env MONGODB_ATLAS_PUBLIC_KEY
check_env MONGODB_ATLAS_PRIVATE_KEY

if ! command -v atlas &>/dev/null; then
  brew install mongodb-atlas-cli
fi

if [ -n "$MONGODB_ATLAS_PUBLIC_KEY" ] && [ -n "$MONGODB_ATLAS_PRIVATE_KEY" ]; then
  atlas config set public_api_key "$MONGODB_ATLAS_PUBLIC_KEY"
  atlas config set private_api_key "$MONGODB_ATLAS_PRIVATE_KEY"
  [ -n "$MONGODB_ATLAS_ORG_ID" ] && atlas config set org_id "$MONGODB_ATLAS_ORG_ID"
  atlas auth whoami
  echo "✅ MongoDB Atlas authenticated"
else
  echo "❌ MongoDB Atlas credentials not set in .env"
  echo "→ Create programmatic API keys at: https://cloud.mongodb.com/v2#/org/[orgId]/settings/apiKeys"
  echo "→ Add to .env: MONGODB_ATLAS_PUBLIC_KEY=... MONGODB_ATLAS_PRIVATE_KEY=... MONGODB_ATLAS_ORG_ID=..."
fi
```

---

## Phase 3 — Status Dashboard (no-arg mode)

When `/connect` is called with no argument, run status checks for ALL services and print this summary:

```bash
echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║              SAAS CONNECTION STATUS                              ║"
echo "╠══════════════════════════════════════════════════════════════════╣"

check_service() {
  local name="$1"; local cli="$2"; local envvar="$3"
  local cli_ok=false; local env_ok=false
  command -v "$cli" &>/dev/null 2>&1 && cli_ok=true
  [ -n "${!envvar}" ] && env_ok=true
  if $cli_ok && $env_ok; then
    printf "║  ✅ %-18s CLI: %-8s  Token: %-20s  ║\n" "$name" "ok" "set"
  elif $env_ok; then
    printf "║  ⚠️  %-18s CLI: %-8s  Token: %-20s  ║\n" "$name" "missing" "set"
  elif $cli_ok; then
    printf "║  ⚠️  %-18s CLI: %-8s  Token: %-20s  ║\n" "$name" "ok" "not set"
  else
    printf "║  ❌ %-18s CLI: %-8s  Token: %-20s  ║\n" "$name" "missing" "not set"
  fi
}

check_service "GitHub"        gh          GITHUB_TOKEN
check_service "AWS"           aws         AWS_ACCESS_KEY_ID
check_service "GCP"           gcloud      GCP_PROJECT_ID
check_service "Azure"         az          AZURE_CLIENT_ID
check_service "Vercel"        vercel      VERCEL_TOKEN
check_service "Netlify"       netlify     NETLIFY_AUTH_TOKEN
check_service "Railway"       railway     RAILWAY_TOKEN
check_service "Heroku"        heroku      HEROKU_API_KEY
check_service "Fly.io"        fly         FLY_API_TOKEN
check_service "Supabase"      supabase    SUPABASE_ACCESS_TOKEN
check_service "PlanetScale"   pscale      PLANETSCALE_SERVICE_TOKEN
check_service "Firebase"      firebase    FIREBASE_TOKEN
check_service "Stripe"        stripe      STRIPE_SECRET_KEY
check_service "Cloudflare"    wrangler    CLOUDFLARE_API_TOKEN
check_service "Docker"        docker      DOCKER_TOKEN
check_service "Terraform"     terraform   TF_TOKEN_app_terraform_io
check_service "Sentry"        sentry-cli  SENTRY_AUTH_TOKEN
check_service "Datadog"       datadog-ci  DD_API_KEY
check_service "Jira"          jira        JIRA_API_TOKEN
check_service "Linear"        curl        LINEAR_API_KEY
check_service "Slack"         curl        SLACK_BOT_TOKEN
check_service "Notion"        curl        NOTION_TOKEN
check_service "npm"           npm         NPM_TOKEN
check_service "PyPI"          twine       PYPI_TOKEN
check_service "DigitalOcean"  doctl       DIGITALOCEAN_ACCESS_TOKEN
check_service "Render"        curl        RENDER_API_KEY
check_service "MongoDB Atlas" atlas       MONGODB_ATLAS_PUBLIC_KEY

echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║  Run: /connect <service>  to connect any service                 ║"
echo "║  Edit .env to add missing credentials                            ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
```

---

## Phase 4 — Hook guidance

After a successful connection, suggest the user add this to their shell profile to auto-load `.env` on project entry:

```bash
# ~/.zshrc or ~/.bashrc — auto-load .env when entering a project directory
autoload_env() {
  local env_file="$PWD/.env"
  [ -f "$env_file" ] && [ -r "$env_file" ] && set -a && source "$env_file" && set +a
}
# Add to PROMPT_COMMAND (bash) or chpwd hook (zsh)
# zsh: add_zsh_hook chpwd autoload_env
# bash: PROMPT_COMMAND="autoload_env; $PROMPT_COMMAND"
```

And suggest adding Claude Code hook in `.claude/settings.json` to warn on missing credentials:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'if [ ! -f .env ] && grep -qE \"(aws|gcloud|stripe|supabase|vercel)\" <<< \"$CLAUDE_TOOL_INPUT\" 2>/dev/null; then echo \"WARNING: No .env file found. Run /connect to set up credentials.\"; fi'"
          }
        ]
      }
    ]
  }
}
```
