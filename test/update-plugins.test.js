'use strict'

// Verifies update.sh's run_plugin_updates() is scope-aware (rectifies the
// project/local-scope false-failure for understand-anything / ui-ux-pro-max):
//   - a plugin installed at project scope (fails at user scope) is still updated
//   - a plugin absent from every scope is reported as failed
//
// The shell function is loaded in isolation via UPDATE_SH_SOURCE_ONLY=1 and run
// against a stubbed `claude` CLI injected through CLAUDE_BIN, so no real plugin
// installs or network access are touched.

const { test } = require('node:test')
const assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const REPO = path.resolve(__dirname, '..')
const UPDATE_SH = path.join(REPO, 'update.sh')

// Build a throwaway workspace: a fake REPO_DIR holding plugins/plugins.json and
// a stub `claude` whose per-scope behaviour is scripted by a manifest file.
function workspace(plugins, behaviour) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), '100x-upd-'))
  fs.mkdirSync(path.join(dir, 'plugins'))
  fs.writeFileSync(
    path.join(dir, 'plugins', 'plugins.json'),
    JSON.stringify({ plugins }, null, 2),
  )

  // behaviour: { "<id>": ["scope-that-succeeds", ...] }. The stub exits 0 only
  // when invoked as `plugin update <id> --scope <s>` with s in that list.
  const bin = path.join(dir, 'bin')
  fs.mkdirSync(bin)
  fs.writeFileSync(path.join(dir, 'behaviour.json'), JSON.stringify(behaviour))
  const stub = `#!/usr/bin/env bash
# args: plugin update <id> --scope <scope>
id="$3"; scope="$5"
ok=$(python3 -c "import json,sys; b=json.load(open('${dir}/behaviour.json')); print('1' if sys.argv[2] in b.get(sys.argv[1],[]) else '0')" "$id" "$scope")
if [ "$ok" = "1" ]; then echo "updated $id @ $scope"; exit 0; fi
echo "Plugin \\"$id\\" is not installed at scope $scope" >&2
exit 1
`
  fs.writeFileSync(path.join(bin, 'claude'), stub, { mode: 0o755 })
  return { dir, bin }
}

function runPluginUpdates({ dir, bin }) {
  // Source update.sh for its functions only, then call run_plugin_updates with
  // REPO_DIR pointed at the fake workspace and CLAUDE_BIN at the stub.
  const script = `
    set -euo pipefail
    export UPDATE_SH_SOURCE_ONLY=1
    export CLAUDE_BIN='${path.join(bin, 'claude')}'
    source '${UPDATE_SH}'
    REPO_DIR='${dir}'
    run_plugin_updates
  `
  return spawnSync('bash', ['-c', script], { encoding: 'utf8' })
}

test('project-scoped plugin updates instead of falsely failing', () => {
  const ws = workspace(
    ['ui-ux-pro-max@ui-ux-pro-max-skill'],
    { 'ui-ux-pro-max@ui-ux-pro-max-skill': ['project'] }, // not at user scope
  )
  const r = runPluginUpdates(ws)
  assert.equal(r.status, 0, r.stderr)
  assert.match(r.stdout, /1 updated/)
  assert.doesNotMatch(r.stdout, /failed/)
})

test('user-scoped plugin still updates on the first attempt', () => {
  const ws = workspace(
    ['superpowers@claude-plugins-official'],
    { 'superpowers@claude-plugins-official': ['user'] },
  )
  const r = runPluginUpdates(ws)
  assert.equal(r.status, 0, r.stderr)
  assert.match(r.stdout, /1 updated/)
})

test('plugin absent from every scope is reported as failed', () => {
  const ws = workspace(
    ['ghost@nowhere'],
    { 'ghost@nowhere': [] }, // fails at user, project, and local
  )
  const r = runPluginUpdates(ws)
  assert.equal(r.status, 0, r.stderr)
  assert.match(r.stdout, /1 failed/)
  assert.match(r.stdout, /ghost@nowhere/)
})
