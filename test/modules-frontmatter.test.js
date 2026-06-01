'use strict'

// Verifies the model-routing wiring in adapters/lib/modules.py (Theme 1, P0):
//   - the 9 routed modules carry a `model:` frontmatter value that resolves in the alias set
//   - no module body still carries a dead `<!-- model: X -->` comment
//   - non-Claude emitters (cursor, concat) surface a vendor-neutral tier hint, not a Claude model name

const { test } = require('node:test')
const assert = require('node:assert/strict')
const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const REPO = path.resolve(__dirname, '..')
const MODULES_PY = path.join(REPO, 'adapters', 'lib', 'modules.py')

const ALIASES = new Set(['haiku', 'sonnet', 'opus'])
const EXPECTED_MODELS = {
  architect: 'opus',
  'enterprise-design': 'opus',
  connect: 'haiku',
  'context-dump': 'haiku',
  'data-query': 'sonnet',
  lint: 'haiku',
  security: 'sonnet',
  techdebt: 'sonnet',
  'update-claude-md': 'haiku',
}
// Concrete Claude model IDs must never leak into non-Claude adapter output. We canary
// on the IDs (not friendly labels) because module *prose* may legitimately mention a
// model name (e.g. commit's `Co-Authored-By: Claude Sonnet 4.6` trailer); the IDs are
// only ever produced by MODEL_ALIASES, so their presence proves an emitter leak.
const CLAUDE_MODEL_IDS = ['claude-haiku-4-5', 'claude-sonnet-4-6', 'claude-opus-4-8']

function py(...args) {
  return execFileSync('python3', [MODULES_PY, ...args], { cwd: REPO, encoding: 'utf8' })
}

// emit-claude-code writes to $HOME/.claude/{skills,commands}; run it against a temp HOME.
function emitClaudeCode() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), '100x-home-'))
  execFileSync('python3', [MODULES_PY, 'emit-claude-code'], {
    cwd: REPO,
    encoding: 'utf8',
    env: { ...process.env, HOME: home },
  })
  return home
}

function listModules() {
  return JSON.parse(py('list'))
}

test('the 9 routed modules carry a model that resolves in the alias set', () => {
  const byId = Object.fromEntries(listModules().map((m) => [m.slug, m]))
  for (const [slug, expected] of Object.entries(EXPECTED_MODELS)) {
    const m = byId[slug]
    assert.ok(m, `module ${slug} missing from manifest`)
    assert.equal(m.model, expected, `${slug} should route to ${expected}`)
    assert.ok(ALIASES.has(m.model), `${slug} model "${m.model}" not a known alias`)
  }
})

test('no module body still contains a dead <!-- model: --> comment', () => {
  for (const m of listModules()) {
    assert.ok(
      !m.body.includes('<!-- model:'),
      `${m.slug} body still has a dead model comment`,
    )
  }
})

test('cursor .mdc emits a vendor-neutral tier hint, never a Claude model name', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), '100x-cursor-'))
  py('emit-cursor', tmp)
  const mdc = fs.readFileSync(path.join(tmp, '.cursor', 'rules', 'lint.mdc'), 'utf8')
  assert.match(mdc, /Suggested model tier:/, 'lint.mdc should carry a tier hint')
  for (const name of CLAUDE_MODEL_IDS) {
    assert.ok(!mdc.includes(name), `lint.mdc must not leak Claude model name "${name}"`)
  }
})

test('concat output surfaces the tier hint for a routed core module', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), '100x-concat-'))
  const out = path.join(tmp, 'AGENTS.md')
  py('emit-concat', out)
  const text = fs.readFileSync(out, 'utf8')
  assert.match(text, /Suggested model tier:/, 'concat should carry tier hints')
  // Both the fast (haiku) and the balanced (sonnet) tiers must be represented,
  // proving the middle tier introduced by the P1 re-tier actually emits.
  assert.match(text, /fast \/ low-cost/, 'concat should carry the haiku tier hint')
  assert.match(text, /balanced \(moderate reasoning\)/, 'concat should carry the sonnet tier hint')
  for (const name of CLAUDE_MODEL_IDS) {
    assert.ok(!text.includes(name), `concat must not leak Claude model name "${name}"`)
  }
})

test('slash-command aliases carry routing + guardrail frontmatter', () => {
  const home = emitClaudeCode()
  const cmd = (name) => fs.readFileSync(path.join(home, '.claude', 'commands', `${name}.md`), 'utf8')

  // data-query (/query): routed to sonnet, declares allowed-tools, no $ARGUMENTS → no arg hint.
  const query = cmd('query')
  assert.ok(query.startsWith('---'), 'command alias should have frontmatter')
  assert.match(query, /^model: sonnet$/m)
  assert.match(query, /^allowed-tools: Bash Read$/m)
  assert.ok(!/argument-hint:/.test(query), 'query takes no positional args → no hint')
  assert.match(query, /Use the `data-query` skill\./)

  // architect (/architect): routed to opus AND consumes $ARGUMENTS → gets a generic arg hint.
  const architect = cmd('architect')
  assert.match(architect, /^model: opus$/m)
  assert.match(architect, /^argument-hint: \[arguments\]$/m)

  // lint (/lint): haiku, no allowed-tools declared, no args.
  const lint = cmd('lint')
  assert.match(lint, /^model: haiku$/m)
  assert.ok(!/allowed-tools:/.test(lint), 'lint declares no tools → no allowed-tools line')
})

test('cursor .mdc maps tier to alwaysApply', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), '100x-cursor-tier-'))
  py('emit-cursor', tmp)
  const mdc = (slug) => fs.readFileSync(path.join(tmp, '.cursor', 'rules', `${slug}.mdc`), 'utf8')
  assert.match(mdc('lint'), /^alwaysApply: true$/m, 'core module should always apply')
  assert.match(mdc('connect'), /^alwaysApply: false$/m, 'on-demand module should not always apply')
})
