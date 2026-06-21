#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATES_DIR="$HOME/100x-templates"
VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null | tr -d '[:space:]')"
RC_FILE=""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo "╔══════════════════════════════════════╗"
echo "║      100x Dev Setup — Installer      ║"
[ -n "$VERSION" ] && printf "║  %-36s║\n" "version v$VERSION"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Tool selection ──────────────────────────────────────────────────────────

TOOL_CLAUDE=false

select_tools() {
  echo "This installs 100x Dev globally for Claude Code."
  echo "To set up Cursor, Codex, Windsurf, Copilot, Gemini, or Antigravity in a project,"
  echo "run  100xprism init  from that project directory after this completes."
  echo ""
  read -rp "  Install for Claude Code? [Y/n]: " yn || true
  yn="${yn:-Y}"
  if [[ "$yn" =~ ^[Yy]$ ]]; then
    TOOL_CLAUDE=true
  else
    echo "  Nothing to install. Exiting."
    exit 0
  fi
}

# ── Component selection ─────────────────────────────────────────────────────

INSTALL_MODULES=true
INSTALL_PLUGINS=true
INSTALL_SHELL=true
INSTALL_TEMPLATES=true
INSTALL_HOOKS=false   # enforcing hooks are opt-in (they change commit behavior)

select_components() {
  echo ""
  echo "What would you like to install?"
  echo "  (Enter numbers to toggle, then press Enter with no input to confirm)"
  echo ""

  while true; do
    echo "  [$([ "$INSTALL_MODULES" = true ] && echo "x" || echo " ")] 1) Modules      — 66 modules (lifecycle, quality, engineering, marketing, …)"
    if [ "$TOOL_CLAUDE" = true ]; then
      echo "  [$([ "$INSTALL_PLUGINS" = true ] && echo "x" || echo " ")] 2) Plugins      — Claude Code only: superpowers, hookify, claude-mem, ..."
    fi
    echo "  [$([ "$INSTALL_SHELL" = true ] && echo "x" || echo " ")] 3) Shell        — aliases + shortcuts (cc, ccc, 100x-update, ...)"
    echo "  [$([ "$INSTALL_TEMPLATES" = true ] && echo "x" || echo " ")] 4) Templates   — project starters (node, python, docker)"
    if [ "$TOOL_CLAUDE" = true ]; then
      echo "  [$([ "$INSTALL_HOOKS" = true ] && echo "x" || echo " ")] 5) Hooks        — Claude Code only: enforce the gate on commit + secret-scan (opt-in)"
    fi
    echo ""
    read -rp "  Toggle (1-5) or press Enter to confirm: " choice || true

    case "$choice" in
      1) INSTALL_MODULES=$([ "$INSTALL_MODULES" = true ] && echo false || echo true) ;;
      2) [ "$TOOL_CLAUDE" = true ] && INSTALL_PLUGINS=$([ "$INSTALL_PLUGINS" = true ] && echo false || echo true) ;;
      3) INSTALL_SHELL=$([ "$INSTALL_SHELL" = true ] && echo false || echo true) ;;
      4) INSTALL_TEMPLATES=$([ "$INSTALL_TEMPLATES" = true ] && echo false || echo true) ;;
      5) [ "$TOOL_CLAUDE" = true ] && INSTALL_HOOKS=$([ "$INSTALL_HOOKS" = true ] && echo false || echo true) ;;
      "") break ;;
      *) echo "  Invalid choice." ;;
    esac
    echo ""
  done
}

# ── Install workflows ───────────────────────────────────────────────────────

install_modules() {
  if [ "$TOOL_CLAUDE" = true ]; then
    source "$REPO_DIR/adapters/claude-code.sh"
    install_global
  fi
}

# ── Install plugins (Claude Code only) ──────────────────────────────────────

do_install_plugins() {
  if [ "$TOOL_CLAUDE" = true ]; then
    source "$REPO_DIR/adapters/claude-code.sh"
    install_plugins
    _install_session_hook
  fi
}

_install_session_hook() {
  local settings_file="$HOME/.claude/settings.json"
  [[ -f "$settings_file" ]] || return 0

  SETTINGS_FILE="$settings_file" python3 - <<'PYEOF'
import json, os

settings_file = os.environ['SETTINGS_FILE']
hook_cmd = os.path.expanduser('~/100xprism/shell/check-update.sh') + ' --claude-hook'

with open(settings_file) as f:
    settings = json.load(f)

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
PYEOF
}

# ── Install enforcing hooks (Claude Code only) ──────────────────────────────

install_hooks() {
  [ "$TOOL_CLAUDE" = true ] || return 0
  echo ""
  echo "Installing enforcing hooks for Claude Code..."
  echo "  These run via ~/.claude/settings.json. Pick which to enable:"
  echo ""

  # Defaults mirror hooks/hooks.manifest.json: gate + secret on, lint + router off.
  local H_GATE=true H_SECRET=true H_LINT=false H_ROUTER=false
  while true; do
    echo "  [$([ "$H_GATE" = true ] && echo "x" || echo " ")] 1) gate-on-commit   — block git commit/push unless /gate passed for the tree"
    echo "  [$([ "$H_SECRET" = true ] && echo "x" || echo " ")] 2) secret-scan      — block writes containing obvious credentials"
    echo "  [$([ "$H_LINT" = true ] && echo "x" || echo " ")] 3) lint-on-save     — advisory lint after each edit (never blocks)"
    echo "  [$([ "$H_ROUTER" = true ] && echo "x" || echo " ")] 4) permission-router — auto-approve known read-only Bash commands"
    echo ""
    read -rp "  Toggle (1-4) or press Enter to confirm: " hchoice || true
    case "$hchoice" in
      1) H_GATE=$([ "$H_GATE" = true ] && echo false || echo true) ;;
      2) H_SECRET=$([ "$H_SECRET" = true ] && echo false || echo true) ;;
      3) H_LINT=$([ "$H_LINT" = true ] && echo false || echo true) ;;
      4) H_ROUTER=$([ "$H_ROUTER" = true ] && echo false || echo true) ;;
      "") break ;;
      *) echo "  Invalid choice." ;;
    esac
    echo ""
  done

  HOOK_GATE="$H_GATE" HOOK_SECRET="$H_SECRET" HOOK_LINT="$H_LINT" HOOK_ROUTER="$H_ROUTER" \
    python3 "$REPO_DIR/adapters/lib/modules.py" emit-hooks

  echo -e "  ${GREEN}→ Hooks merged into ~/.claude/settings.json ✓${NC}"
  echo -e "  ${CYAN}→ Restart Claude Code to load hooks${NC}"
}

# ── Install shell aliases ───────────────────────────────────────────────────

install_shell() {
  echo ""
  echo "Installing shell aliases..."

  SOURCE_LINE="source $REPO_DIR/shell/aliases.sh"

  if [ -f "$HOME/.zshrc" ]; then
    RC_FILE="$HOME/.zshrc"
    SHELL_NAME="zsh"
  elif [ -f "$HOME/.bashrc" ]; then
    RC_FILE="$HOME/.bashrc"
    SHELL_NAME="bash"
  else
    RC_FILE="$HOME/.bashrc"
    SHELL_NAME="bash"
    touch "$RC_FILE"
  fi

  # Remove old claude-dev-setup source line if present
  if grep -qF "claude-dev-setup/shell/claude-aliases.sh" "$RC_FILE" 2>/dev/null; then
    grep -v "claude-dev-setup/shell/claude-aliases.sh" "$RC_FILE" > "$RC_FILE.tmp" && mv "$RC_FILE.tmp" "$RC_FILE"
    echo -e "  ${YELLOW}→ Removed old claude-dev-setup alias line${NC}"
  fi

  if grep -qF "$SOURCE_LINE" "$RC_FILE" 2>/dev/null; then
    echo -e "  ${YELLOW}→ Already sourced in ~/${RC_FILE##*/} (no change)${NC}"
  else
    { echo ""; echo "# 100x Dev aliases"; echo "$SOURCE_LINE"; } >> "$RC_FILE"
    echo -e "  ${GREEN}→ Added source line to ~/${RC_FILE##*/} ($SHELL_NAME) ✓${NC}"
  fi

  echo -e "  ${CYAN}→ Run: source ~/${RC_FILE##*/}  to activate now${NC}"
}

# ── Install templates ───────────────────────────────────────────────────────

install_templates() {
  echo ""
  echo "Installing templates..."
  mkdir -p "$TEMPLATES_DIR"

  count=0
  for f in "$REPO_DIR/templates/"*.md; do
    cp "$f" "$TEMPLATES_DIR/"
    count=$((count + 1))
  done

  echo -e "  ${GREEN}→ Copied $count templates to ~/100x-templates/ ✓${NC}"
  echo ""
  echo "  Copy a template into your project and rename for your tool:"
  echo "    Claude Code  →  CLAUDE.md"
  echo "    Cursor       →  .cursorrules"
  echo "    Codex        →  AGENTS.md"
  echo "    Windsurf     →  .windsurfrules"
  echo "    Copilot      →  .github/copilot-instructions.md"
  echo "    Gemini CLI   →  GEMINI.md"
}

# ── Main ─────────────────────────────────────────────────────────────────────

select_tools
select_components

echo ""
echo "──────────────────────────────────────"

[ "$INSTALL_MODULES" = true ] && install_modules
[ "$INSTALL_PLUGINS" = true ] && [ "$TOOL_CLAUDE" = true ] && do_install_plugins
[ "$INSTALL_HOOKS" = true ] && [ "$TOOL_CLAUDE" = true ] && install_hooks
[ "$INSTALL_SHELL" = true ] && install_shell
[ "$INSTALL_TEMPLATES" = true ] && install_templates

echo ""
echo "──────────────────────────────────────"
echo -e "${GREEN}✓ Done!${NC}"
echo ""
if [ "$TOOL_CLAUDE" = true ]; then
  echo -e "  ${CYAN}In Claude Code:${NC}"
  echo -e "    Restart Claude Code to load modules and plugins."
  echo ""
fi
echo -e "  ${CYAN}In your terminal:${NC}"
if [ -n "$RC_FILE" ]; then
  echo -e "    source ~/${RC_FILE##*/}          # reload shell aliases"
fi
echo -e "    cd your-project && ${YELLOW}100xprism init${NC}  # set up a project"
echo ""
