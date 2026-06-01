'use strict'

// Verifies scripts/meta-check.py — the repo drift gate (issue #23):
//   - passes on the real repo (counts, frontmatter, evals, version triple all agree)
//   - fails (exit 1) when the git tag disagrees with the version triple

const { test } = require('node:test')
const assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
const path = require('node:path')

const REPO = path.resolve(__dirname, '..')
const SCRIPT = path.join(REPO, 'scripts', 'meta-check.py')

function run(args = []) {
  return spawnSync('python3', [SCRIPT, ...args], { cwd: REPO, encoding: 'utf8' })
}

test('meta-check passes on the current repo state', () => {
  const r = run()
  assert.equal(r.status, 0, `expected exit 0, got ${r.status}\n${r.stdout}\n${r.stderr}`)
  assert.match(r.stdout, /all checks passed/)
})

test('meta-check reports the four README counts', () => {
  const r = run()
  for (const label of ['modules', 'slash commands', 'auto-trigger skills', 'plugins']) {
    assert.match(r.stdout, new RegExp(`README '${label}' count`))
  }
})

test('meta-check fails when the git tag disagrees with the version triple', () => {
  const r = run(['--tag', 'v0.0.0-nope'])
  assert.equal(r.status, 1, 'expected non-zero exit on tag mismatch')
  assert.match(r.stderr, /version drift: git tag/)
})
