'use strict'

const { spawn } = require('child_process')
const path = require('path')
const { installDir } = require('./platform')

function kickDashboard() {
  try {
    const script = path.join(installDir, 'scripts', 'token-dashboard.py')
    const p = spawn('python3', [script, '--ensure-daemon'], {
      detached: true, stdio: 'ignore'
    })
    p.unref()
  } catch (_) { /* never break install/init/update */ }
}

module.exports = { kickDashboard }
