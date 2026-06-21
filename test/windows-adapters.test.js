'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('fs')
const os = require('os')
const path = require('path')
const {
  emitClaudeModules,
  scaffoldClaudeMd,
  mergePluginsJson,
  generateCombinedConfig,
  addTrackedProject,
} = require('../lib/adapters/windows')

function makeTmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), '100x-test-'))
}

// Build a fake modules/ dir: each entry is { slug, frontmatter, body }.
function fakeModules(dir, mods) {
  for (const m of mods) {
    const md = path.join(dir, m.slug)
    fs.mkdirSync(md, { recursive: true })
    const fm = Object.entries(m.fm || {}).map(([k, v]) => `${k}: ${v}`).join('\n')
    fs.writeFileSync(path.join(md, 'SKILL.md'), `---\n${fm}\n---\n\n${m.body || 'body'}\n`)
  }
}

test('emitClaudeModules writes skills + slash aliases from modules/', () => {
  const modulesDir = makeTmpDir(), skills = makeTmpDir(), commands = makeTmpDir()
  fakeModules(modulesDir, [
    { slug: 'gate', fm: { name: 'gate', description: 'Quality gate.', slash_command: '/gate' } },
    { slug: 'copywriting', fm: { name: 'copywriting', description: 'Write copy.' } }, // no slash
    { slug: '_lib', fm: {}, body: '' }, // shared-ref dir — but has SKILL.md here; skip via missing? keep simple
  ])
  // _lib in the real repo has no SKILL.md; emulate that:
  fs.rmSync(path.join(modulesDir, '_lib', 'SKILL.md'))

  const r = emitClaudeModules(modulesDir, skills, commands)
  assert.equal(r.skills, 2, 'two real modules emitted, _lib skipped')
  assert.ok(fs.existsSync(path.join(skills, 'gate', 'SKILL.md')))
  assert.ok(fs.existsSync(path.join(skills, 'gate', '.100xprism-generated')), 'marker written')
  assert.ok(fs.existsSync(path.join(commands, 'gate.md')), 'slash alias written')
  assert.ok(!fs.existsSync(path.join(commands, 'copywriting.md')), 'no alias without slash_command')
  const manifest = JSON.parse(fs.readFileSync(path.join(skills, '.100xprism-manifest.json'), 'utf8'))
  assert.deepEqual(manifest.skills, ['copywriting', 'gate'])
  assert.deepEqual(manifest.commands, ['gate'])
})

test('emitClaudeModules prunes removed modules but keeps user-authored skills/commands', () => {
  const modulesDir = makeTmpDir(), skills = makeTmpDir(), commands = makeTmpDir()
  fakeModules(modulesDir, [{ slug: 'gate', fm: { name: 'gate', description: 'Quality gate.', slash_command: '/gate' } }])

  // Pre-existing: a merged-away module (in REMOVED_MODULES, no marker), a user skill, a user command.
  fs.mkdirSync(path.join(skills, 'systems-architect'))
  fs.writeFileSync(path.join(skills, 'systems-architect', 'SKILL.md'), 'old')
  fs.mkdirSync(path.join(skills, 'my-skill'))
  fs.writeFileSync(path.join(skills, 'my-skill', 'SKILL.md'), 'mine')
  fs.writeFileSync(path.join(commands, 'my-cmd.md'), 'mine')

  const r = emitClaudeModules(modulesDir, skills, commands)
  assert.ok(r.prunedSkills >= 1)
  assert.ok(!fs.existsSync(path.join(skills, 'systems-architect')), 'removed module pruned')
  assert.ok(fs.existsSync(path.join(skills, 'my-skill')), 'user skill kept')
  assert.ok(fs.existsSync(path.join(commands, 'my-cmd.md')), 'user command kept')

  // A future-removed module tracked only via manifest/marker is pruned on re-run.
  fs.mkdirSync(path.join(skills, 'ghost'))
  fs.writeFileSync(path.join(skills, 'ghost', 'SKILL.md'), 'x')
  fs.writeFileSync(path.join(skills, 'ghost', '.100xprism-generated'), 'gen')
  emitClaudeModules(modulesDir, skills, commands)
  assert.ok(!fs.existsSync(path.join(skills, 'ghost')), 'marker-tagged orphan pruned')
})

test('scaffoldClaudeMd writes CLAUDE.md with project name', () => {
  const projectDir = makeTmpDir()
  scaffoldClaudeMd(projectDir)
  const content = fs.readFileSync(path.join(projectDir, 'CLAUDE.md'), 'utf8')
  assert.ok(content.includes(path.basename(projectDir)))
  assert.ok(content.includes('## Database'))
  assert.ok(content.includes('## Rules'))
})

test('scaffoldClaudeMd skips if CLAUDE.md already exists', () => {
  const projectDir = makeTmpDir()
  fs.writeFileSync(path.join(projectDir, 'CLAUDE.md'), 'existing')
  scaffoldClaudeMd(projectDir)
  assert.equal(fs.readFileSync(path.join(projectDir, 'CLAUDE.md'), 'utf8'), 'existing')
})

test('scaffoldClaudeMd skips if .cursorrules already exists', () => {
  const projectDir = makeTmpDir()
  fs.writeFileSync(path.join(projectDir, '.cursorrules'), 'existing')
  scaffoldClaudeMd(projectDir)
  assert.ok(!fs.existsSync(path.join(projectDir, 'CLAUDE.md')))
})

test('mergePluginsJson adds plugins to settings.json', () => {
  const settingsFile = path.join(makeTmpDir(), 'settings.json')
  const pluginsFile = path.join(makeTmpDir(), 'plugins.json')
  fs.writeFileSync(settingsFile, JSON.stringify({ enabledPlugins: {} }))
  fs.writeFileSync(pluginsFile, JSON.stringify({ plugins: ['plugin-a', 'plugin-b'] }))
  mergePluginsJson(pluginsFile, settingsFile)
  const settings = JSON.parse(fs.readFileSync(settingsFile, 'utf8'))
  assert.equal(settings.enabledPlugins['plugin-a'], true)
  assert.equal(settings.enabledPlugins['plugin-b'], true)
})

test('mergePluginsJson is idempotent', () => {
  const settingsFile = path.join(makeTmpDir(), 'settings.json')
  const pluginsFile = path.join(makeTmpDir(), 'plugins.json')
  fs.writeFileSync(settingsFile, JSON.stringify({ enabledPlugins: { 'plugin-a': true } }))
  fs.writeFileSync(pluginsFile, JSON.stringify({ plugins: ['plugin-a'] }))
  mergePluginsJson(pluginsFile, settingsFile)
  const settings = JSON.parse(fs.readFileSync(settingsFile, 'utf8'))
  assert.equal(Object.keys(settings.enabledPlugins).length, 1)
})

test('mergePluginsJson removes a dropped managed plugin but keeps user plugins', () => {
  const settingsFile = path.join(makeTmpDir(), 'settings.json')
  const pluginsFile = path.join(makeTmpDir(), 'plugins.json')
  fs.writeFileSync(settingsFile, JSON.stringify({ enabledPlugins: { 'user-only': true } }))

  // First run: declare a + b (seed managed set, add both, remove nothing).
  fs.writeFileSync(pluginsFile, JSON.stringify({ plugins: ['plugin-a', 'plugin-b'] }))
  mergePluginsJson(pluginsFile, settingsFile)

  // Second run: drop plugin-b from plugins.json -> it should be removed.
  fs.writeFileSync(pluginsFile, JSON.stringify({ plugins: ['plugin-a'] }))
  mergePluginsJson(pluginsFile, settingsFile)

  const enabled = JSON.parse(fs.readFileSync(settingsFile, 'utf8')).enabledPlugins
  assert.equal('plugin-b' in enabled, false, 'dropped managed plugin removed')
  assert.equal(enabled['plugin-a'], true, 'still-declared plugin kept')
  assert.equal(enabled['user-only'], true, 'user-managed plugin preserved')
})

test('generateCombinedConfig inlines core module bodies and indexes on-demand ones', () => {
  const modulesDir = makeTmpDir()
  fakeModules(modulesDir, [
    { slug: 'gate', fm: { name: 'gate', category: 'quality', tier: 'core', slash_command: '/gate', description: 'Quality gate.' }, body: 'GATE BODY' },
    { slug: 'copywriting', fm: { name: 'copywriting', category: 'marketing', tier: 'on-demand', description: 'Write copy.' }, body: 'COPY BODY' },
  ])
  const result = generateCombinedConfig(modulesDir)
  assert.ok(result.includes('100x Dev — Modules'), 'has header')
  assert.ok(result.includes('GATE BODY'), 'core body inlined')
  assert.ok(!result.includes('COPY BODY'), 'on-demand body not inlined')
  assert.ok(result.includes('`copywriting`'), 'on-demand module indexed')
})

test('addTrackedProject writes path to file', () => {
  const trackedFile = path.join(makeTmpDir(), 'tracked-projects')
  addTrackedProject('/some/project', trackedFile)
  assert.ok(fs.readFileSync(trackedFile, 'utf8').includes('/some/project'))
})

test('addTrackedProject is idempotent', () => {
  const trackedFile = path.join(makeTmpDir(), 'tracked-projects')
  addTrackedProject('/some/project', trackedFile)
  addTrackedProject('/some/project', trackedFile)
  const lines = fs.readFileSync(trackedFile, 'utf8').trim().split('\n')
  assert.equal(lines.filter(l => l === '/some/project').length, 1)
})
