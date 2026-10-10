---
name: db
description: Connect to any database — Cloud SQL, PostgreSQL, Snowflake, Databricks, Athena, Presto, or Oracle.
category: data
tier: on-demand
slash_command: /db
---

# DB — Universal Database Access

Reads connection config from `.claude/100xprism.yml` (preferred — kept out of the always-on context), falling back to the project instruction file (CLAUDE.md, AGENTS.md, .cursorrules, or equivalent), then ~/.claude/db-connections.json (global registry).

> **Scope:** `/db` executes specific SQL or migrations against named connections. For analytics in plain English, use `/query`.

## Supported engines
cloud-sql | postgres | snowflake | databricks | athena | presto | oracle

## Usage
- `/db` — default audit query for current project DB
- `/db "SELECT count(*) FROM users"` — arbitrary SQL on current project DB
- `/db migrate` — run pending migrations
- `/db prod-snowflake` — named connection from global registry
- `/db prod-snowflake "SELECT ..."` — named connection + custom SQL

---

## Step 0 — Parse arguments

```bash
# If first arg looks like a connection name (no spaces, no SQL keywords), treat as named connection
ARGS="${1:-}"
if echo "$ARGS" | grep -qE '^[a-zA-Z0-9_-]+$' && ! echo "$ARGS" | grep -qiE '^(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|SHOW|DESCRIBE|migrate)'; then
  NAMED_CONNECTION=$(echo "$ARGS" | awk '{print $1}')
  SQL=$(echo "$ARGS" | cut -s -d' ' -f2-)
else
  NAMED_CONNECTION=""
  SQL="$ARGS"
fi
```

---

## Step 1 — Load connection config

```bash
# Detect project instruction file
INSTRUCTION_FILE=$(ROOT=$(git rev-parse --show-toplevel 2>/dev/null); for f in .claude/100xprism.yml CLAUDE.md AGENTS.md .cursorrules; do [ -f "$ROOT/$f" ] && echo "$ROOT/$f" && break; done)
DB_CONNECTIONS="$HOME/.claude/db-connections.json"

if [ -n "$NAMED_CONNECTION" ]; then
  ENGINE=$(python3 -c "import json; d=json.load(open('$DB_CONNECTIONS')); c=d.get('$NAMED_CONNECTION',{}); print(c.get('engine',''))" 2>/dev/null)
  CONFIG_SOURCE="registry:$NAMED_CONNECTION"

elif [ -n "$INSTRUCTION_FILE" ] && grep -q "^engine:" "$INSTRUCTION_FILE" 2>/dev/null; then
  ENGINE=$(grep "^engine:" "$INSTRUCTION_FILE" | head -1 | cut -d: -f2 | tr -d ' ')
  CONNECTION_NAME=$(grep "^connection:" "$INSTRUCTION_FILE" | head -1 | cut -d: -f2 | tr -d ' ')
  CONFIG_SOURCE="instruction-file"

elif [ -f "$DB_CONNECTIONS" ]; then
  echo "No DB config found in project instruction file. Available connections:"
  python3 -c "
import json
d = json.load(open('$DB_CONNECTIONS'))
for i, (name, cfg) in enumerate(d.items(), 1):
    print(f'  {i}) {name} ({cfg.get(\"engine\",\"unknown\")})')
"
  read -rp "Select connection (number or name): " SELECTION
  NAMED_CONNECTION=$(python3 -c "
import json, sys
d = json.load(open('$DB_CONNECTIONS'))
keys = list(d.keys())
sel = '$SELECTION'
if sel.isdigit() and 1 <= int(sel) <= len(keys):
    print(keys[int(sel)-1])
elif sel in d:
    print(sel)
else:
    print('', end='')
" 2>/dev/null)
  ENGINE=$(python3 -c "import json; d=json.load(open('$DB_CONNECTIONS')); print(d.get('$NAMED_CONNECTION',{}).get('engine',''))" 2>/dev/null)
  CONFIG_SOURCE="registry:$NAMED_CONNECTION"

else
  echo "ERROR: No database config found."
  echo "  Option 1: Add a '## Database' section to your project instruction file"
  echo "  Option 2: Create ~/.claude/db-connections.json with named connections"
  exit 1
fi

if [ -z "$ENGINE" ]; then
  echo "ERROR: Could not determine database engine from config."
  exit 1
fi

echo "Engine: $ENGINE | Config: $CONFIG_SOURCE"
```

---

## Step 2 — Load connection details from registry (if using named connection)

```bash
if [ -n "$NAMED_CONNECTION" ]; then
  # Extract all fields from the named connection into shell variables
  eval "$(python3 -c "
import json, shlex
d = json.load(open('$HOME/.claude/db-connections.json'))
cfg = d.get('$NAMED_CONNECTION', {})
for k, v in cfg.items():
    if k != 'engine':
        print(f'CONN_{k.upper()}={shlex.quote(str(v))}')
" 2>/dev/null)"
fi
```

---

## Step 3 — Resolve credentials

```bash
AUTH="${CONN_AUTH:-$(grep '^auth:' "$INSTRUCTION_FILE" 2>/dev/null | head -1 | cut -d: -f2- | tr -d ' ')}"

case "$AUTH" in
  gcp-secret:*)
    SECRET_NAME="${AUTH#gcp-secret:}"
    DB_CRED=$(gcloud secrets versions access latest --secret="$SECRET_NAME" --project="${CONN_GCP_PROJECT:-$(grep '^gcp_project:' "$INSTRUCTION_FILE" | cut -d: -f2 | tr -d ' ')}")
    echo "Credential resolved from GCP Secret Manager ✓"
    ;;
  env:*)
    VAR_NAME="${AUTH#env:}"
    DB_CRED="${!VAR_NAME}"
    # Fall back to .env file
    if [ -z "$DB_CRED" ] && [ -f "$(git rev-parse --show-toplevel 2>/dev/null)/.env" ]; then
      DB_CRED=$(grep "^${VAR_NAME}=" "$(git rev-parse --show-toplevel)/.env" | cut -d= -f2-)
    fi
    [ -z "$DB_CRED" ] && { echo "ERROR: Env var $VAR_NAME not set and not in .env"; exit 1; }
    echo "Credential resolved from env var $VAR_NAME ✓"
    ;;
  sso)
    DB_CRED=""
    echo "Using SSO auth — browser window will open ✓"
    ;;
  keychain:*)
    KEY_NAME="${AUTH#keychain:}"
    DB_CRED=$(security find-generic-password -a "$KEY_NAME" -w 2>/dev/null)
    [ -z "$DB_CRED" ] && { echo "ERROR: Keychain key '$KEY_NAME' not found"; exit 1; }
    echo "Credential resolved from macOS keychain ✓"
    ;;
  databricks-token)
    DB_CRED="${DATABRICKS_TOKEN:-$(grep -A5 '\[DEFAULT\]' "$HOME/.databrickscfg" 2>/dev/null | grep '^token' | cut -d= -f2 | tr -d ' ')}"
    [ -z "$DB_CRED" ] && { echo "ERROR: No Databricks token found"; exit 1; }
    echo "Credential resolved from Databricks config ✓"
    ;;
  *)
    DB_CRED=""
    echo "WARNING: No auth method specified — proceeding without credentials"
    ;;
esac
```

---

## Step 4 — Set default SQL if not provided

```bash
if [ -z "$SQL" ]; then
  SQL="SELECT 'connected' AS status, current_timestamp AS at"
  echo "No SQL provided — running default connectivity check"
fi
```

---

## Step 5 — Delegate to engine file

```bash
# Engine files ship in db-engines/ next to this SKILL.md. Use the directory you
# loaded this skill from; the list below covers every adapter's install location.
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
ENGINE_FILE=""
for d in "${DB_SKILL_DIR:-}" \
  "$HOME/.claude/skills/db" "$HOME/.100xprism/100xprism-catalog/db" \
  "$ROOT/.agents/skills/db" "$ROOT/.agents/100xprism-catalog/db" \
  "$ROOT/.cursor/100xprism-catalog/db" \
  "$ROOT/.pi/skills/db" "$ROOT/.pi/100xprism-catalog/db" \
  "$HOME/.hermes/skills/100xprism/db" "$HOME/.claude/commands"; do
  [ -n "$d" ] && [ -f "$d/db-engines/${ENGINE}.md" ] && ENGINE_FILE="$d/db-engines/${ENGINE}.md" && break
done

if [ -z "$ENGINE_FILE" ]; then
  echo "ERROR: No engine file found for ${ENGINE} (looked for db-engines/${ENGINE}.md beside this skill)"
  echo "Supported engines: cloud-sql, postgres, snowflake, databricks, athena, presto, oracle"
  exit 1
fi

echo "Delegating to db-engines/${ENGINE}.md..."
echo "---"

# Pass all resolved variables to the engine skill
# The engine file will use these pre-resolved values:
# - For cloud-sql:   INSTANCE=$CONN_INSTANCE, GCP_PROJECT=$CONN_GCP_PROJECT, DB_NAME=$CONN_DB_NAME, DB_USER=$CONN_DB_USER, DB_PASS=$DB_CRED
# - For postgres:    DB_HOST=$CONN_HOST, DB_PORT=$CONN_PORT, DB_NAME=$CONN_DATABASE, DB_USER=$CONN_USER, DB_PASS=$DB_CRED
# - For snowflake:   SF_ACCOUNT=$CONN_ACCOUNT, SF_WAREHOUSE=$CONN_WAREHOUSE, SF_DATABASE=$CONN_DATABASE, SF_ROLE=$CONN_ROLE, SF_USER=$CONN_USER, SF_TOKEN=$DB_CRED, SF_AUTH=$AUTH
# - For databricks:  DBX_HOST=$CONN_HOST, DBX_HTTP_PATH=$CONN_HTTP_PATH, DBX_TOKEN=$DB_CRED, DBX_CATALOG=$CONN_CATALOG, DBX_SCHEMA=$CONN_SCHEMA
# - For athena:      ATHENA_REGION=$CONN_REGION, ATHENA_DATABASE=$CONN_DATABASE, ATHENA_WORKGROUP=$CONN_WORKGROUP, ATHENA_S3_OUTPUT=$CONN_S3_OUTPUT
# - For presto:      PRESTO_HOST=$CONN_HOST, PRESTO_PORT=$CONN_PORT, PRESTO_USER=$CONN_USER, PRESTO_CATALOG=$CONN_CATALOG, PRESTO_SCHEMA=$CONN_SCHEMA, PRESTO_TOKEN=$DB_CRED, PRESTO_ENGINE=$CONN_ENGINE
# - For oracle:      ORA_HOST=$CONN_HOST, ORA_PORT=$CONN_PORT, ORA_SERVICE=$CONN_SERVICE, ORA_USER=$CONN_USER, ORA_PASS=$DB_CRED
# SQL=$SQL is passed to all engines

# Follow the instructions in db-engines/${ENGINE}.md using the variable mappings above
```
