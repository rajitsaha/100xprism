'use strict'

// Verifies scripts/eval-harness.py (issue #24):
//   - validate passes on the real repo's 32 eval files
//   - validate fails on a malformed eval file
//   - plan --json emits the case/assertion work-list
//   - score renders a per-assertion pass/fail scorecard and exits non-zero on a failure

const { test } = require('node:test')
const assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const REPO = path.resolve(__dirname, '..')
const SCRIPT = path.join(REPO, 'scripts', 'eval-harness.py')

function run(args, opts = {}) {
  return spawnSync('python3', [SCRIPT, ...args], { cwd: REPO, encoding: 'utf8', ...opts })
}

test('validate --all passes on the real repo', () => {
  const r = run(['validate', '--all'])
  assert.equal(r.status, 0, `${r.stdout}\n${r.stderr}`)
  assert.match(r.stdout, /all eval files well-formed/)
  assert.match(r.stdout, /32 module\(s\)/)
})

test('validate --module reports cases and assertions', () => {
  const r = run(['validate', '--module', 'marketing-psychology'])
  assert.equal(r.status, 0, r.stderr)
  assert.match(r.stdout, /validated 1 module\(s\)/)
})

test('plan --json emits cases with assertions', () => {
  const r = run(['plan', '--module', 'marketing-psychology', '--json'])
  assert.equal(r.status, 0, r.stderr)
  const plan = JSON.parse(r.stdout)
  assert.equal(plan.modules.length, 1)
  const m = plan.modules[0]
  assert.equal(m.module, 'marketing-psychology')
  assert.ok(m.cases.length >= 1)
  assert.ok(Array.isArray(m.cases[0].assertions) && m.cases[0].assertions.length >= 1)
  assert.ok(typeof m.cases[0].prompt === 'string' && m.cases[0].prompt.length > 0)
})

test('validate fails on a malformed eval file', () => {
  // run the harness against a throwaway repo layout via a temp module dir is overkill;
  // instead point validate at a slug whose evals.json we corrupt in a temp copy.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), '100x-eval-'))
  const modDir = path.join(tmp, 'modules', 'broken', 'evals')
  fs.mkdirSync(modDir, { recursive: true })
  fs.writeFileSync(path.join(modDir, 'evals.json'), '{ not json')
  // Copy the harness so REPO resolves to our temp tree (parents[1] of scripts/).
  fs.mkdirSync(path.join(tmp, 'scripts'))
  fs.copyFileSync(SCRIPT, path.join(tmp, 'scripts', 'eval-harness.py'))
  const r = spawnSync('python3', [path.join(tmp, 'scripts', 'eval-harness.py'), 'validate', '--module', 'broken'],
    { cwd: tmp, encoding: 'utf8' })
  assert.equal(r.status, 1, 'malformed eval file must fail validation')
  assert.match(r.stderr, /invalid JSON/)
})

test('score renders a pass/fail scorecard and exits non-zero on failure', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), '100x-score-'))
  const results = path.join(tmp, 'results.json')
  fs.writeFileSync(results, JSON.stringify([
    { module: 'demo', case_id: 1, assertion: 'does the thing', passed: true, reason: '' },
    { module: 'demo', case_id: 1, assertion: 'handles the edge case', passed: false, reason: 'ignored empty input' },
  ]))
  const r = run(['score', '--results', results])
  assert.equal(r.status, 1, 'a failed assertion must make score exit non-zero')
  assert.match(r.stdout, /✓ does the thing/)
  assert.match(r.stdout, /✗ handles the edge case — ignored empty input/)
  assert.match(r.stdout, /1\/2 assertions passed/)
})

test('score exits zero when every assertion passes', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), '100x-score-'))
  const results = path.join(tmp, 'results.json')
  fs.writeFileSync(results, JSON.stringify([
    { module: 'demo', case_id: 1, assertion: 'a', passed: true, reason: '' },
  ]))
  const r = run(['score', '--results', results])
  assert.equal(r.status, 0, r.stderr)
})
