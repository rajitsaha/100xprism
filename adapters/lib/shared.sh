#!/usr/bin/env bash
# shared.sh — common logic for all 100x-dev adapter scripts.
# Source this file; do not execute directly.
#
# All adapters dispatch to adapters/lib/modules.py for module reading and
# rendering. Adapters here provide thin shell wrappers + per-tool output paths.

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$_LIB_DIR/../.." && pwd)"
MODULES_PY="$_LIB_DIR/modules.py"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# _run_concat <project_path> <output_file_relative> <display_name> [<mode>] [<warning>]
#
# Used by tools that consume a single rules file (Codex AGENTS.md, Windsurf, etc.).
# mode is 'concat' (default — full core bodies + on-demand index) or 'index'
# (one-line per module; used when the tool has tight size limits).
_run_concat() {
  local project_path="$1"
  local output_rel="$2"
  local display_name="$3"
  local mode="${4:-concat}"
  local warning_message="${5:-}"

  local output_file="$project_path/$output_rel"
  mkdir -p "$(dirname "$output_file")"

  echo ""
  echo "Generating $output_rel for $display_name..."
  if [[ -n "$warning_message" ]]; then
    echo -e "${YELLOW}${warning_message}${NC}"
  fi

  python3 "$MODULES_PY" emit-concat "$output_file" "$mode"

  _track_project "$project_path"
  echo -e "  ${GREEN}→ Generated $output_file ✓${NC}"
}

# _run_cursor <project_path>
# Cursor supports per-rule files with description-based auto-trigger, so we
# write one .cursor/rules/<slug>.mdc per module.
_run_cursor() {
  local project_path="$1"
  echo ""
  echo "Generating .cursor/rules/ for Cursor..."
  python3 "$MODULES_PY" emit-cursor "$project_path"
  _track_project "$project_path"
  echo -e "  ${GREEN}→ Generated .cursor/rules/ in $project_path ✓${NC}"
}

# _run_codex <project_path>
# Codex supports repo-scoped skills and hooks, so we keep AGENTS.md compact and
# emit full module bodies into .agents/skills for progressive loading.
_run_codex() {
  local project_path="$1"
  echo ""
  echo "Generating Codex project artifacts..."
  python3 "$MODULES_PY" emit-codex "$project_path"
  _track_project "$project_path"
  echo -e "  ${GREEN}→ Generated AGENTS.md, .agents/skills/, and .codex/hooks.json in $project_path ✓${NC}"
  echo -e "  ${YELLOW}→ In Codex, run /hooks to review and trust generated hooks.${NC}"
}

_track_project() {
  local project_path="$1"
  local _tracked_file="$HOME/.100x-dev/tracked-projects"
  mkdir -p "$(dirname "$_tracked_file")"
  local _abs_path
  _abs_path="$(cd "$project_path" && pwd)"
  if ! grep -qxF "$_abs_path" "$_tracked_file" 2>/dev/null; then
    echo "$_abs_path" >> "$_tracked_file"
  fi
}

# Back-compat: old adapters call _run_generate with a single output file.
# Keep it as an alias for _run_concat with default 'concat' mode.
_run_generate() {
  _run_concat "$@"
}
