'use strict'

// Verifies scripts/trigger-overlap.py (issue #24):
//   - flags the known overlapping pairs named in the issue
//   - --strict passes on the current repo (every flagged pair is allow-listed)
//   - --strict fails when an allow-listed pair is removed (i.e. a "new" overlap appears)

const { test } = require('node:test')
const assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const REPO = path.resolve(__dirname, '..')
const SCRIPT = path.join(REPO, 'scripts', 'trigger-overlap.py')
const ALLOW = path.join(REPO, 'scripts', 'trigger-overlap-allow.txt')

function run(args = [], opts = {}) {
  return spawnSync('python3', [SCRIPT, ...args], { cwd: REPO, encoding: 'utf8', ...opts })
}

function flaggedPairs() {
  const r = run(['--json'])
  assert.equal(r.status, 0, r.stderr)
  return JSON.parse(r.stdout).flagged.map((f) => f.pair.slice().sort().join(' <-> '))
}

test('flags the known overlapping pairs from the issue', () => {
  // conversion-copy and systems-architect were merged into copywriting /
  // enterprise-design respectively, resolving two of the issue's pairs. The
  // remaining intentional CRO-funnel overlaps must still be flagged.
  const pairs = new Set(flaggedPairs())
  for (const pair of [
    ['form-cro', 'signup-flow-cro'],
    ['onboarding-cro', 'signup-flow-cro'],
  ]) {
    assert.ok(pairs.has(pair.slice().sort().join(' <-> ')), `expected to flag ${pair.join(' ⇄ ')}`)
  }
})

test('--strict passes on the current repo (all flagged pairs allow-listed)', () => {
  const r = run(['--strict'])
  assert.equal(r.status, 0, `expected 0 new overlaps:\n${r.stdout}\n${r.stderr}`)
  assert.match(r.stdout, /0 new/)
})

test('--strict fails when a previously-accepted overlap is no longer allow-listed', () => {
  // Copy repo descriptions are stable; simulate a "new" overlap by running with an
  // empty allow-list via a temp copy of the script pointing at an empty allow file.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), '100x-overlap-'))
  fs.mkdirSync(path.join(tmp, 'scripts'))
  // Point the script's ALLOW_FILE at an empty file by copying it beside an empty allow.
  let src = fs.readFileSync(SCRIPT, 'utf8')
  fs.writeFileSync(path.join(tmp, 'scripts', 'trigger-overlap-allow.txt'), '# empty\n')
  fs.writeFileSync(path.join(tmp, 'scripts', 'trigger-overlap.py'), src)
  // Symlink modules/ so the temp script sees the real descriptions (REPO = parents[1]).
  fs.symlinkSync(path.join(REPO, 'modules'), path.join(tmp, 'modules'))
  const r = spawnSync('python3', [path.join(tmp, 'scripts', 'trigger-overlap.py'), '--strict'],
    { cwd: tmp, encoding: 'utf8' })
  assert.equal(r.status, 1, 'an empty allow-list must surface the existing overlaps as new')
  assert.match(r.stderr, /new overlapping pair/)
})

test('allow-list file exists and lists the named pairs', () => {
  const allow = fs.readFileSync(ALLOW, 'utf8')
  assert.match(allow, /form-cro <-> signup-flow-cro/)
  assert.match(allow, /onboarding-cro <-> signup-flow-cro/)
})
