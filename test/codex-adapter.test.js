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

function emitCodex(projectDir, env = {}) {
  execFileSync('python3', [MODULES_PY, 'emit-codex', projectDir], {
    cwd: REPO,
    encoding: 'utf8',
    env: { ...process.env, ...env },
  })
}

test('codex adapter emits compact AGENTS plus must-have repo skills and resolver', () => {
  const tmp = makeTmpDir()
  emitCodex(tmp)

  const agentsPath = path.join(tmp, 'AGENTS.md')
  const agents = fs.readFileSync(agentsPath, 'utf8')
  assert.ok(Buffer.byteLength(agents, 'utf8') < 8 * 1024, 'AGENTS.md should stay within the Codex router budget')
  assert.match(agents, /Full reusable workflows live in `\.agents\/skills\/<slug>\/SKILL\.md`/)
  assert.match(agents, /`\/gate` → `\$gate`/)
  assert.match(agents, /Claude Code plugins .* are not Codex plugins/)
  assert.match(agents, /\.agents\/100x-index\.md/)
  assert.doesNotMatch(agents, /## Available Skills/)

  const index = fs.readFileSync(path.join(tmp, '.agents', '100x-index.md'), 'utf8')
  assert.match(index, /100xprism Skill Index/)
  assert.match(index, /`gate` `\/gate`/)

  const skillsDir = path.join(tmp, '.agents', 'skills')
  const skillFiles = fs.readdirSync(skillsDir)
    .filter((name) => fs.existsSync(path.join(skillsDir, name, 'SKILL.md')))
  const sourceSkillCount = fs.readdirSync(path.join(REPO, 'modules'))
    .filter((name) => fs.existsSync(path.join(REPO, 'modules', name, 'SKILL.md')))
    .length
  assert.ok(skillFiles.length < sourceSkillCount)
  assert.ok(skillFiles.length < 20, `lean default should stay small, got ${skillFiles.length}`)
  assert.ok(fs.existsSync(path.join(skillsDir, 'gate', 'SKILL.md')))
  assert.ok(fs.existsSync(path.join(skillsDir, '100x-resolver', 'SKILL.md')))
  assert.ok(!fs.existsSync(path.join(skillsDir, 'copywriting', 'SKILL.md')))
  assert.ok(fs.existsSync(path.join(tmp, '.agents', '100xprism-catalog', 'copywriting', 'SKILL.md')))
})

test('codex adapter keeps hooks disabled by default', () => {
  const tmp = makeTmpDir()
  emitCodex(tmp)

  const hooksPath = path.join(tmp, '.codex', 'hooks.json')
  const hooksText = fs.readFileSync(hooksPath, 'utf8')
  const config = JSON.parse(hooksText)
  assert.deepEqual(config.hooks, {})
})

test('codex adapter emits hooks only after explicit opt-in', () => {
  const tmp = makeTmpDir()
  emitCodex(tmp, { PRISM_HOOKS: '1' })

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

test('codex adapter never overwrites a hand-written AGENTS.md', () => {
  const tmp = makeTmpDir()
  const agentsPath = path.join(tmp, 'AGENTS.md')
  fs.writeFileSync(agentsPath, '# My repo contract\n\nHand-written rules.\n')
  emitCodex(tmp)
  assert.equal(fs.readFileSync(agentsPath, 'utf8'), '# My repo contract\n\nHand-written rules.\n')
  assert.ok(fs.existsSync(path.join(tmp, '.agents', 'skills', 'gate', 'SKILL.md')))
})

test('codex adapter refreshes an AGENTS.md it generated', () => {
  const tmp = makeTmpDir()
  emitCodex(tmp)
  const agentsPath = path.join(tmp, 'AGENTS.md')
  fs.appendFileSync(agentsPath, '\nstale line\n')
  emitCodex(tmp)
  assert.doesNotMatch(fs.readFileSync(agentsPath, 'utf8'), /stale line/)
})
