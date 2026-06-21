#!/usr/bin/env bash
# check-update.sh — daily version check + cache for 100xprism
#
# Usage:
#   check-update.sh --silent       Refresh cache only. No output.
#   check-update.sh --notify       Show banner + prompt if update available.
#   check-update.sh --claude-hook  Inject session notice if update available.

set -euo pipefail

REPO_DIR="${HUNDRED_X_REPO_OVERRIDE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STATE_DIR="$HOME/.100xprism"
CACHE_FILE="$STATE_DIR/update-cache"
FLAG="${1:-}"

YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

mkdir -p "$STATE_DIR"

# ── Cache helpers ─────────────────────────────────────────────────────────────

_cache_get() {
  local key="$1"
  if [[ ! -f "$CACHE_FILE" ]]; then
    echo ""
    return
  fi
  grep "^${key}=" "$CACHE_FILE" 2>/dev/null | cut -d= -f2- || true
}

_cache_set() {
  local key="$1" value="$2"
  touch "$CACHE_FILE"
  local tmp
  tmp="$(mktemp)"
  grep -v "^${key}=" "$CACHE_FILE" > "$tmp" 2>/dev/null || true
  echo "${key}=${value}" >> "$tmp"
  mv "$tmp" "$CACHE_FILE"
}

# ── Version check ─────────────────────────────────────────────────────────────

_refresh_cache() {
  local now
  now="$(date +%s)"
  local last_check
  last_check="$(_cache_get last_check)"
  last_check="${last_check:-0}"

  local age=$(( now - last_check ))
  if (( age < 86400 )); then
    return  # Cache is fresh — skip network call
  fi

  cd "$REPO_DIR"

  # Fetch with timeout; on failure, update timestamp only to avoid retry storm
  if ! git fetch origin main --quiet 2>/dev/null; then
    _cache_set last_check "$now"
    return
  fi

  local local_sha remote_sha
  local_sha="$(git rev-parse HEAD 2>/dev/null || echo "unknown")"
  remote_sha="$(git rev-parse origin/main 2>/dev/null || echo "unknown")"

  local has_update="false"
  local changelog=""

  if [[ "$local_sha" != "$remote_sha" && "$local_sha" != "unknown" ]]; then
    has_update="true"
    changelog="$(git log --oneline "${local_sha}..origin/main" 2>/dev/null | head -5 | tr '\n' '|' | sed 's/|$//')"
  fi

  _cache_set last_check  "$now"
  _cache_set has_update  "$has_update"
  _cache_set local_sha   "$local_sha"
  _cache_set remote_sha  "$remote_sha"
  _cache_set changelog   "$changelog"

  # Clear snooze when no update
  if [[ "$has_update" == "false" ]]; then
    _cache_set snoozed_until "0"
  fi
}

_fetch_release_notes() {
  # Try GitHub Releases API first (requires gh CLI)
  if command -v gh >/dev/null 2>&1; then
    local notes
    notes=$(gh release view --repo rajitsaha/100xprism --json body -q .body 2>/dev/null | head -20 || true)
    if [[ -n "$notes" ]]; then
      echo "$notes"
      return
    fi
  fi
  # Fallback: format cached commit messages
  local changelog
  changelog="$(_cache_get changelog)"
  IFS='|' read -ra _lines <<< "$changelog"
  for _line in "${_lines[@]}"; do
    [[ -z "$_line" ]] && continue
    echo "• ${_line#* }"
  done
}

# ── Snooze helpers ────────────────────────────────────────────────────────────

_is_snoozed() {
  local snoozed_until
  snoozed_until="$(_cache_get snoozed_until)"
  snoozed_until="${snoozed_until:-0}"
  local now
  now="$(date +%s)"
  (( now < snoozed_until )) && return 0 || return 1
}

_snooze() {
  local until=$(( $(date +%s) + 86400 ))
  _cache_set snoozed_until "$until"
}

# ── Output modes ──────────────────────────────────────────────────────────────

_do_silent() {
  _refresh_cache
}

_do_notify() {
  # Read from cache only — no network call (that's _do_silent's job)
  local has_update
  has_update="$(_cache_get has_update)"
  [[ "$has_update" == "true" ]] || return 0
  _is_snoozed && return 0

  # Only show prompt if stdin is a terminal
  [[ -t 0 ]] || return 0

  local local_sha remote_sha changelog
  local_sha="$(_cache_get local_sha)"
  remote_sha="$(_cache_get remote_sha)"
  changelog="$(_cache_get changelog)"

  local short_local="${local_sha:0:7}"
  local short_remote="${remote_sha:0:7}"

  echo ""
  # shellcheck disable=SC2059
  printf "${YELLOW}╔══════════════════════════════════════════════════════╗${NC}\n"
  # shellcheck disable=SC2059
  printf "${YELLOW}║${NC}  %-52s${YELLOW}║${NC}\n" "100x Dev update available: $short_local → $short_remote"

  local _notes
  _notes="$(_fetch_release_notes)"
  while IFS= read -r _note; do
    [[ -z "$_note" ]] && continue
    _note="${_note:0:50}"
    # shellcheck disable=SC2059
    printf "${YELLOW}║${NC}  %-52s${YELLOW}║${NC}\n" "$_note"
  done <<< "$_notes"

  # shellcheck disable=SC2059
  printf "${YELLOW}╚══════════════════════════════════════════════════════╝${NC}\n"
  echo ""

  read -rp "Update now? (Y/n): " _confirm
  _confirm="${_confirm:-Y}"
  if [[ "$_confirm" =~ ^[Yy]$ ]]; then
    bash "$REPO_DIR/update.sh"
  else
    _snooze
    echo -e "${CYAN}Reminder snoozed for 24h. Run \`100x-update\` when ready.${NC}"
  fi
}

_do_claude_hook() {
  local has_update
  has_update="$(_cache_get has_update)"
  [[ "$has_update" == "true" ]] || return 0
  _is_snoozed && return 0

  local local_sha remote_sha changelog
  local_sha="$(_cache_get local_sha)"
  remote_sha="$(_cache_get remote_sha)"
  changelog="$(_cache_get changelog)"

  local short_local="${local_sha:0:7}"
  local short_remote="${remote_sha:0:7}"

  echo "> 100x Dev update available ($short_local → $short_remote)"
  local _notes
  _notes="$(_fetch_release_notes)"
  while IFS= read -r _note; do
    [[ -z "$_note" ]] && continue
    echo "  ${_note:0:60}"
  done <<< "$_notes"
  echo "> Run \`100x-update\` in your terminal to upgrade."
}

# ── Dispatch ──────────────────────────────────────────────────────────────────

case "$FLAG" in
  --silent)      _do_silent      ;;
  --notify)      _do_notify      ;;
  --claude-hook) _do_claude_hook ;;
  *)
    echo "Usage: check-update.sh [--silent|--notify|--claude-hook]" >&2
    exit 1
    ;;
esac
