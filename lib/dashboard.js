'use strict'

const fs = require('fs')
const os = require('os')
const { spawn, spawnSync } = require('child_process')
const path = require('path')
const { installDir } = require('./platform')
const { createLifecycle } = require('./token-economics')

const PID_FILE = path.join(os.homedir(), '.100xprism', 'token-dashboard.pid')
const DASHBOARD_URL = 'http://127.0.0.1:8787'

function dashboardScript() {
  return path.join(installDir, 'scripts', 'token-dashboard.py')
}

function dashboardStartCommand() {
  return '100xprism tokens'
}

function dashboardStartedMessage() {
  return `📊 AI economics dashboard → ${DASHBOARD_URL}`
}

function dashboardStartInstructions() {
  return [
    '📊 AI economics dashboard is not started by default.',
    `   Start and open it with: ${dashboardStartCommand()}`,
  ].join('\n')
}

function kickDashboard() {
  if (process.platform === 'darwin') {
    try { startOwnedDashboard(['--no-open']); return true } catch (e) { console.warn(e.message); return false }
  }
  if (process.env.PRISM_NO_DASHBOARD) return false
  try {
    const script = dashboardScript()
    const p = spawn('python3', [script, '--ensure-daemon'], {
      detached: true, stdio: 'ignore'
    })
    p.unref()
    return true
  } catch (_) {
    return false
  }
}

function commandForPid(pid) {
  const result = spawnSync('ps', ['-p', String(pid), '-o', 'command='], { encoding: 'utf8' })
  return result.status === 0 ? result.stdout.trim() : ''
}

function readOwnedDashboardPid() {
  let record
  try {
    record = JSON.parse(fs.readFileSync(PID_FILE, 'utf8'))
  } catch (_) {
    return null
  }

  const pid = Number(record.pid)
  if (!Number.isInteger(pid) || pid <= 0 || pid === process.pid) return null

  const expected = path.resolve(record.script || dashboardScript())
  const allowed = [path.resolve(dashboardScript()), path.resolve(__dirname, '..', 'scripts', 'token-dashboard.py')]
  if (!allowed.includes(expected)) return null
  const command = commandForPid(pid)
  if (!command || !command.includes('token-dashboard.py')) return null
  if (!command.includes(expected) && !command.includes(path.resolve(dashboardScript()))) return null

  return pid
}

function sleep(milliseconds) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds)
}

function waitForExit(pid, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      process.kill(pid, 0)
    } catch (err) {
      if (err && err.code === 'ESRCH') return true
      throw err
    }
    sleep(25)
  }
  return false
}

function stopDashboard() {
  const lifecycle = createLifecycle()
  if (fs.existsSync(lifecycle.manifest)) {
    const record = readRuntime(lifecycle)
    if (record) return stopOwnedRuntime(lifecycle, record)
  }
  const pid = readOwnedDashboardPid()
  if (!pid) return { stopped: false, reason: 'not running' }

  try {
    process.kill(pid, 'SIGTERM')
  } catch (err) {
    if (err && err.code !== 'ESRCH') throw err
  }

  let exited = waitForExit(pid, 1500)
  if (!exited && readOwnedDashboardPid() === pid) {
    try {
      process.kill(pid, 'SIGKILL')
      exited = waitForExit(pid, 500)
    } catch (err) {
      if (err && err.code !== 'ESRCH') throw err
      exited = true
    }
  }

  try {
    fs.rmSync(PID_FILE, { force: true })
  } catch (_) {
    // best effort
  }

  return { stopped: true, pid, exited }
}

// Credential paths and server commands are accepted only through the owned manifest.
function readRuntime(lifecycle = createLifecycle()) {
  if (!fs.existsSync(lifecycle.runtime)) return null
  const m = lifecycle.readManifest()
  if (!m.artifacts.some(a => a.path === lifecycle.runtime && a.kind === 'file')) throw new Error('Unowned dashboard credential')
  const stat = fs.lstatSync(lifecycle.runtime)
  if (!stat.isFile() || (stat.mode & 0o077)) throw new Error('Dashboard credential must be a private regular file')
  const record = JSON.parse(fs.readFileSync(lifecycle.runtime, 'utf8'))
  if (!Number.isInteger(record.pid) || record.pid <= 0 || record.pid === process.pid ||
      !Number.isInteger(record.port) || record.port < 1 || record.port > 65535 ||
      typeof record.token !== 'string' || !/^[A-Za-z0-9_-]{32,128}$/.test(record.token) ||
      typeof record.script !== 'string' || !m.artifacts.some(a => a.path === record.script && a.kind === 'file') ||
      path.basename(record.script) !== 'token-dashboard.py') throw new Error('Invalid dashboard credential record')
  const command = commandForPid(record.pid)
  if (!command) {
    try { process.kill(record.pid, 0) } catch (error) {
      if (error.code === 'ESRCH') { fs.unlinkSync(lifecycle.runtime); return null }
      throw error
    }
    return null
  }
  if (!command.includes(record.script) || /(?:^|\s)-c(?:\s|$)/.test(command)) { fs.unlinkSync(lifecycle.runtime); return null } // PID reused; remove only its owned stale credential.
  return record
}

function runtimeUrl(record) {
  return `http://127.0.0.1:${record.port}/#token=${encodeURIComponent(record.token)}`
}

function stopOwnedRuntime(lifecycle, record) {
  process.kill(record.pid, 'SIGTERM')
  let exited = waitForExit(record.pid, 1500)
  const command = commandForPid(record.pid)
  if (!exited && command.includes(record.script) && !/(?:^|\s)-c(?:\s|$)/.test(command)) {
    process.kill(record.pid, 'SIGKILL')
    exited = waitForExit(record.pid, 500)
  }
  if (exited && fs.existsSync(lifecycle.runtime)) {
    const latest=JSON.parse(fs.readFileSync(lifecycle.runtime,'utf8'))
    if (latest.pid===record.pid && latest.token===record.token) fs.unlinkSync(lifecycle.runtime)
  }
  if (!exited) throw new Error('Dashboard did not stop; refusing maintenance while a reader is active')
  return {stopped:true,pid:record.pid,exited}
}

function startOwnedDashboard(args = []) {
  const lifecycle = createLifecycle({stopDashboard})
  if (!lifecycle.ownedAgent()) lifecycle.install(fs.existsSync(path.join(installDir,'scripts','token-collector.py')) ? installDir : path.resolve(__dirname,'..'))
  lifecycle.ensureLoaded(); lifecycle.kickstart()
  const existing = readRuntime(lifecycle)
  if (existing) {
    if (!args.includes('--no-open')) spawnSync('open', [runtimeUrl(existing)], {stdio:'ignore'})
    console.log(`📊 AI economics dashboard → http://127.0.0.1:${existing.port}`)
    return existing
  }
  const generation = lifecycle.active()
  lifecycle.register(lifecycle.runtime)
  const command = [path.join(generation, 'scripts', 'token-dashboard.py'), '--ensure-daemon',
    '--db', path.join(generation, 'token-economics.sqlite3'), '--root', lifecycle.root, '--no-open', ...args]
  const result = spawnSync(lifecycle.python(), ['-B', ...command], {stdio:'inherit'})
  if (result.status !== 0) throw new Error('Dashboard startup failed')
  const record=readRuntime(lifecycle)
  if (record && !args.includes('--no-open')) spawnSync('open',[runtimeUrl(record)],{stdio:'ignore'})
  return record
}

module.exports = {
  kickDashboard,
  startOwnedDashboard,
  readRuntime,
  runtimeUrl,
  stopDashboard,
  PID_FILE,
  DASHBOARD_URL,
  dashboardStartCommand,
  dashboardStartedMessage,
  dashboardStartInstructions,
}
