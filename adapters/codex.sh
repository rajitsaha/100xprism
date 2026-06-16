#!/usr/bin/env bash
set -e

# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/lib/shared.sh"

install_project() {
  _run_codex "${1:-.}"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  install_project "${1:-.}"
fi
