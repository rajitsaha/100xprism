#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_PATH="$(cd "${1:-$PWD}" && pwd)"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo "╔══════════════════════════════════════╗"
echo "║    100x Dev — Project Setup          ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "  Project: $PROJECT_PATH"
echo ""

TOOL_CLAUDE=false
TOOL_CURSOR=false
TOOL_CODEX=false
TOOL_WINDSURF=false
TOOL_COPILOT=false
TOOL_GEMINI=false
TOOL_ANTIGRAVITY=false

select_tools() {
  echo "Which AI coding tools do you use in this project?"
  echo "  (Enter numbers to toggle, then press Enter with no input to confirm)"
  echo ""

  while true; do
    echo "  [$([ "$TOOL_CLAUDE" = true ] && echo "x" || echo " ")] 1) Claude Code"
    echo "  [$([ "$TOOL_CURSOR" = true ] && echo "x" || echo " ")] 2) Cursor"
    echo "  [$([ "$TOOL_CODEX" = true ] && echo "x" || echo " ")] 3) Codex (OpenAI)"
    echo "  [$([ "$TOOL_WINDSURF" = true ] && echo "x" || echo " ")] 4) Windsurf"
    echo "  [$([ "$TOOL_COPILOT" = true ] && echo "x" || echo " ")] 5) Copilot CLI"
    echo "  [$([ "$TOOL_GEMINI" = true ] && echo "x" || echo " ")] 6) Gemini CLI"
    echo "  [$([ "$TOOL_ANTIGRAVITY" = true ] && echo "x" || echo " ")] 7) Antigravity"
    echo ""
    read -rp "  Toggle (1-7) or press Enter to confirm: " choice || true
    case "$choice" in
      1) TOOL_CLAUDE=$([ "$TOOL_CLAUDE" = true ] && echo false || echo true) ;;
      2) TOOL_CURSOR=$([ "$TOOL_CURSOR" = true ] && echo false || echo true) ;;
      3) TOOL_CODEX=$([ "$TOOL_CODEX" = true ] && echo false || echo true) ;;
      4) TOOL_WINDSURF=$([ "$TOOL_WINDSURF" = true ] && echo false || echo true) ;;
      5) TOOL_COPILOT=$([ "$TOOL_COPILOT" = true ] && echo false || echo true) ;;
      6) TOOL_GEMINI=$([ "$TOOL_GEMINI" = true ] && echo false || echo true) ;;
      7) TOOL_ANTIGRAVITY=$([ "$TOOL_ANTIGRAVITY" = true ] && echo false || echo true) ;;
      "") break ;;
      *) echo "  Invalid choice. Enter 1-7." ;;
    esac
    echo ""
  done

  if [ "$TOOL_CLAUDE" = false ] && [ "$TOOL_CURSOR" = false ] && [ "$TOOL_CODEX" = false ] && \
     [ "$TOOL_WINDSURF" = false ] && [ "$TOOL_COPILOT" = false ] && [ "$TOOL_GEMINI" = false ] && \
     [ "$TOOL_ANTIGRAVITY" = false ]; then
    echo -e "  ${YELLOW}No tools selected. Exiting.${NC}"
    exit 1
  fi
}

select_tools

if [ "$TOOL_CLAUDE" = true ]; then
  source "$REPO_DIR/adapters/claude-code.sh"
  install_project "$PROJECT_PATH"
fi

[ "$TOOL_CURSOR" = true ]      && bash "$REPO_DIR/adapters/cursor.sh"      "$PROJECT_PATH"
[ "$TOOL_CODEX" = true ]       && bash "$REPO_DIR/adapters/codex.sh"       "$PROJECT_PATH"
[ "$TOOL_WINDSURF" = true ]    && bash "$REPO_DIR/adapters/windsurf.sh"    "$PROJECT_PATH"
[ "$TOOL_COPILOT" = true ]     && bash "$REPO_DIR/adapters/copilot.sh"     "$PROJECT_PATH"
[ "$TOOL_GEMINI" = true ]      && bash "$REPO_DIR/adapters/gemini.sh"      "$PROJECT_PATH"
[ "$TOOL_ANTIGRAVITY" = true ] && bash "$REPO_DIR/adapters/antigravity.sh" "$PROJECT_PATH"

echo ""
echo -e "${GREEN}✓ Project set up!${NC}"
echo -e "${CYAN}  Run 100xprism update any time to pull latest workflows.${NC}"
echo ""
