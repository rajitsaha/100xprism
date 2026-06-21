'use strict'

const { spawnSync } = require('child_process')
const fs = require('fs')
const path = require('path')
const { installDir } = require('./platform')
const { migrateLegacy } = require('./migrate')

const REPO_URL = 'https://github.com/rajitsaha/100xprism.git'

function bootstrap() {
  migrateLegacy()
  const gitDir = path.join(installDir, '.git')

  if (fs.existsSync(gitDir)) {
    console.log('100xprism already installed — pulling latest...')
    const result = spawnSync('git', ['-C', installDir, 'pull', '--rebase', 'origin', 'main', '--quiet'], { stdio: 'inherit' })
    if (result.status !== 0) {
      console.error('Error: git pull failed. Check your network or resolve any conflicts in ' + installDir)
      process.exit(result.status ?? 1)
    }
  } else {
    console.log('Installing 100xprism...')
    fs.mkdirSync(path.dirname(installDir), { recursive: true })
    const result = spawnSync('git', ['clone', REPO_URL, installDir, '--quiet'], { stdio: 'inherit' })
    if (result.status !== 0) {
      console.error('Error: git clone failed. Check your network and try again.')
      process.exit(result.status ?? 1)
    }
  }
}

module.exports = { bootstrap }
