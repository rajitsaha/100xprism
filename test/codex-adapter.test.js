'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const { execFileSync } = require('node:child_process')
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
  assert.equal(skillFiles.length, 68)
  assert.ok(fs.existsSync(path.join(skillsDir, 'gate', 'SKILL.md')))
  assert.ok(fs.existsSync(path.join(skillsDir, 'copywriting', 'SKILL.md')))
})

test('codex adapter emits hooks.json for Codex hook review flow', () => {
  const tmp = makeTmpDir()
  emitCodex(tmp)

  const hooksPath = path.join(tmp, '.codex', 'hooks.json')
  const config = JSON.parse(fs.readFileSync(hooksPath, 'utf8'))
  assert.ok(Array.isArray(config.hooks.PreToolUse))

  const commands = config.hooks.PreToolUse
    .flatMap((entry) => entry.hooks || [])
    .map((hook) => hook.command)
  assert.ok(commands.some((cmd) => cmd.includes('pretooluse-gate.py')))
  assert.ok(commands.some((cmd) => cmd.includes('pretooluse-secret-scan.py')))
})

