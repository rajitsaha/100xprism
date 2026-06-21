#!/usr/bin/env bash
# Test harness for shell/check-update.sh
# Uses a temporary HOME to avoid touching real state.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_DIR/shell/check-update.sh"

PASS=0
FAIL=0

_setup() {
  export HOME
  HOME="$(mktemp -d)"
  mkdir -p "$HOME/.100xprism"
  trap '_teardown' EXIT
}

_teardown() {
  rm -rf "$HOME"
}

_assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    echo "  PASS: $label"
    (( PASS++ )) || true
  else
    echo "  FAIL: $label"
    echo "    expected: $expected"
    echo "    actual:   $actual"
    (( FAIL++ )) || true
  fi
}

_assert_file_contains() {
  local label="$1" file="$2" pattern="$3"
  if grep -qF "$pattern" "$file" 2>/dev/null; then
    echo "  PASS: $label"
    (( PASS++ )) || true
  else
    echo "  FAIL: $label — pattern '$pattern' not found in $file"
    (( FAIL++ )) || true
  fi
}

_make_cache() {
  # _make_cache <home_dir> [has_update] [snoozed_until]
  local home_dir="${1:-$HOME}"
  local has_update="${2:-false}"
  local snoozed_until="${3:-0}"
  mkdir -p "$home_dir/.100xprism"
  cat > "$home_dir/.100xprism/update-cache" << EOF
last_check=9999999999
has_update=$has_update
local_sha=abc1234abc
remote_sha=def5678def
changelog=abc1234 fix: detect Bun|def5678 feat: shared lib
snoozed_until=$snoozed_until
EOF
}

# ── Tests ─────────────────────────────────────────────────────────────────────

test_creates_state_dir() {
  _setup
  rm -rf "$HOME/.100xprism"
  bash "$SCRIPT" --silent || true
  if [[ -d "$HOME/.100xprism" ]]; then
    echo "  PASS: creates ~/.100xprism on first run"
    (( PASS++ )) || true
  else
    echo "  FAIL: ~/.100xprism not created"
    (( FAIL++ )) || true
  fi
  _teardown
}

test_no_output_when_no_update() {
  _setup
  _make_cache "$HOME" false
  local output
  output=$(bash "$SCRIPT" --claude-hook 2>/dev/null)
  _assert_eq "no output when no update (--claude-hook)" "" "$output"
  _teardown
}

test_claude_hook_outputs_notice_when_update_available() {
  _setup
  _make_cache "$HOME" true
  local output
  output=$(bash "$SCRIPT" --claude-hook 2>/dev/null)
  if echo "$output" | grep -q "100x Dev update available"; then
    echo "  PASS: --claude-hook outputs update notice"
    (( PASS++ )) || true
  else
    echo "  FAIL: --claude-hook missing update notice"
    echo "    output: $output"
    (( FAIL++ )) || true
  fi
  _teardown
}

test_snoozed_suppresses_claude_hook() {
  _setup
  _make_cache "$HOME" true "$(( $(date +%s) + 86400 ))"
  local output
  output=$(bash "$SCRIPT" --claude-hook 2>/dev/null)
  _assert_eq "snoozed suppresses --claude-hook output" "" "$output"
  _teardown
}

test_silent_creates_cache_file() {
  _setup
  # Point REPO_DIR to a temp git repo to avoid real network calls
  local fake_repo
  fake_repo="$(mktemp -d)"
  cd "$fake_repo"
  git init --quiet
  git commit --allow-empty -m "init" --quiet

  HUNDRED_X_REPO_OVERRIDE="$fake_repo" bash "$SCRIPT" --silent 2>/dev/null || true

  if [[ -f "$HOME/.100xprism/update-cache" ]]; then
    echo "  PASS: --silent creates cache file"
    (( PASS++ )) || true
  else
    echo "  FAIL: --silent did not create cache file"
    (( FAIL++ )) || true
  fi
  rm -rf "$fake_repo"
  _teardown
}

test_invalid_flag_exits_nonzero() {
  _setup
  local exit_code=0
  bash "$SCRIPT" --invalid-flag 2>/dev/null || exit_code=$?
  if (( exit_code != 0 )); then
    echo "  PASS: invalid flag exits non-zero"
    (( PASS++ )) || true
  else
    echo "  FAIL: invalid flag should exit non-zero"
    (( FAIL++ )) || true
  fi
  _teardown
}

test_aliases_sources_cleanly() {
  _setup
  local fake_script="$HOME/100xprism/shell/check-update.sh"
  mkdir -p "$(dirname "$fake_script")"
  cat > "$fake_script" << 'STUB'
#!/usr/bin/env bash
# fake stub — records calls, exits cleanly
echo "called: $1" >> "$HOME/.100xprism/calls.log" 2>/dev/null || true
STUB
  chmod +x "$fake_script"

  # Source aliases in a subshell with fake HOME — should produce no error output
  local alias_file
  alias_file="$REPO_DIR/shell/aliases.sh"
  local err_output
  err_output=$(cd /tmp && HOME="$HOME" bash -c "source '$alias_file'" 2>&1) || true

  if [[ -z "$err_output" ]]; then
    echo "  PASS: aliases.sh sources without errors"
    (( PASS++ )) || true
  else
    echo "  FAIL: aliases.sh produced unexpected stderr"
    echo "    output: $err_output"
    (( FAIL++ )) || true
  fi
}

test_adapter_writes_tracked_projects() {
  _setup
  local fake_repo
  fake_repo="$(mktemp -d)"

  # Minimal fake modules dir so the python emitter has something to read
  mkdir -p "$fake_repo/modules/gate"
  printf -- "---\nname: gate\ndescription: test\ncategory: quality\ntier: core\nslash_command: /gate\n---\n# Gate\n" \
    > "$fake_repo/modules/gate/SKILL.md"
  cp "$REPO_DIR/adapters/lib/modules.py" "$fake_repo/modules.py"

  local project_dir="$HOME/myproject"
  mkdir -p "$project_dir"

  (
    export HOME
    export HUNDRED_X_REPO_OVERRIDE="$fake_repo"
    bash -c "
      source '$REPO_DIR/adapters/lib/shared.sh'
      _run_generate '$project_dir' '.cursorrules' 'TestTool'
    "
  ) 2>/dev/null || true

  local tracked="$HOME/.100xprism/tracked-projects"
  if grep -qxF "$project_dir" "$tracked" 2>/dev/null; then
    echo "  PASS: _run_generate writes project path to tracked-projects"
    (( PASS++ )) || true
  else
    echo "  FAIL: project path not found in tracked-projects"
    echo "    tracked file contents: $(cat "$tracked" 2>/dev/null || echo '(missing)')"
    (( FAIL++ )) || true
  fi

  rm -rf "$fake_repo"
}

# ── Run ───────────────────────────────────────────────────────────────────────

echo ""
echo "Running check-update tests..."
echo ""

test_creates_state_dir
test_no_output_when_no_update
test_claude_hook_outputs_notice_when_update_available
test_snoozed_suppresses_claude_hook
test_silent_creates_cache_file
test_invalid_flag_exits_nonzero
test_aliases_sources_cleanly
test_adapter_writes_tracked_projects

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
