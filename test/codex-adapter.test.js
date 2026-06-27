'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const { execFileSync, spawnSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const REPO = path.resolve(__dirname, '..')
const MODULES_PY = path.join(REPO, 'adapters', 'lib', 'modules.py')

function makeTmpDir(prefix = '100x-codex-') {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix))
}

function emitCodex(projectDir) {
  execFileSync('python3', [MODULES_PY, 'emit-codex', projectDir], {
    cwd: REPO,
    encoding: 'utf8',
  })
}

test('codex adapter emits compact AGENTS plus repo skills', () => {
  const tmp = makeTmpDir()
  emitCodex(tmp)

  const agentsPath = path.join(tmp, 'AGENTS.md')
  const agents = fs.readFileSync(agentsPath, 'utf8')
  assert.ok(Buffer.byteLength(agents, 'utf8') < 32 * 1024, 'AGENTS.md should fit Codex default project-doc budget')
  assert.match(agents, /Full reusable workflows live in `\.agents\/skills\/<slug>\/SKILL\.md`/)
  assert.match(agents, /`\/gate` → `\$gate`/)
  assert.match(agents, /Claude Code plugins .* are not Codex plugins/)

  const skillsDir = path.join(tmp, '.agents', 'skills')
  const skillFiles = fs.readdirSync(skillsDir)
    .filter((name) => fs.existsSync(path.join(skillsDir, name, 'SKILL.md')))
  assert.equal(skillFiles.length, 66)
  assert.ok(fs.existsSync(path.join(skillsDir, 'gate', 'SKILL.md')))
  assert.ok(fs.existsSync(path.join(skillsDir, 'copywriting', 'SKILL.md')))
})

test('codex adapter emits hooks.json for Codex hook review flow', () => {
  const tmp = makeTmpDir()
  emitCodex(tmp)

  const hooksPath = path.join(tmp, '.codex', 'hooks.json')
  const hooksText = fs.readFileSync(hooksPath, 'utf8')
  const config = JSON.parse(hooksText)
  assert.ok(Array.isArray(config.hooks.PreToolUse))
  assert.doesNotMatch(hooksText, new RegExp(REPO.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  assert.match(hooksText, /\.codex\/100xprism-hooks\/run-hook\.py/)
  assert.ok(fs.existsSync(path.join(tmp, '.codex', '100xprism-hooks', 'run-hook.py')))

  const commands = config.hooks.PreToolUse
    .flatMap((entry) => entry.hooks || [])
    .map((hook) => hook.command)
  assert.ok(commands.some((cmd) => cmd === 'python3 .codex/100xprism-hooks/run-hook.py pretooluse-gate.py'))
  assert.ok(commands.some((cmd) => cmd === 'python3 .codex/100xprism-hooks/run-hook.py pretooluse-secret-scan.py'))
})

test('codex hook wrapper fails clearly when 100xprism install cannot be resolved', () => {
  const tmp = makeTmpDir()
  emitCodex(tmp)

  const wrapper = path.join(tmp, '.codex', '100xprism-hooks', 'run-hook.py')
  const r = spawnSync('python3', [wrapper, 'pretooluse-gate.py'], {
    cwd: tmp,
    env: { PATH: process.env.PATH, HOME: tmp, DEV_100X_HOME: '', HUNDRED_X_HOME: '' },
    input: '{}',
    encoding: 'utf8',
  })

  assert.equal(r.status, 2)
  assert.match(r.stderr, /could not find the 100xprism install/i)
  assert.deepEqual(r.stderr.trim().split('\n'), [
    '100xprism Codex hook could not find the 100xprism install.',
    'Set DEV_100X_HOME=/path/to/100xprism or run `100xprism install`, then retry.',
  ])
  assert.doesNotMatch(r.stderr, /\\n/)
})

test('codex hook wrapper reports version skew when install exists without requested hook', () => {
  const tmp = makeTmpDir()
  const install = path.join(tmp, 'install')
  fs.mkdirSync(path.join(install, 'hooks'), { recursive: true })
  fs.writeFileSync(path.join(install, 'hooks', 'hooks.manifest.json'), JSON.stringify({ hooks: [] }))
  fs.writeFileSync(path.join(install, 'hooks', 'pretooluse-gate.py'), 'print("unused")\n')
  emitCodex(tmp)

  const wrapper = path.join(tmp, '.codex', '100xprism-hooks', 'run-hook.py')
  const r = spawnSync('python3', [wrapper, 'pretooluse-gate.py'], {
    cwd: tmp,
    env: { PATH: process.env.PATH, HOME: tmp, DEV_100X_HOME: install, HUNDRED_X_HOME: '' },
    input: '{}',
    encoding: 'utf8',
  })

  assert.equal(r.status, 2)
  assert.match(r.stderr, /found a 100xprism install/i)
  assert.match(r.stderr, /does not provide hook pretooluse-gate\.py/i)
  assert.deepEqual(r.stderr.trim().split('\n'), [
    '100xprism Codex hook found a 100xprism install, but it does not provide hook pretooluse-gate.py.',
    'Update the generated Codex hooks or reinstall 100xprism, then retry.',
  ])
  assert.doesNotMatch(r.stderr, /\\n/)
})

test('codex hook wrapper reuses its current Python interpreter for hook execution', () => {
  const tmp = makeTmpDir()
  emitCodex(tmp)

  const wrapper = fs.readFileSync(path.join(tmp, '.codex', '100xprism-hooks', 'run-hook.py'), 'utf8')
  assert.match(wrapper, /subprocess\.run\(\[sys\.executable, str\(hook\)\]\)/)
  assert.doesNotMatch(wrapper, /subprocess\.run\(\["python3", str\(hook\)\]\)/)
  assert.match(wrapper, /allowlist is intentionally baked into generated projects/)
})

test('codex hook wrapper avoids Python 3.10-only union type syntax', () => {
  const tmp = makeTmpDir()
  emitCodex(tmp)

  const wrapper = fs.readFileSync(path.join(tmp, '.codex', '100xprism-hooks', 'run-hook.py'), 'utf8')
  assert.doesNotMatch(wrapper, /\| None/)
})

test('codex adapter preserves non-100xprism repo skills', () => {
  const tmp = makeTmpDir()
  const custom = path.join(tmp, '.agents', 'skills', 'custom-skill')
  fs.mkdirSync(custom, { recursive: true })
  fs.writeFileSync(path.join(custom, 'SKILL.md'), 'custom\n')

  emitCodex(tmp)

  assert.equal(fs.readFileSync(path.join(custom, 'SKILL.md'), 'utf8'), 'custom\n')
})
