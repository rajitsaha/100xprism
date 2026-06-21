# shellcheck shell=bash
# 100xPrism shortcuts
# Source this file from ~/.zshrc or ~/.bashrc:
#   source ~/100xprism/shell/aliases.sh

# Launch Claude
alias cc='claude'
alias ccc='claude --continue'

# Setup management
# shellcheck disable=SC2139
alias 100xprism="node $HOME/100xprism/bin/100xprism.js"
# shellcheck disable=SC2139
alias 100x-update="$HOME/100xprism/update.sh"
# shellcheck disable=SC2139
alias 100x-check="$HOME/100xprism/update.sh --check-only"

# Token usage — one machine-wide dashboard (all sessions & repos); auto-opens the
# URL, and relaunching from any session just opens the already-running one.
# shellcheck disable=SC2139
alias 100x-tokens="python3 $HOME/100xprism/scripts/token-dashboard.py"
# What shipped (value, to read next to token cost) — defaults to the current repo.
# shellcheck disable=SC2139
alias 100x-value="python3 $HOME/100xprism/scripts/value-report.py"

# ── Version check ─────────────────────────────────────────────────────────────
# On shell startup: read cached update status (no network) + prompt if available.
# Then kick off a background cache refresh for next session.
if [[ -x "$HOME/100xprism/shell/check-update.sh" ]]; then
  bash "$HOME/100xprism/shell/check-update.sh" --notify
  ("$HOME/100xprism/shell/check-update.sh" --silent &) 2>/dev/null
fi

# ── Token-usage line (cache-only, fast; silent until you've run 100x-tokens once) ──
# Opt out any time:  export PRISM_NO_TOKEN_LINE=1
if [[ -z "${PRISM_NO_TOKEN_LINE:-}" && -f "$HOME/100xprism/scripts/token-dashboard.py" ]]; then
  python3 "$HOME/100xprism/scripts/token-dashboard.py" --oneline 2>/dev/null
fi
