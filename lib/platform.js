'use strict'

const os = require('os')
const path = require('path')

const isWindows = process.platform === 'win32'
const isMac = process.platform === 'darwin'
const isLinux = process.platform === 'linux'
const home = os.homedir()

const installDir = path.join(home, '100xprism')
const claudeDir = path.join(home, '.claude')
const claudeCommandsDir = path.join(claudeDir, 'commands')
const claudeSettingsFile = path.join(claudeDir, 'settings.json')
const trackedProjectsFile = path.join(home, '.100xprism', 'tracked-projects')

module.exports = {
  isWindows, isMac, isLinux,
  home, installDir,
  claudeDir, claudeCommandsDir, claudeSettingsFile,
  trackedProjectsFile,
}
