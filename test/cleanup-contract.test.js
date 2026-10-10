'use strict'

// The cleanup contract: install/update remove only what 100xprism owns (including
// v1 leftovers proven by signature), and uninstall reverses everything it wrote
// while leaving user-authored skills, commands, hooks, and settings untouched.

const { test } = require('node:test')
const assert = require('node:assert/strict')
const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const REPO = path.resolve(__dirname, '..')
const MODULES_PY = path.join(REPO, 'adapters', 'lib', 'modules.py')
const UNINSTALL = path.join(REPO, 'lib', 'uninstall.js')
const AUDIT_CLI = path.join(REPO, 'bin', '100xprism.js')

function tmpHome() {
  return fs.mkdtempSync(path.join(os.tmpdir(), '100x-contract-'))
}

function run(cmd, args, home, extraEnv = {}) {
  return execFileSync(cmd, args, {
    cwd: REPO,
    encoding: 'utf8',
    env: { ...process.env, HOME: home, USERPROFILE: home, PRISM_SKILLS: '', ...extraEnv },
  })
}

function write(file, content) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, content)
}

function plantV1Leftovers(commandsDir) {
  write(path.join(commandsDir, '_lib.md'), '# _lib — Shared Workflow Conventions\n<!-- Reference only -->\n')
  write(path.join(commandsDir, 'db-engines', 'postgres.md'), '# db-engine: postgres\nbody\n')
  write(path.join(commandsDir, 'commit.md'), '# Commit — Gate → Stage → Commit\nbody\n')
}

function plantUserCommands(commandsDir) {
  // Same names as v1 files, different content: must survive.
  write(path.join(commandsDir, 'fix.md'), '# My own fix command\n')
  write(path.join(commandsDir, 'db-engines', 'mine.md'), '# my engine notes\n')
  write(path.join(commandsDir, 'mine.md'), '# mine\n')
}

function assertV1SweptUserKept(commandsDir) {
  assert.ok(!fs.existsSync(path.join(commandsDir, '_lib.md')), 'v1 _lib.md should be swept')
  assert.ok(!fs.existsSync(path.join(commandsDir, 'commit.md')), 'v1 commit.md should be swept')
  assert.ok(!fs.existsSync(path.join(commandsDir, 'db-engines', 'postgres.md')), 'v1 engine should be swept')
  assert.equal(fs.readFileSync(path.join(commandsDir, 'fix.md'), 'utf8'), '# My own fix command\n')
  assert.ok(fs.existsSync(path.join(commandsDir, 'db-engines', 'mine.md')), 'user file in db-engines kept')
  assert.ok(fs.existsSync(path.join(commandsDir, 'mine.md')))
}

test('install sweeps signature-matched v1 leftovers and keeps same-named user files', () => {
  const home = tmpHome()
  const commandsDir = path.join(home, '.claude', 'commands')
  plantV1Leftovers(commandsDir)
  plantUserCommands(commandsDir)
  run('python3', [MODULES_PY, 'emit-claude-code'], home)
  assertV1SweptUserKept(commandsDir)
})

test('Windows emitter sweeps the same v1 leftovers', () => {
  const home = tmpHome()
  const commandsDir = path.join(home, '.claude', 'commands')
  plantV1Leftovers(commandsDir)
  plantUserCommands(commandsDir)
  run('node', ['-e', `
    const w = require(${JSON.stringify(path.join(REPO, 'lib', 'adapters', 'windows.js'))})
    w.emitClaudeModules(${JSON.stringify(path.join(REPO, 'modules'))},
      ${JSON.stringify(path.join(home, '.claude', 'skills'))}, ${JSON.stringify(commandsDir)})
  `], home)
  assertV1SweptUserKept(commandsDir)
})

test('an emptied v1 db-engines directory is removed', () => {
  const home = tmpHome()
  const commandsDir = path.join(home, '.claude', 'commands')
  plantV1Leftovers(commandsDir)
  run('python3', [MODULES_PY, 'emit-claude-code'], home)
  assert.ok(!fs.existsSync(path.join(commandsDir, 'db-engines')))
})

function installEverything(home) {
  run('python3', [MODULES_PY, 'emit-claude-code'], home)
  run('python3', [MODULES_PY, 'emit-hooks'], home, { HOOK_ROUTER: '1' })
}

function plantUserState(home) {
  const settingsFile = path.join(home, '.claude', 'settings.json')
  const settings = JSON.parse(fs.readFileSync(settingsFile, 'utf8'))
  settings.model = 'opus'
  settings.hooks.PreToolUse.push({ matcher: 'Bash', hooks: [{ type: 'command', command: 'echo user-hook' }] })
  fs.writeFileSync(settingsFile, JSON.stringify(settings, null, 2))
  write(path.join(home, '.claude', 'skills', 'mine', 'SKILL.md'), '---\nname: mine\ndescription: mine\n---\n')
  write(path.join(home, '.claude', 'commands', 'mine.md'), '# mine\n')
  write(path.join(home, '.100xprism', 'value.json'), '{}\n')
}

test('uninstall reverses every user-scope artifact and keeps user state', () => {
  const home = tmpHome()
  installEverything(home)
  plantUserState(home)
  plantV1Leftovers(path.join(home, '.claude', 'commands'))

  const skillsDir = path.join(home, '.claude', 'skills')
  assert.ok(fs.existsSync(path.join(skillsDir, 'gate', '.100xprism-generated')), 'precondition: installed')
  const settingsBefore = fs.readFileSync(path.join(home, '.claude', 'settings.json'), 'utf8')
  assert.match(settingsBefore, /100xprism\/hooks\//, 'precondition: hooks registered')

  run('node', [UNINSTALL], home)

  const generated = fs.readdirSync(skillsDir)
    .filter(name => fs.existsSync(path.join(skillsDir, name, '.100xprism-generated')))
  assert.deepEqual(generated, [], 'no generated skills remain')
  assert.ok(!fs.existsSync(path.join(skillsDir, '100x-resolver')))
  assert.ok(!fs.existsSync(path.join(skillsDir, '.100xprism-manifest.json')))
  assert.ok(!fs.existsSync(path.join(home, '.claude', 'commands', '100x.md')), 'route alias removed')
  assert.ok(!fs.existsSync(path.join(home, '.claude', 'commands', '_lib.md')), 'v1 leftovers removed')
  assert.ok(!fs.existsSync(path.join(home, '.100xprism', '100xprism-catalog')), 'catalog removed')

  const settings = JSON.parse(fs.readFileSync(path.join(home, '.claude', 'settings.json'), 'utf8'))
  assert.doesNotMatch(JSON.stringify(settings), /100xprism\/hooks\//, 'every managed hook removed, all events')
  assert.equal(settings.model, 'opus')
  assert.match(JSON.stringify(settings.hooks), /echo user-hook/)

  assert.ok(fs.existsSync(path.join(skillsDir, 'mine', 'SKILL.md')))
  assert.ok(fs.existsSync(path.join(home, '.claude', 'commands', 'mine.md')))
  assert.ok(fs.existsSync(path.join(home, '.100xprism', 'value.json')), 'user data kept without --purge')
})

test('uninstall --purge also removes ~/.100xprism', () => {
  const home = tmpHome()
  installEverything(home)
  write(path.join(home, '.100xprism', 'value.json'), '{}\n')
  run('node', [UNINSTALL, '--purge'], home)
  assert.ok(!fs.existsSync(path.join(home, '.100xprism')))
})

test('uninstall --projects cleans tracked projects but keeps hand-written files', () => {
  const home = tmpHome()
  const project = fs.mkdtempSync(path.join(os.tmpdir(), '100x-contract-proj-'))
  write(path.join(project, 'AGENTS.md'), '# Hand-written contract\n')
  write(path.join(project, '.cursor', 'rules', 'mine.mdc'), '---\ndescription: mine\n---\nmine\n')
  write(path.join(project, '.agents', 'skills', 'mine', 'SKILL.md'), '---\nname: mine\n---\n')
  run('python3', [MODULES_PY, 'emit-cursor', project], home)
  run('python3', [MODULES_PY, 'emit-codex', project], home)
  write(path.join(home, '.100xprism', 'tracked-projects'), project + '\n')
  assert.ok(fs.existsSync(path.join(project, '.cursor', 'rules', 'gate.mdc')), 'precondition')

  run('node', [UNINSTALL, '--projects'], home)

  assert.ok(!fs.existsSync(path.join(project, '.cursor', 'rules', 'gate.mdc')))
  assert.ok(!fs.existsSync(path.join(project, '.cursor', '100xprism-catalog')))
  assert.ok(!fs.existsSync(path.join(project, '.agents', 'skills', 'gate')))
  assert.ok(!fs.existsSync(path.join(project, '.agents', '100xprism-catalog')))
  assert.ok(!fs.existsSync(path.join(project, '.agents', '100x-index.md')))
  assert.ok(!fs.existsSync(path.join(project, '.codex', '100xprism-hooks')))
  assert.equal(fs.readFileSync(path.join(project, 'AGENTS.md'), 'utf8'), '# Hand-written contract\n')
  assert.ok(fs.existsSync(path.join(project, '.cursor', 'rules', 'mine.mdc')))
  assert.ok(fs.existsSync(path.join(project, '.agents', 'skills', 'mine', 'SKILL.md')))
})

test('uninstall --projects removes an AGENTS.md that 100xprism generated', () => {
  const home = tmpHome()
  const project = fs.mkdtempSync(path.join(os.tmpdir(), '100x-contract-proj-'))
  run('python3', [MODULES_PY, 'emit-codex', project], home)
  write(path.join(home, '.100xprism', 'tracked-projects'), project + '\n')
  run('node', [UNINSTALL, '--projects'], home)
  assert.ok(!fs.existsSync(path.join(project, 'AGENTS.md')))
})

test('audit reports drift and audit --fix repairs it', () => {
  const home = tmpHome()
  installEverything(home)
  const settingsFile = path.join(home, '.claude', 'settings.json')
  const settings = JSON.parse(fs.readFileSync(settingsFile, 'utf8'))
  settings.hooks.PreToolUse.push({
    matcher: 'Bash',
    hooks: [{ type: 'command', command: 'python3 "/nowhere/100xprism/hooks/pretooluse-gate.py"' }],
  })
  fs.writeFileSync(settingsFile, JSON.stringify(settings, null, 2))
  plantV1Leftovers(path.join(home, '.claude', 'commands'))
  fs.rmSync(path.join(home, '.100xprism', '100xprism-catalog'), { recursive: true, force: true })

  const report = JSON.parse(run('node', [AUDIT_CLI, 'audit', '--json'], home))
  const kinds = [...new Set(report.drift.map(d => d.kind))].sort()
  assert.deepEqual(kinds, ['broken-hook', 'legacy-v1', 'missing-catalog'])

  run('node', [AUDIT_CLI, 'audit', '--fix'], home)
  const after = JSON.parse(run('node', [AUDIT_CLI, 'audit', '--json'], home))
  assert.deepEqual(after.drift, [])
  assert.doesNotMatch(fs.readFileSync(settingsFile, 'utf8'), /\/nowhere\//)
  assert.ok(fs.existsSync(path.join(home, '.100xprism', '100xprism-catalog')))
})

test('npm preuninstall drops only hooks pointing into the package and keeps skills', () => {
  const home = tmpHome()
  installEverything(home)
  const settingsFile = path.join(home, '.claude', 'settings.json')
  const settings = JSON.parse(fs.readFileSync(settingsFile, 'utf8'))
  settings.hooks.PreToolUse.push({
    matcher: 'Bash',
    hooks: [{ type: 'command', command: 'python3 "/elsewhere/100xprism/hooks/permission-router.py"' }],
  })
  fs.writeFileSync(settingsFile, JSON.stringify(settings, null, 2))

  run('node', [UNINSTALL, '--remove-live-links'], home, { npm_lifecycle_event: 'preuninstall' })

  const after = fs.readFileSync(settingsFile, 'utf8')
  assert.ok(!after.includes(path.join(REPO, 'hooks')), 'hooks into this package removed')
  assert.match(after, /\/elsewhere\/100xprism\/hooks\//, 'hooks from another install kept')
  assert.ok(fs.existsSync(path.join(home, '.claude', 'skills', 'gate', 'SKILL.md')), 'skills kept')
})

test('npm preuninstall (an upgrade) leaves packs, SessionStart, and shell startup alone', () => {
  const home = tmpHome()
  installEverything(home)
  const claude = path.join(home, '.claude')
  const settingsFile = path.join(claude, 'settings.json')
  const settings = JSON.parse(fs.readFileSync(settingsFile, 'utf8'))
  settings.enabledPlugins = { 'databricks-core@databricks-agent-skills': true }
  settings.extraKnownMarketplaces = { 'databricks-agent-skills': {} }
  settings.hooks.SessionStart = [{ hooks: [{ type: 'command', command: 'bash ~/100xprism/shell/check-update.sh' }] }]
  fs.writeFileSync(settingsFile, JSON.stringify(settings, null, 2))
  const packState = JSON.stringify({ schema: 1, packs: { databricks: { platforms: { 'claude-code': 'installed' },
    owned: { plugins: ['databricks-core@databricks-agent-skills'], marketplace: 'databricks-agent-skills' }, uninstall: {} } } })
  write(path.join(claude, '.100xprism-packs.json'), packState)
  const rc = '# 100xPrism aliases\nsource ~/100xprism/shell/aliases.sh\n'
  write(path.join(home, '.zshrc'), rc)

  run('node', [UNINSTALL, '--remove-live-links'], home, { npm_lifecycle_event: 'preuninstall' })

  const after = JSON.parse(fs.readFileSync(settingsFile, 'utf8'))
  assert.equal(after.enabledPlugins['databricks-core@databricks-agent-skills'], true, 'managed pack still enabled')
  assert.ok('databricks-agent-skills' in after.extraKnownMarketplaces, 'pack marketplace kept')
  assert.match(JSON.stringify(after.hooks.SessionStart), /check-update\.sh/, 'SessionStart hook kept')
  assert.equal(fs.readFileSync(path.join(claude, '.100xprism-packs.json'), 'utf8'), packState, 'pack ownership kept')
  assert.equal(fs.readFileSync(path.join(home, '.zshrc'), 'utf8'), rc, 'shell startup kept')
})

test('uninstall --projects strips our Codex hooks and keeps user hook groups', () => {
  const home = tmpHome()
  const onlyOurs = fs.mkdtempSync(path.join(os.tmpdir(), '100x-contract-proj-'))
  const mixed = fs.mkdtempSync(path.join(os.tmpdir(), '100x-contract-proj-'))
  for (const project of [onlyOurs, mixed]) run('python3', [MODULES_PY, 'emit-codex', project], home, { PRISM_HOOKS: '1' })
  const mixedHooks = path.join(mixed, '.codex', 'hooks.json')
  const data = JSON.parse(fs.readFileSync(mixedHooks, 'utf8'))
  assert.match(JSON.stringify(data), /100xprism-hooks\/run-hook\.py/, 'precondition: our hooks emitted')
  data.hooks.Stop = [{ hooks: [{ type: 'command', command: 'echo user-stop' }] }]
  fs.writeFileSync(mixedHooks, JSON.stringify(data, null, 2))
  write(path.join(home, '.100xprism', 'tracked-projects'), `${onlyOurs}\n${mixed}\n`)

  run('node', [UNINSTALL, '--projects'], home)

  assert.ok(!fs.existsSync(path.join(onlyOurs, '.codex', 'hooks.json')), 'hooks.json with only our groups removed')
  const kept = fs.readFileSync(mixedHooks, 'utf8')
  assert.doesNotMatch(kept, /100xprism-hooks/, 'no hook left pointing at the deleted runner')
  assert.match(kept, /echo user-stop/, 'user hook group kept')
})

test('uninstall --projects keeps an AGENTS.md that only mentions the marker', () => {
  const home = tmpHome()
  const project = fs.mkdtempSync(path.join(os.tmpdir(), '100x-contract-proj-'))
  const text = '# Contract\n\nFiles headed `# Generated by 100xprism` are regenerated.\n'
  write(path.join(project, 'AGENTS.md'), text)
  write(path.join(home, '.100xprism', 'tracked-projects'), project + '\n')
  run('node', [UNINSTALL, '--projects'], home)
  assert.equal(fs.readFileSync(path.join(project, 'AGENTS.md'), 'utf8'), text)
})

test('uninstall keeps removed-artifacts (the recovery copy) unless --purge', () => {
  const home = tmpHome()
  installEverything(home)
  write(path.join(home, '.100xprism', 'removed-artifacts', 'x', 'GEMINI.md'), 'backup\n')
  run('node', [UNINSTALL], home)
  assert.ok(fs.existsSync(path.join(home, '.100xprism', 'removed-artifacts', 'x', 'GEMINI.md')))
})

test('audit fixDrift reports a failed catalog rebuild instead of claiming success', () => {
  const { fixDrift } = require('../lib/audit')
  const home = tmpHome()
  const bogusRepo = fs.mkdtempSync(path.join(os.tmpdir(), '100x-contract-norepo-'))
  const result = fixDrift(home, bogusRepo, [{ kind: 'missing-catalog', detail: 'x' }])
  assert.equal(result.ok, false)
  assert.ok(result.error)
  assert.deepEqual(fixDrift(home, bogusRepo, []), { ok: true })
})

test('JS cleanup markers match the Python adapter', () => {
  const c = require(path.join(REPO, 'lib', 'cleanup.js'))
  const py = JSON.parse(execFileSync('python3', ['-c', `
import json, sys; sys.path.insert(0, ${JSON.stringify(path.join(REPO, 'adapters', 'lib'))})
import modules as m
print(json.dumps([m.GENERATED_MARKER, m.ALIAS_MARKER, m.MANIFEST_NAME, m.CURSOR_MARKER,
                  m.CODEX_AGENTS_MARKER, m.RESOLVER_SLUG, m.CATALOG_DIRNAME]))
`], { encoding: 'utf8' }))
  assert.deepEqual([c.GENERATED_MARKER, c.ALIAS_MARKER, c.MANIFEST_NAME, c.CURSOR_MARKER,
    c.CODEX_AGENTS_MARKER, c.RESOLVER_SLUG, c.CATALOG_DIRNAME], py)
})
