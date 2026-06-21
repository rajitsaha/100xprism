#!/usr/bin/env bash
# changelog.sh — generate or update CHANGELOG.md from git tags and commits
#
# Usage:
#   scripts/changelog.sh             Print changelog for unreleased commits
#   scripts/changelog.sh --release   Tag current HEAD and add entry to CHANGELOG.md

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANGELOG="$REPO_DIR/CHANGELOG.md"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

# Get the latest tag (if any)
_latest_tag() {
  git -C "$REPO_DIR" tag --sort=-version:refname 2>/dev/null | head -1 || true
}

# Get commits since last tag (or all commits if no tags)
_unreleased_commits() {
  local latest_tag
  latest_tag="$(_latest_tag)"
  if [[ -n "$latest_tag" ]]; then
    git -C "$REPO_DIR" log --oneline "${latest_tag}..HEAD" 2>/dev/null
  else
    git -C "$REPO_DIR" log --oneline 2>/dev/null
  fi
}

# Format commit list into changelog bullets grouped by type
_format_commits() {
  local commits="$1"
  local added="" changed="" fixed=""

  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    local msg="${line#* }"  # strip SHA

    case "$msg" in
      feat:*|feat\(*)  added+="- ${msg#feat*: }"$'\n' ;;
      fix:*|fix\(*)    fixed+="- ${msg#fix*: }"$'\n' ;;
      docs:*|chore:*|refactor:*|test:*) changed+="- ${msg}"$'\n' ;;
      *)               changed+="- ${msg}"$'\n' ;;
    esac
  done <<< "$commits"

  [[ -n "$added"   ]] && printf "### Added\n%s\n" "$added"
  [[ -n "$fixed"   ]] && printf "### Fixed\n%s\n" "$fixed"
  [[ -n "$changed" ]] && printf "### Changed\n%s\n" "$changed"
}

_do_release() {
  read -rp "Version (e.g. 1.1.0): " version
  [[ -z "$version" ]] && { echo "Version required." >&2; exit 1; }

  local tag="v$version"
  local date
  date="$(date +%Y-%m-%d)"

  local commits
  commits="$(_unreleased_commits)"

  if [[ -z "$commits" ]]; then
    echo "No unreleased commits. Nothing to release."
    exit 0
  fi

  local formatted_commits
  formatted_commits="$(_format_commits "$commits")"

  local entry
  entry="## [$version] — $date"$'\n\n'"$formatted_commits"

  # Prepend after the first --- separator in CHANGELOG.md
  local tmp entry_file
  tmp="$(mktemp)"
  entry_file="$(mktemp)"
  printf '%s\n' "$entry" > "$entry_file"
  awk -v ef="$entry_file" '
    /^---$/ && !done {
      print; print ""
      while ((getline line < ef) > 0) print line
      print "---"; done=1; next
    }
    { print }
  ' "$CHANGELOG" > "$tmp"
  rm -f "$entry_file"
  mv "$tmp" "$CHANGELOG"

  echo "$version" > "$REPO_DIR/VERSION"

  git -C "$REPO_DIR" tag -a "$tag" -m "Release $tag"
  echo -e "${GREEN}Tagged $tag, updated CHANGELOG.md and VERSION${NC}"

  read -rp "Push tag $tag now to trigger GitHub Release? (Y/n): " _push
  _push="${_push:-Y}"
  if [[ "$_push" =~ ^[Yy]$ ]]; then
    git -C "$REPO_DIR" push origin "$tag"
    echo -e "${GREEN}Tag $tag pushed — GitHub Release will be created automatically.${NC}"
    echo -e "${CYAN}Watch: https://github.com/rajitsaha/100xprism/releases${NC}"
  else
    echo -e "${CYAN}  Push when ready: git push origin $tag${NC}"
  fi
}

case "${1:-}" in
  --release)
    _do_release
    ;;

  *)
    commits="$(_unreleased_commits)"
    if [[ -z "$commits" ]]; then
      echo "No unreleased commits since $(_latest_tag)."
    else
      latest_tag="$(_latest_tag)"
      echo ""
      echo "## [Unreleased]${latest_tag:+ since $latest_tag}"
      echo ""
      _format_commits "$commits"
    fi
    ;;
esac
