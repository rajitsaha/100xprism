'use strict'

const { spawnSync } = require('child_process')
const path = require('path')
const fs = require('fs')
const { isWindows, installDir } = require('./platform')
const { bootstrap } = require('./bootstrap')
const { kickDashboard, dashboardStartedMessage, dashboardStartInstructions } = require('./dashboard')
const { preinstallCleanup } = require('./uninstall')

function run(args = []) {
  const startDashboard = args.includes('--dashboard')
  const economics = require('./token-economics').createLifecycle({stopDashboard: require('./dashboard').stopDashboard})
  if (fs.existsSync(path.join(installDir, '.git'))) economics.assertClean(installDir)
  preinstallCleanup()
  bootstrap()
  if (isWindows) {
    require('./adapters/windows').installGlobalWindows(installDir)
  } else {
    const env = { ...process.env, PRISM_PREINSTALL_CLEANUP_DONE: '1', PRISM_TOKEN_LIFECYCLE_DONE: '1' }
    const result = spawnSync('bash', [path.join(installDir, 'install.sh')], { stdio: 'inherit', env })
    if (result.status !== 0) process.exit(result.status ?? 1)
  }
  if (economics.supported) economics.install(installDir)
  else console.log(require('./token-economics').BANNER)
  if (startDashboard) {
    if (kickDashboard()) {
      console.log(dashboardStartedMessage())
    } else if (isWindows) {
      console.log(dashboardStartInstructions())
    }
  } else if (isWindows) {
    // Unix installs run install.sh, which already prints the default dashboard
    // start command. Windows does not run install.sh, so print it here.
    console.log(dashboardStartInstructions())
  }
}

module.exports = { run }
