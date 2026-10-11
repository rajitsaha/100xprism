#!/usr/bin/env bash
set -euo pipefail

# Everything below installs into and reads from $HOME. Under `set -u` an unset
# HOME aborts with an opaque "unbound variable" at the first expansion, so fail
# with an explanation instead.
if [ -z "${HOME:-}" ]; then
  echo "100xprism update: HOME is not set; cannot locate ~/.claude or ~/.100xprism." >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
COMMANDS_DIR="$CLAUDE_DIR/commands"
SKILLS_DIR="$CLAUDE_DIR/skills"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"
CHECK_ONLY=false
PLUGINS_ONLY=false
ASSUME_YES=false
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
  { [ "$_arg" = "--yes" ] || [ "$_arg" = "-y" ]; } && ASSUME_YES=true
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

# Reconciles Hermes/OpenClaw skills (~/.hermes/skills/100xprism/) the same way
# emit-claude-code reconciles ~/.claude/skills — adds new modules, updates
# changed ones, prunes removed ones. Auto-detected and silent: never creates
# ~/.hermes for a user who does not have Hermes installed.
sync_hermes() {
  # shellcheck disable=SC1091
  source "$REPO_DIR/adapters/lib/shared.sh"
  _hermes_installed || return 0
  _run_hermes
}

remove_session_update_hook() {
  [ -f "$SETTINGS_FILE" ] || return 0
  if command -v node >/dev/null 2>&1; then
    node "$REPO_DIR/lib/uninstall.js" --hooks-only
  else
    echo -e "  ${YELLOW}→ Node.js not found; skipping legacy SessionStart hook cleanup${NC}" >&2
  fi
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

if [ "$CHECK_ONLY" = false ] && [ -n "$(git status --porcelain --untracked-files=all)" ]; then
  echo "Refusing update: stable clone has tracked or untracked changes. Commit or move them, then retry." >&2
  exit 1
fi

git fetch origin main --quiet 2>/dev/null

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

# ── Regenerate tracked project instruction files ────────────────────────────
# Defined above the "already up to date" early exit so both paths run it: a v3
# migration that was interrupted after the pull must still get another chance to
# prune, rather than being skipped forever once HEAD matches origin.

regenerate_tracked_projects() {
  local tracked="$HOME/.100xprism/tracked-projects"
  [[ -f "$tracked" ]] || return 0

  # shellcheck disable=SC1091
  source "$REPO_DIR/adapters/lib/deprecated.sh"

  local count=0
  local pruned_total=0
  local pruned_projects=0
  local prune_failures=0
  local adapter_failures=0
  # One backup root for the whole run, so every removal is reported as a single
  # recoverable location even if the loop crosses a one-second boundary.
  local backup_dir
  backup_dir="$(prune_backup_root)"

  while IFS= read -r project_path; do
    [[ -z "$project_path" ]] && continue
    [[ -d "$project_path" ]] || continue  # skip deleted projects

    local regenerated=false

    # Explicit if-blocks, not `&&` chains: inside an AND-list a failing adapter is
    # exempt from `errexit`, so a broken regeneration used to be silently reported
    # as "not regenerated" while the update still claimed success.
    if [[ -d "$project_path/.cursor/rules" ]] || [[ -f "$project_path/.cursorrules" ]]; then
      if bash "$REPO_DIR/adapters/cursor.sh" "$project_path"; then
        regenerated=true
      else
        echo -e "  ${YELLOW}→ $project_path: Cursor adapter failed${NC}"
        adapter_failures=$(( adapter_failures + 1 ))
      fi
    fi
    if [[ -f "$project_path/AGENTS.md" ]]; then
      if bash "$REPO_DIR/adapters/codex.sh" "$project_path"; then
        regenerated=true
      else
        echo -e "  ${YELLOW}→ $project_path: Codex adapter failed${NC}"
        adapter_failures=$(( adapter_failures + 1 ))
      fi
    fi

    # Prune artifacts from tools dropped in v3.0.0 (Windsurf/Copilot/Gemini/Antigravity).
    prune_deprecated_artifacts "$project_path" "$backup_dir"
    if (( PRUNED_COUNT > 0 )); then
      echo -e "  ${YELLOW}→ $project_path: removed $PRUNED_COUNT file(s) from tools dropped in v3.0.0${NC}"
      pruned_total=$(( pruned_total + PRUNED_COUNT ))
      pruned_projects=$(( pruned_projects + 1 ))
    fi
    (( PRUNE_FAILED > 0 )) && prune_failures=$(( prune_failures + PRUNE_FAILED ))

    "$regenerated" && (( count++ )) || true
  done < "$tracked" || {
    # Calling this function in an OR-list suspends errexit inside it, so an
    # unreadable tracked file (deleted or chmod'd between the -f test and this
    # redirection) would otherwise fall through to `return 0` and report success.
    echo -e "  ${YELLOW}→ Could not read $tracked; no projects were reconciled${NC}"
    return 1
  }

  if (( count > 0 )); then
    echo -e "  ${GREEN}→ Regenerated instruction files in $count tracked project(s) ✓${NC}"
  fi
  if (( pruned_total > 0 )); then
    echo -e "  ${GREEN}→ Pruned $pruned_total deprecated file(s) across $pruned_projects project(s) ✓${NC}"
    echo -e "  ${CYAN}   Backups: $backup_dir${NC}"
    echo -e "  ${CYAN}   These are usually committed — review with 'git status' and commit the deletions.${NC}"
  fi
  if (( prune_failures > 0 )); then
    echo -e "  ${YELLOW}→ $prune_failures deprecated file(s) could not be removed and were left in place${NC}"
  fi
  if (( adapter_failures > 0 )); then
    echo -e "  ${YELLOW}→ $adapter_failures adapter run(s) failed; those projects were not regenerated${NC}"
  fi
  # Non-zero so automation sees an incomplete regeneration instead of a silent
  # partial success. Callers decide whether that is fatal.
  (( adapter_failures > 0 || prune_failures > 0 )) && return 1
  return 0
}

# Read-only: reports packs relevant to this project. Never installs. Defined as a
# function because update.sh has two exit paths — the already-up-to-date branch below
# returns before the tail of the script, so an inline block there would only ever run
# on the far rarer "there was an update" path.
suggest_packs() {
  local suggestions
  suggestions=$(python3 "$REPO_DIR/adapters/lib/packs.py" detect \
    --settings "$SETTINGS_FILE" 2>/dev/null || true)
  if [ -n "$suggestions" ]; then
    echo ""
    echo -e "${CYAN}Optional skill packs for this project:${NC}"
    echo "$suggestions"
    echo -e "${CYAN}Install with: /pack add <slug>${NC}"
  fi
}

sync_token_economics() {
  if command -v node >/dev/null 2>&1; then
    node -e 'const root=process.argv[1]; require(root+"/lib/token-economics").createLifecycle({stopDashboard:require(root+"/lib/dashboard").stopDashboard}).update(root,process.argv[2]||undefined)' "$REPO_DIR" "${1:-}"
  else
    echo "Background token collector update needs Node 18+. Existing collector retained; install Node and rerun update.sh."
  fi
}

if [ "$LOCAL" = "$REMOTE" ]; then
  echo -e "${GREEN}✓ Repo already up to date.${NC}"
  if [ "$CHECK_ONLY" = false ] && [ "$(uname -s)" = Darwin ]; then
    sync_token_economics
  fi
  echo ""
  echo "Syncing plugins and settings..."
  python3 "$REPO_DIR/adapters/lib/sync_plugins.py" \
    --settings "$SETTINGS_FILE" --plugins "$REPO_DIR/plugins/plugins.json"
  python3 "$REPO_DIR/adapters/lib/packs.py" sync --settings "$SETTINGS_FILE" || \
    echo -e "  ${YELLOW}→ Pack sync failed — run /pack to check pack state${NC}"
  sync_hooks
  sync_hermes
  run_plugin_updates
  # Still reconcile projects: an earlier run may have pulled v3 and then failed
  # before pruning, and without this the migration would never be retried.
  # NEVER under --check-only: that contract is notify-only, and reconciliation
  # DELETES deprecated artifacts from the user's repositories.
  RECONCILE_STATUS=0
  if [ "$CHECK_ONLY" = true ]; then
    echo -e "  ${CYAN}→ --check-only: skipping project reconciliation (it would delete deprecated files)${NC}"
  else
    regenerate_tracked_projects || RECONCILE_STATUS=$?
  fi
  suggest_packs
  echo ""
  echo -e "${CYAN}Tip: Restart Claude Code to activate any plugin changes.${NC}"
  echo ""
  exit "$RECONCILE_STATUS"
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
  echo -e "${CYAN}Run '~/100xprism/update.sh' to apply updates.${NC}"
  echo ""
  exit 0
fi

if [ "$ASSUME_YES" = true ]; then
  confirm=Y
else
  # `|| confirm=n` keeps `set -e` from aborting on EOF (non-interactive without
  # --yes): no TTY to answer the prompt → treat as "skip" rather than crash.
  read -rp "Apply updates? (Y/n): " confirm || confirm=n
  confirm=${confirm:-Y}
fi

if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  echo "Skipped."
  exit 0
fi

echo ""

# Stage and validate the next macOS runtime before advancing the stable checkout.
# The job continues to resolve its owned current generation throughout the update.
if [ "$(uname -s)" = Darwin ]; then
  sync_token_economics origin/main
fi
if ! git merge --ff-only origin/main --quiet; then
  echo "Stable clone could not fast-forward. Its files and the active token runtime were retained." >&2
  exit 1
fi
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
  # Keep the 3 newest backups; every run makes one, so they pile up otherwise.
  ls -1dt "$CLAUDE_DIR"/commands.bak.* 2>/dev/null | tail -n +4 | while IFS= read -r old; do rm -rf "$old"; done
fi

# Sync modules (skills + slash command aliases) via the claude-code adapter
python3 "$REPO_DIR/adapters/lib/modules.py" emit-claude-code
echo -e "  ${GREEN}→ Updated modules ✓${NC}"

python3 "$REPO_DIR/adapters/lib/sync_plugins.py" \
  --settings "$SETTINGS_FILE" --plugins "$REPO_DIR/plugins/plugins.json"
python3 "$REPO_DIR/adapters/lib/packs.py" sync --settings "$SETTINGS_FILE" || \
  echo -e "  ${YELLOW}→ Pack sync failed — run /pack to check pack state${NC}"
remove_session_update_hook

echo -e "  ${CYAN}→ Shell startup files are not modified. Run 100xprism uninstall to remove legacy source lines.${NC}"

# Refresh any installed hooks so their wired commands stay current.
sync_hooks

# Reconcile Hermes/OpenClaw skills, if Hermes is present on this machine.
sync_hermes

# ── Update Claude plugins ─────────────────────────────────────────────────────
run_plugin_updates


# Clear update-available flag from cache so banner stops showing
if [[ -f "$HOME/.100xprism/update-cache" ]]; then
  _tmp="$(mktemp)"
  grep -v '^has_update=' "$HOME/.100xprism/update-cache" > "$_tmp" 2>/dev/null || true
  grep -v '^snoozed_until=' "$_tmp" >> /dev/null || true
  mv "$_tmp" "$HOME/.100xprism/update-cache"
  echo "has_update=false"  >> "$HOME/.100xprism/update-cache"
  echo "snoozed_until=0"   >> "$HOME/.100xprism/update-cache"
fi

# `|| RECONCILE_STATUS=$?` keeps `set -e` from aborting: a project-level adapter
# or prune failure must not discard the successful global update, but it must
# still surface in this script's exit status.
RECONCILE_STATUS=0
regenerate_tracked_projects || RECONCILE_STATUS=$?

suggest_packs

echo ""
NEW_VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null | tr -d '[:space:]')"
echo -e "${GREEN}✓ 100x Dev updated to v${NEW_VERSION}! Restart Claude Code to activate new modules and plugins.${NC}"
echo ""
echo -e "${CYAN}Tip: Add this to your crontab — notify weekly, or auto-apply with --yes:${NC}"
echo "  0 9 * * 1 $REPO_DIR/update.sh --check-only   # notify only"
echo "  0 9 * * 1 $REPO_DIR/update.sh --yes          # auto-apply (no prompt)"
echo ""

exit "$RECONCILE_STATUS"
