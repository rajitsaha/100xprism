'use strict'

const { test } = require('node:test')
const assert = require('node:assert')
const fs = require('fs')
const os = require('os')
const path = require('path')
const { migrateLegacy } = require('../lib/migrate')

function tmpHome() {
  return fs.mkdtempSync(path.join(os.tmpdir(), '100xprism-migrate-'))
}

function paths(home) {
  return {
    home,
    legacyInstall: path.join(home, '100x-dev'),
    newInstall: path.join(home, '100xprism'),
    legacyConfig: path.join(home, '.100x-dev'),
    newConfig: path.join(home, '.100xprism'),
    runGit: false,
    log: () => {},
  }
}

test('moves legacy install + config dirs to the new names', () => {
  const home = tmpHome()
  const p = paths(home)
  fs.mkdirSync(p.legacyInstall)
  fs.writeFileSync(path.join(p.legacyInstall, 'VERSION'), '2.3.1\n')
  fs.mkdirSync(p.legacyConfig)
  fs.writeFileSync(path.join(p.legacyConfig, 'tracked-projects'), '/some/proj\n')

  const actions = migrateLegacy(p)

  assert.ok(fs.existsSync(p.newInstall), 'install dir moved')
  assert.ok(!fs.existsSync(p.legacyInstall), 'legacy install dir gone')
  assert.equal(fs.readFileSync(path.join(p.newInstall, 'VERSION'), 'utf8'), '2.3.1\n', 'contents preserved')
  assert.ok(fs.existsSync(p.newConfig), 'config dir moved')
  assert.equal(fs.readFileSync(path.join(p.newConfig, 'tracked-projects'), 'utf8'), '/some/proj\n')
  assert.equal(actions.length, 2)
})

test('is a no-op when nothing legacy exists', () => {
  const home = tmpHome()
  const actions = migrateLegacy(paths(home))
  assert.deepEqual(actions, [])
})

test('never overwrites an existing new-name dir', () => {
  const home = tmpHome()
  const p = paths(home)
  fs.mkdirSync(p.legacyInstall)
  fs.writeFileSync(path.join(p.legacyInstall, 'VERSION'), 'OLD\n')
  fs.mkdirSync(p.newInstall)
  fs.writeFileSync(path.join(p.newInstall, 'VERSION'), 'NEW\n')

  migrateLegacy(p)

  assert.equal(fs.readFileSync(path.join(p.newInstall, 'VERSION'), 'utf8'), 'NEW\n', 'new dir untouched')
  assert.ok(fs.existsSync(p.legacyInstall), 'legacy dir left in place, not destroyed')
})

test('is idempotent across repeated runs', () => {
  const home = tmpHome()
  const p = paths(home)
  fs.mkdirSync(p.legacyInstall)
  assert.equal(migrateLegacy(p).length, 1)
  assert.equal(migrateLegacy(p).length, 0, 'second run does nothing')
})
