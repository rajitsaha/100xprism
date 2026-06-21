'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const os = require('os')
const path = require('path')
const platform = require('../lib/platform')

test('home resolves to os.homedir()', () => {
  assert.equal(platform.home, os.homedir())
})

test('installDir is ~/100xprism', () => {
  assert.equal(platform.installDir, path.join(os.homedir(), '100xprism'))
})

test('claudeCommandsDir is ~/.claude/commands', () => {
  assert.equal(platform.claudeCommandsDir, path.join(os.homedir(), '.claude', 'commands'))
})

test('claudeSettingsFile is ~/.claude/settings.json', () => {
  assert.equal(platform.claudeSettingsFile, path.join(os.homedir(), '.claude', 'settings.json'))
})

test('trackedProjectsFile is ~/.100xprism/tracked-projects', () => {
  assert.equal(platform.trackedProjectsFile, path.join(os.homedir(), '.100xprism', 'tracked-projects'))
})

test('exactly one of isWindows/isMac/isLinux is true', () => {
  const flags = [platform.isWindows, platform.isMac, platform.isLinux]
  assert.equal(flags.filter(Boolean).length, 1)
})
