'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('fs')
const os = require('os')
const path = require('path')
const {
  copyWorkflowsToClaudeCommands,
  scaffoldClaudeMd,
  mergePluginsJson,
  generateCombinedWorkflows,
  addTrackedProject,
} = require('../lib/adapters/windows')

function makeTmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), '100x-test-'))
}

test('copyWorkflowsToClaudeCommands copies .md files and appends $ARGUMENTS', () => {
  const src = makeTmpDir()
  const dst = makeTmpDir()
  fs.writeFileSync(path.join(src, 'gate.md'), '# Gate\ncontent')
  copyWorkflowsToClaudeCommands(src, dst)
  const content = fs.readFileSync(path.join(dst, 'gate.md'), 'utf8')
  assert.ok(content.includes('# Gate'))
  assert.ok(content.includes('$ARGUMENTS'))
})

test('copyWorkflowsToClaudeCommands copies db-engines subdir', () => {
  const src = makeTmpDir()
  const dst = makeTmpDir()
  const dbDir = path.join(src, 'db-engines')
  fs.mkdirSync(dbDir)
  fs.writeFileSync(path.join(dbDir, 'postgres.md'), '# Postgres')
  copyWorkflowsToClaudeCommands(src, dst)
  assert.ok(fs.existsSync(path.join(dst, 'db-engines', 'postgres.md')))
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

test('generateCombinedWorkflows concatenates gate before commit', () => {
  const workflowsDir = makeTmpDir()
  fs.writeFileSync(path.join(workflowsDir, 'gate.md'), '# Gate')
  fs.writeFileSync(path.join(workflowsDir, 'commit.md'), '# Commit')
  const result = generateCombinedWorkflows(workflowsDir)
  assert.ok(result.indexOf('# Gate') < result.indexOf('# Commit'))
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
