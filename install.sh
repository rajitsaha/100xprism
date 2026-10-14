#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATES_DIR="$HOME/100x-templates"
VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null | tr -d '[:space:]')"
RC_FILE=""

# Migrate the legacy 100x-dev config/cache dir (rebrand → 100xPrism). The clone
# dir itself is migrated earlier (get.sh / npm bootstrap) before this runs.
if [ -d "$HOME/.100x-dev" ] && [ ! -d "$HOME/.100xprism" ]; then
  mv "$HOME/.100x-dev" "$HOME/.100xprism"
  echo "  → Migrated ~/.100x-dev → ~/.100xprism"
fi

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     100xPrism Setup — Installer      ║"
[ -n "$VERSION" ] && printf "║  %-36s║\n" "version v$VERSION"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Tool selection ──────────────────────────────────────────────────────────

TOOL_CLAUDE=false

select_tools() {
  echo "This installs 100xPrism globally for Claude Code."
  echo "To set up Cursor or Codex in a project,"
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
INSTALL_SHELL=false
INSTALL_TEMPLATES=true
INSTALL_HOOKS=false   # enforcing hooks are opt-in (they change commit behavior)

select_components() {
  echo ""
  echo "What would you like to install?"
  echo "  (Enter numbers to toggle, then press Enter with no input to confirm)"
  echo ""

  while true; do
    echo "  [$([ "$INSTALL_MODULES" = true ] && echo "x" || echo " ")] 1) Modules      — 68 modules (lifecycle, quality, engineering, marketing, …)"
    if [ "$TOOL_CLAUDE" = true ]; then
      echo "  [$([ "$INSTALL_PLUGINS" = true ] && echo "x" || echo " ")] 2) Plugins      — Claude Code core only: GitHub + security guidance"
    fi
    echo "  [$([ "$INSTALL_SHELL" = true ] && echo "x" || echo " ")] 3) Shell        — print manual alias instructions (no rc-file edits)"
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
  # Hermes/OpenClaw is auto-detected, not a tool the user picks in select_tools:
  # it's a global skill install with no interactive component of its own, so it
  # runs silently alongside Claude Code whenever ~/.hermes (or the hermes CLI)
  # is present, and is a no-op otherwise.
  source "$REPO_DIR/adapters/lib/shared.sh"
  if _hermes_installed; then
    _run_hermes
  fi
}

# ── Install plugins (Claude Code only) ──────────────────────────────────────

do_install_plugins() {
  if [ "$TOOL_CLAUDE" = true ]; then
    source "$REPO_DIR/adapters/claude-code.sh"
    install_plugins
  fi
}

run_preinstall_cleanup() {
  if [[ "${PRISM_PREINSTALL_CLEANUP_DONE:-}" == "1" ]]; then
    return 0
  fi
  if command -v node >/dev/null 2>&1; then
    node "$REPO_DIR/lib/uninstall.js" --preinstall-cleanup
  else
    echo -e "  ${YELLOW}→ Node.js not found; skipping legacy startup cleanup${NC}" >&2
  fi
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
  echo "Shell aliases are available but are not added to ~/.zshrc, ~/.bashrc,"
  echo "or ~/.bash_profile because shell-startup work slows new terminals."
  echo ""
  echo -e "  ${CYAN}Use aliases in this terminal only:${NC}"
  echo "    source \"$REPO_DIR/shell/aliases.sh\""
  echo ""
  echo -e "  ${CYAN}Or call the CLI directly:${NC}"
  echo "    100xprism update"
  echo ""
  echo -e "  ${CYAN}AI economics dashboard:${NC}"
  echo "    Open it anytime with:"
  echo "    100xprism tokens"
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
  echo "    Cursor       →  .cursor/rules/"
  echo "    Codex        →  AGENTS.md"
}

# ── Main ─────────────────────────────────────────────────────────────────────

run_preinstall_cleanup
select_tools
select_components

echo ""
echo "──────────────────────────────────────"

[ "$INSTALL_MODULES" = true ] && install_modules
[ "$INSTALL_PLUGINS" = true ] && [ "$TOOL_CLAUDE" = true ] && do_install_plugins
[ "$INSTALL_HOOKS" = true ] && [ "$TOOL_CLAUDE" = true ] && install_hooks
[ "$INSTALL_SHELL" = true ] && install_shell
[ "$INSTALL_TEMPLATES" = true ] && install_templates

# Read-only: reports packs relevant to this project. Never installs. Lives here
# rather than in install_plugins so Cursor-only, Codex-only, and modules-without-
# plugins installs still see the suggestion.
SUGGESTIONS=$(python3 "$REPO_DIR/adapters/lib/packs.py" detect \
  --settings "$HOME/.claude/settings.json" 2>/dev/null || true)
if [ -n "$SUGGESTIONS" ]; then
  echo ""
  echo -e "${CYAN}Optional skill packs for this project:${NC}"
  echo "$SUGGESTIONS"
  echo -e "${CYAN}Install with: /pack add <slug>${NC}"
fi

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
echo -e "    source \"$REPO_DIR/shell/aliases.sh\"  # optional aliases for this terminal only"
echo -e "    cd your-project && ${YELLOW}100xprism init${NC}  # set up a project"
echo ""

# Direct shell installs receive the same durable macOS collector as the Node CLI.
if [ "${PRISM_TOKEN_LIFECYCLE_DONE:-0}" != 1 ]; then
  if [ "$(uname -s)" = Darwin ]; then
    if command -v node >/dev/null 2>&1; then
      node -e 'const root=process.argv[1]; require(root+"/lib/token-economics").createLifecycle({stopDashboard:require(root+"/lib/dashboard").stopDashboard}).install(root)' "$REPO_DIR"
    else
      echo "Background token collection needs Node 18+. Setup deferred; install Node and rerun install.sh."
    fi
  else
    echo "Background token economics is coming soon for this platform."
  fi
fi
