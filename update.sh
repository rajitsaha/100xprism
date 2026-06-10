#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
COMMANDS_DIR="$CLAUDE_DIR/commands"
SKILLS_DIR="$CLAUDE_DIR/skills"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"
CHECK_ONLY=false
PLUGINS_ONLY=false
# Overridable so tests can stub the CLI (see test/update-plugins.test.js).
CLAUDE_BIN="${CLAUDE_BIN:-claude}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

for _arg in "$@"; do
  [ "$_arg" = "--check-only"   ] && CHECK_ONLY=true
  [ "$_arg" = "--plugins-only" ] && PLUGINS_ONLY=true
done

# ── Plugin update function (always available) ─────────────────────────────────
# Reads the plugin list from plugins/plugins.json (single source of truth). Each
# entry is the fully-qualified `name@marketplace` id — the only form
# `claude plugin update` accepts (bare names fail with "not found").
run_plugin_updates() {
  local _plugins_file="$REPO_DIR/plugins/plugins.json"
  local _plugins=()
  while IFS= read -r _p; do
    [ -n "$_p" ] && _plugins+=("$_p")
  done < <(python3 -c "import json,sys; print('\n'.join(json.load(open(sys.argv[1])).get('plugins',[])))" "$_plugins_file" 2>/dev/null)

  if [ "${#_plugins[@]}" -eq 0 ]; then
    echo -e "  ${YELLOW}→ Plugins: no entries found in plugins.json — skipping${NC}"
    return 0
  fi

  local _ok=0 _failed=()
  for _p in "${_plugins[@]}"; do
    # A plugin can be installed at user, project, or local scope. `claude plugin
    # update` defaults to user scope and exits non-zero with "not installed at
    # scope <s>" when the plugin lives at a different scope — which previously
    # surfaced as a spurious failure for project/local-scoped plugins. Try each
    # scope until one updates; only mark failed if it's absent from all of them.
    local _updated=false _scope
    for _scope in user project local; do
      if "$CLAUDE_BIN" plugin update "$_p" --scope "$_scope" >/dev/null 2>&1; then
        _updated=true
        break
      fi
    done
    if $_updated; then
      (( _ok++ )) || true
    else
      _failed+=("$_p")
    fi
  done

  if [ "${#_failed[@]}" -eq 0 ]; then
    echo -e "  ${GREEN}→ Plugins: $_ok updated ✓${NC}"
  else
    echo -e "  ${GREEN}→ Plugins: $_ok updated${NC}, ${YELLOW}${#_failed[@]} failed:${NC}"
    for _p in "${_failed[@]}"; do
      echo -e "      ${YELLOW}• $_p${NC}"
    done
  fi
}

# ── Hook sync (only refreshes hooks already installed; never enables new ones) ─
# Keeps the wired-in hook commands current after a pull without re-prompting or
# silently opting users into hooks they declined at install time.
sync_hooks() {
  [ -f "$SETTINGS_FILE" ] || return 0
  python3 "$REPO_DIR/adapters/lib/modules.py" emit-hooks --sync 2>/dev/null || true
}

# Test seam: when sourced with UPDATE_SH_SOURCE_ONLY=1, load the functions above
# without running the update flow, so tests can exercise them against a stubbed
# CLAUDE_BIN. No effect when the script is executed normally.
if [ -n "${UPDATE_SH_SOURCE_ONLY:-}" ]; then
  return 0 2>/dev/null || exit 0
fi

if [ "$PLUGINS_ONLY" = true ]; then
  echo ""
  echo "Updating Claude plugins..."
  run_plugin_updates
  echo ""
  echo -e "${GREEN}✓ Done. Restart Claude Code to activate changes.${NC}"
  echo ""
  exit 0
fi

echo ""
echo "Checking for updates..."

cd "$REPO_DIR"

git fetch origin main --quiet 2>/dev/null

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
  echo -e "${GREEN}✓ Repo already up to date.${NC}"
  echo ""
  echo "Syncing plugins and settings..."
  REPO_DIR="$REPO_DIR" SETTINGS_FILE="$SETTINGS_FILE" python3 - <<'PYEOF'
import json, os

plugins_file = os.path.join(os.environ['REPO_DIR'], 'plugins', 'plugins.json')
settings_file = os.environ['SETTINGS_FILE']

with open(plugins_file) as f:
    repo_data = json.load(f)

with open(settings_file) as f:
    settings = json.load(f)

new_plugins = repo_data.get('plugins', [])
enabled = settings.get('enabledPlugins', {})
added = 0
for p in new_plugins:
    if p not in enabled:
        enabled[p] = True
        added += 1

settings['enabledPlugins'] = enabled

extra = repo_data.get('extraKnownMarketplaces', {})
settings.setdefault('extraKnownMarketplaces', {}).update(extra)

with open(settings_file, 'w') as f:
    json.dump(settings, f, indent=2)

if added > 0:
    print(f'  Added {added} new plugin(s) to settings.json ✓')
else:
    print('  Plugins: settings already up to date ✓')
PYEOF
  sync_hooks
  run_plugin_updates
  echo ""
  echo -e "${CYAN}Tip: Restart Claude Code to activate any plugin changes.${NC}"
  echo ""
  exit 0
fi

CHANGES=$(git log --oneline "$LOCAL..$REMOTE" 2>/dev/null)
LOCAL_SHORT=$(git rev-parse --short HEAD)
REMOTE_SHORT=$(git rev-parse --short origin/main)

echo ""
echo "  Current version:  $LOCAL_SHORT ($(git log -1 --format='%ar' HEAD))"
echo "  Latest version:   $REMOTE_SHORT ($(git log -1 --format='%ar' origin/main))"
echo ""
echo "Changes:"
while IFS= read -r line; do echo "  • $line"; done <<< "$CHANGES"
echo ""

if [ "$CHECK_ONLY" = true ]; then
  echo -e "${CYAN}Run '~/100x-dev/update.sh' to apply updates.${NC}"
  echo ""
  exit 0
fi

read -rp "Apply updates? (Y/n): " confirm
confirm=${confirm:-Y}

if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  echo "Skipped."
  exit 0
fi

echo ""

git pull origin main --quiet
echo -e "  ${GREEN}→ Pulled latest ✓${NC}"

# Optional: verify GPG signature on HEAD when HUNDRED_X_VERIFY_SIGNATURES=1.
# Requires the maintainer's public key to be imported in the user's GPG keyring.
if [ "${HUNDRED_X_VERIFY_SIGNATURES:-0}" = "1" ]; then
  if git verify-commit HEAD >/dev/null 2>&1; then
    echo -e "  ${GREEN}→ HEAD signature verified ✓${NC}"
  else
    echo -e "  ${YELLOW}⚠ HEAD commit is not signed or signature is invalid.${NC}"
    echo -e "  ${YELLOW}  Set HUNDRED_X_VERIFY_SIGNATURES=0 to skip this check.${NC}"
  fi
fi

if [ -d "$COMMANDS_DIR" ] && [ "$(ls -A "$COMMANDS_DIR" 2>/dev/null)" ]; then
  BACKUP="$CLAUDE_DIR/commands.bak.$(date +%Y%m%d_%H%M%S)"
  cp -r "$COMMANDS_DIR" "$BACKUP"
  echo -e "  ${YELLOW}→ Backed up commands to $(basename "$BACKUP")${NC}"
fi

# Sync modules (skills + slash command aliases) via the claude-code adapter
python3 "$REPO_DIR/adapters/lib/modules.py" emit-claude-code
echo -e "  ${GREEN}→ Updated modules ✓${NC}"

REPO_DIR="$REPO_DIR" SETTINGS_FILE="$SETTINGS_FILE" python3 - <<'PYEOF'
import json, os

plugins_file = os.path.join(os.environ['REPO_DIR'], 'plugins', 'plugins.json')
settings_file = os.environ['SETTINGS_FILE']

with open(plugins_file) as f:
    repo_data = json.load(f)

with open(settings_file) as f:
    settings = json.load(f)

new_plugins = repo_data.get('plugins', [])
enabled = settings.get('enabledPlugins', {})
added = 0
for p in new_plugins:
    if p not in enabled:
        enabled[p] = True
        added += 1

settings['enabledPlugins'] = enabled

extra = repo_data.get('extraKnownMarketplaces', {})
settings.setdefault('extraKnownMarketplaces', {}).update(extra)

# Merge SessionStart hook for version check
hook_cmd = os.path.expanduser('~/100x-dev/shell/check-update.sh') + ' --claude-hook'
hooks = settings.setdefault('hooks', {})
session_start = hooks.setdefault('SessionStart', [])

already_exists = any(
    h.get('command') == hook_cmd
    for entry in session_start
    for h in entry.get('hooks', [])
)

if not already_exists:
    session_start.append({
        'matcher': '',
        'hooks': [{'type': 'command', 'command': hook_cmd}]
    })
    print('  Added SessionStart update-check hook ✓')
else:
    print('  SessionStart hook: already configured ✓')

with open(settings_file, 'w') as f:
    json.dump(settings, f, indent=2)

if added > 0:
    print(f'  Added {added} new plugin(s) to settings.json ✓')
else:
    print('  Plugins: already up to date ✓')
PYEOF

echo -e "  ${CYAN}→ Shell aliases auto-updated (sourced file)${NC}"

# Refresh any installed hooks so their wired commands stay current.
sync_hooks

# ── Update Claude plugins ─────────────────────────────────────────────────────
run_plugin_updates

# ── Regenerate tracked project instruction files ────────────────────────────

regenerate_tracked_projects() {
  local tracked="$HOME/.100x-dev/tracked-projects"
  [[ -f "$tracked" ]] || return 0

  local count=0
  while IFS= read -r project_path; do
    [[ -z "$project_path" ]] && continue
    [[ -d "$project_path" ]] || continue  # skip deleted projects

    local regenerated=false

    [[ -f "$project_path/.cursorrules" ]]                    && bash "$REPO_DIR/adapters/cursor.sh"      "$project_path" && regenerated=true
    [[ -f "$project_path/AGENTS.md" ]]                       && bash "$REPO_DIR/adapters/codex.sh"       "$project_path" && regenerated=true
    [[ -f "$project_path/.windsurfrules" ]]                  && bash "$REPO_DIR/adapters/windsurf.sh"    "$project_path" && regenerated=true
    [[ -f "$project_path/.github/copilot-instructions.md" ]] && bash "$REPO_DIR/adapters/copilot.sh"    "$project_path" && regenerated=true
    [[ -f "$project_path/GEMINI.md" ]]                       && bash "$REPO_DIR/adapters/gemini.sh"      "$project_path" && regenerated=true
    [[ -f "$project_path/ANTIGRAVITY.md" ]]                  && bash "$REPO_DIR/adapters/antigravity.sh" "$project_path" && regenerated=true

    "$regenerated" && (( count++ )) || true
  done < "$tracked"

  if (( count > 0 )); then
    echo -e "  ${GREEN}→ Regenerated instruction files in $count tracked project(s) ✓${NC}"
  fi
}

# Clear update-available flag from cache so banner stops showing
if [[ -f "$HOME/.100x-dev/update-cache" ]]; then
  _tmp="$(mktemp)"
  grep -v '^has_update=' "$HOME/.100x-dev/update-cache" > "$_tmp" 2>/dev/null || true
  grep -v '^snoozed_until=' "$_tmp" >> /dev/null || true
  mv "$_tmp" "$HOME/.100x-dev/update-cache"
  echo "has_update=false"  >> "$HOME/.100x-dev/update-cache"
  echo "snoozed_until=0"   >> "$HOME/.100x-dev/update-cache"
fi

regenerate_tracked_projects

echo ""
NEW_VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null | tr -d '[:space:]')"
echo -e "${GREEN}✓ 100x Dev updated to v${NEW_VERSION}! Restart Claude Code to activate new modules and plugins.${NC}"
echo ""
echo -e "${CYAN}Tip: Add this to your crontab to get notified weekly:${NC}"
echo "  0 9 * * 1 $REPO_DIR/update.sh --check-only"
echo ""
