'use strict'

// Verifies the first-party enforcing hooks (issue #22):
//   - secret-scan blocks obvious credentials, allows placeholders
//   - gate-on-commit blocks until /gate records a pass, and re-arms when the tree changes
//   - permission-router auto-approves read-only commands, defers on risky ones
//   - emit-hooks merges idempotently, honours per-hook toggles, and --sync never enables
//     a hook the user opted out of

const { test } = require('node:test')
const assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const REPO = path.resolve(__dirname, '..')
const HOOKS = path.join(REPO, 'hooks')
const MODULES_PY = path.join(REPO, 'adapters', 'lib', 'modules.py')

function runHook(script, event, opts = {}) {
  return spawnSync('python3', [path.join(HOOKS, script)], {
    input: JSON.stringify(event),
    encoding: 'utf8',
    ...opts,
  })
}

function git(args, cwd) {
  return spawnSync('git', args, { cwd, encoding: 'utf8' })
}

function tmpGitRepo() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), '100x-gate-'))
  git(['init', '-q'], dir)
  git(['config', 'user.email', 't@t.co'], dir)
  git(['config', 'user.name', 'tester'], dir)
  fs.writeFileSync(path.join(dir, 'a.txt'), 'hello\n')
  git(['add', '-A'], dir)
  git(['commit', '-qm', 'init'], dir)
  return dir
}

// ── secret-scan ───────────────────────────────────────────────────────────────

test('secret-scan blocks an obvious AWS key', () => {
  const r = runHook('pretooluse-secret-scan.py', {
    tool_name: 'Write',
    tool_input: { file_path: 'config.py', content: 'KEY = "AKIAIOSFODNN7EXAMPLE"' },
  })
  assert.equal(r.status, 2, r.stderr)
  assert.match(r.stderr, /AWS access key/)
})

test('secret-scan blocks an OpenAI-style key', () => {
  const r = runHook('pretooluse-secret-scan.py', {
    tool_name: 'Edit',
    tool_input: { file_path: 'a.py', new_string: 'k="sk-abcdefghijklmnopqrstuvwxyz0123"' },
  })
  assert.equal(r.status, 2, r.stderr)
})

test('secret-scan allows placeholders / env indirection', () => {
  const r = runHook('pretooluse-secret-scan.py', {
    tool_name: 'Write',
    tool_input: { file_path: '.env.example', content: 'API_KEY=your_api_key_here\nT=${TOKEN}' },
  })
  assert.equal(r.status, 0, r.stderr)
})

test('secret-scan can be disabled with HOOK_SECRET_SCAN=off', () => {
  const r = runHook('pretooluse-secret-scan.py', {
    tool_name: 'Write',
    tool_input: { file_path: 'config.py', content: 'KEY = "AKIAIOSFODNN7EXAMPLE"' },
  }, { env: { ...process.env, HOOK_SECRET_SCAN: 'off' } })
  assert.equal(r.status, 0, r.stderr)
})

// ── gate-on-commit ──────────────────────────────────────────────────────────

test('gate hook blocks commit with no pass, allows after gate-pass, re-arms on edit', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), '100x-home-'))
  const repo = tmpGitRepo()
  const env = { ...process.env, HOME: home }
  const commitEvent = { tool_name: 'Bash', cwd: repo, tool_input: { command: 'git commit -m wip' } }

  // 1. no recorded pass → blocked
  let r = runHook('pretooluse-gate.py', commitEvent, { env })
  assert.equal(r.status, 2, 'expected block before any gate pass')

  // 2. record a pass → allowed
  const pass = spawnSync('python3', [path.join(HOOKS, 'gate-pass.py'), repo], { env, encoding: 'utf8' })
  assert.equal(pass.status, 0, pass.stderr)
  r = runHook('pretooluse-gate.py', commitEvent, { env })
  assert.equal(r.status, 0, `expected allow after gate pass:\n${r.stderr}`)

  // 3. push is gated the same way → still allowed for the unchanged tree
  r = runHook('pretooluse-gate.py', { tool_name: 'Bash', cwd: repo, tool_input: { command: 'git push origin main' } }, { env })
  assert.equal(r.status, 0, r.stderr)

  // 4. edit the tree → cache invalid → blocked again
  fs.appendFileSync(path.join(repo, 'a.txt'), 'changed\n')
  r = runHook('pretooluse-gate.py', { tool_name: 'Bash', cwd: repo, tool_input: { command: 'git commit -am wip' } }, { env })
  assert.equal(r.status, 2, 'expected re-armed block after a tree change')
})

test('gate hook ignores non-commit git commands and non-git dirs', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), '100x-home-'))
  const repo = tmpGitRepo()
  const env = { ...process.env, HOME: home }
  let r = runHook('pretooluse-gate.py', { tool_name: 'Bash', cwd: repo, tool_input: { command: 'git status' } }, { env })
  assert.equal(r.status, 0, 'git status must not be gated')

  const nongit = fs.mkdtempSync(path.join(os.tmpdir(), '100x-nogit-'))
  r = runHook('pretooluse-gate.py', { tool_name: 'Bash', cwd: nongit, tool_input: { command: 'git commit -m x' } }, { env })
  assert.equal(r.status, 0, 'commit outside a git repo must not be gated')
})

// ── permission-router ─────────────────────────────────────────────────────────

test('router auto-approves a read-only command', () => {
  const r = runHook('permission-router.py', { tool_name: 'Bash', tool_input: { command: 'ls -la && cat README.md' } })
  assert.equal(r.status, 0, r.stderr)
  const out = JSON.parse(r.stdout)
  assert.equal(out.hookSpecificOutput.permissionDecision, 'allow')
})

test('router defers (no decision) on a risky command', () => {
  const r = runHook('permission-router.py', { tool_name: 'Bash', tool_input: { command: 'rm -rf build' } })
  assert.equal(r.status, 0)
  assert.equal(r.stdout.trim(), '', 'risky command should not be auto-approved')
})

// ── emit-hooks merge ──────────────────────────────────────────────────────────

function emitHooks(settingsFile, extraEnv = {}, args = []) {
  return spawnSync('python3', [MODULES_PY, 'emit-hooks', ...args], {
    encoding: 'utf8',
    env: { ...process.env, SETTINGS_FILE: settingsFile, ...extraEnv },
  })
}

function readSettings(f) {
  return JSON.parse(fs.readFileSync(f, 'utf8'))
}

function commandCount(settings, needle) {
  let n = 0
  for (const arr of Object.values(settings.hooks || {})) {
    for (const entry of arr) for (const h of entry.hooks || []) {
      if ((h.command || '').includes(needle)) n++
    }
  }
  return n
}

test('emit-hooks is idempotent and preserves user hooks', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), '100x-set-'))
  const f = path.join(dir, 'settings.json')
  fs.writeFileSync(f, JSON.stringify({
    hooks: { PreToolUse: [{ matcher: 'Bash', hooks: [{ type: 'command', command: 'echo mine' }] }] },
  }))
  emitHooks(f)
  emitHooks(f) // run twice
  const s = readSettings(f)
  assert.equal(commandCount(s, 'pretooluse-gate.py'), 1, 'gate hook must appear exactly once')
  assert.equal(commandCount(s, 'pretooluse-secret-scan.py'), 1, 'secret-scan on by default')
  assert.equal(commandCount(s, 'echo mine'), 1, "user's own hook must be preserved")
})

test('emit-hooks toggles add/remove declaratively', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), '100x-set-'))
  const f = path.join(dir, 'settings.json')
  fs.writeFileSync(f, '{}')
  emitHooks(f, { HOOK_ROUTER: '1', HOOK_SECRET: '0' })
  let s = readSettings(f)
  assert.equal(commandCount(s, 'permission-router.py'), 1, 'router enabled by toggle')
  assert.equal(commandCount(s, 'pretooluse-secret-scan.py'), 0, 'secret disabled by toggle')

  // flip secret back on, router off → declarative
  emitHooks(f, { HOOK_ROUTER: '0', HOOK_SECRET: '1' })
  s = readSettings(f)
  assert.equal(commandCount(s, 'permission-router.py'), 0)
  assert.equal(commandCount(s, 'pretooluse-secret-scan.py'), 1)
})

test('emit-hooks --sync only refreshes already-present hooks', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), '100x-set-'))
  const f = path.join(dir, 'settings.json')
  fs.writeFileSync(f, '{}')
  // sync against empty settings must NOT add the (off-by-default) lint hook, even if toggled on
  emitHooks(f, { HOOK_LINT: '1' }, ['--sync'])
  const s = readSettings(f)
  assert.equal(commandCount(s, 'posttooluse-lint.py'), 0, 'sync must not enable an absent hook')
})
