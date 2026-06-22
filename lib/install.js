'use strict'

const { spawnSync } = require('child_process')
const path = require('path')
const { isWindows, installDir } = require('./platform')
const { bootstrap } = require('./bootstrap')
const { kickDashboard } = require('./dashboard')

function run(_args) {
  bootstrap()
  if (isWindows) {
    require('./adapters/windows').installGlobalWindows(installDir)
  } else {
    const result = spawnSync('bash', [path.join(installDir, 'install.sh')], { stdio: 'inherit' })
    if (result.status !== 0) process.exit(result.status ?? 1)
  }
  kickDashboard()
  console.log('📊 Token + value dashboard → http://127.0.0.1:8787')
}

module.exports = { run }
