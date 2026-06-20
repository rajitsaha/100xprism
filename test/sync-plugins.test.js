'use strict'

// Verifies adapters/lib/sync_plugins.py reconciles enabledPlugins with
// plugins.json: adds new, removes 100x-dev-managed plugins dropped from
// plugins.json, never flips a value the user set, and never removes a plugin
// the user enabled themselves.

const { test } = require('node:test')
const assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const REPO = path.resolve(__dirname, '..')
const SCRIPT = path.join(REPO, 'adapters', 'lib', 'sync_plugins.py')

function setup(settings) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), '100x-sp-'))
  const settingsFile = path.join(dir, 'settings.json')
  const pluginsFile = path.join(dir, 'plugins.json')
  fs.writeFileSync(settingsFile, JSON.stringify(settings))
  return { dir, settingsFile, pluginsFile }
}

function sync(ctx, plugins) {
  fs.writeFileSync(ctx.pluginsFile, JSON.stringify({ plugins, extraKnownMarketplaces: {} }))
  const r = spawnSync('python3', [SCRIPT, '--settings', ctx.settingsFile, '--plugins', ctx.pluginsFile], { encoding: 'utf8' })
  assert.equal(r.status, 0, r.stderr)
  return JSON.parse(fs.readFileSync(ctx.settingsFile, 'utf8')).enabledPlugins
}

test('adds newly-declared plugins, leaves user values untouched', () => {
  const ctx = setup({ enabledPlugins: { 'github@x': true, 'mem@y': false } })
  const enabled = sync(ctx, ['github@x', 'foo@z'])
  assert.equal(enabled['foo@z'], true, 'new plugin added')
  assert.equal(enabled['github@x'], true)
  assert.equal(enabled['mem@y'], false, 'user-disabled plugin not flipped')
})

test('first run removes nothing; a later drop removes the managed plugin', () => {
  const ctx = setup({ enabledPlugins: {} })
  sync(ctx, ['github@x', 'foo@z'])            // first run: seed + add
  let enabled = JSON.parse(fs.readFileSync(ctx.settingsFile, 'utf8')).enabledPlugins
  assert.equal(enabled['foo@z'], true)

  enabled = sync(ctx, ['github@x'])           // foo dropped from plugins.json
  assert.equal('foo@z' in enabled, false, 'dropped managed plugin removed')
  assert.equal(enabled['github@x'], true, 'still-declared plugin kept')
})

test('never removes a plugin the user enabled themselves', () => {
  // user-only plugin is enabled but never appears in plugins.json
  const ctx = setup({ enabledPlugins: { 'user-only@m': true } })
  sync(ctx, ['github@x'])                      // seed run
  const enabled = sync(ctx, ['github@x'])     // second run
  assert.equal(enabled['user-only@m'], true, 'user-managed plugin preserved')
})
