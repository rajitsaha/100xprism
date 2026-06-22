'use strict'

const { spawnSync } = require('child_process')
const fs = require('fs')
const path = require('path')
const { isWindows, installDir } = require('./platform')
const { migrateLegacy } = require('./migrate')
const { kickDashboard } = require('./dashboard')

function run(args) {
  const checkOnly = args.includes('--check-only')

  migrateLegacy()

  if (!fs.existsSync(installDir)) {
    console.error('100xprism is not installed. Run: 100xprism install')
    process.exit(1)
  }

  if (isWindows) {
    require('./adapters/windows').updateWindows(installDir, checkOnly)
  } else {
    const script = path.join(installDir, 'update.sh')
    const scriptArgs = checkOnly ? ['--check-only'] : []
    const result = spawnSync('bash', [script, ...scriptArgs], { stdio: 'inherit' })
    if (result.status !== 0) process.exit(result.status ?? 1)
  }

  if (!checkOnly) {
    kickDashboard()
    console.log('📊 Token + value dashboard → http://127.0.0.1:8787')
  }
}

module.exports = { run }
