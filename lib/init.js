'use strict'

const { spawnSync, spawn } = require('child_process')
const path = require('path')
const { isWindows, installDir } = require('./platform')
const { bootstrap } = require('./bootstrap')

function kickDashboard() {
  try {
    const script = path.join(installDir, 'scripts', 'token-dashboard.py')
    const p = spawn('python3', [script, '--ensure-daemon'], {
      detached: true, stdio: 'ignore'
    })
    p.unref()
  } catch (_) { /* never break install/init/update */ }
}

function run(args) {
  bootstrap()
  const projectPath = args[0] || process.cwd()
  if (isWindows) {
    require('./adapters/windows').initProjectWindows(installDir, projectPath)
  } else {
    const result = spawnSync('bash', [path.join(installDir, 'install-project.sh'), projectPath], { stdio: 'inherit' })
    if (result.status !== 0) process.exit(result.status ?? 1)
  }
  kickDashboard()
  console.log('📊 Token + value dashboard → http://127.0.0.1:8787')
}

module.exports = { run }
