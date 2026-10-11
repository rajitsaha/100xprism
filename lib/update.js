'use strict'

const { spawnSync } = require('child_process')
const fs = require('fs')
const path = require('path')
const { isWindows, installDir } = require('./platform')
const { migrateLegacy } = require('./migrate')
const { kickDashboard, dashboardStartedMessage, dashboardStartInstructions } = require('./dashboard')

function run(args) {
  const checkOnly = args.includes('--check-only')
  const startDashboard = args.includes('--dashboard')

  const economics = require('./token-economics').createLifecycle()
  if (!checkOnly && fs.existsSync(path.join(installDir, '.git'))) economics.assertClean(installDir)
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
    // update.sh exits non-zero when per-project reconciliation was incomplete,
    // even though the global update itself succeeded. Record that for the caller
    // via exitCode rather than exiting here, so `--dashboard` still starts —
    // a project-level failure should not withhold the rest of the command.
    if (result.status !== 0) process.exitCode = result.status ?? 1
  }

  if (!checkOnly && !args.includes('--no-slim')) applyOneTimeSlim()

  if (!checkOnly && startDashboard) {
    if (kickDashboard()) {
      console.log(dashboardStartedMessage())
    } else {
      console.log(dashboardStartInstructions())
    }
  }
}

// Move an install onto the routed skill index exactly once, the first time it
// updates into a version that has one. Skipped forever after, so a user who
// later chooses a mode (including "all") never has that choice overwritten by a
// future update — and skipped entirely if they already have a preference.
function applyOneTimeSlim() {
  const os = require('os')
  const configFile = path.join(os.homedir(), '.100xprism', 'config.json')
  let cfg = {}
  try { cfg = JSON.parse(fs.readFileSync(configFile, 'utf8')) } catch { cfg = {} }
  if (cfg.skills || cfg.slimApplied) return

  const slim = require('./slim')
  console.log('\n  Slimming the always-on skill index (one time, reversible):')
  slim.writeUserMode(slim.DEFAULT_MODE)

  try {
    cfg = JSON.parse(fs.readFileSync(configFile, 'utf8'))
  } catch { /* writeUserMode just wrote it; fall through with what we have */ }
  cfg.slimApplied = true
  fs.writeFileSync(configFile, JSON.stringify(cfg, null, 2) + '\n')

  const root = slim.resolveInstallDir()
  if (isWindows) {
    require('./adapters/windows').installGlobalWindows(root)
  } else {
    const r = spawnSync('python3',
      [path.join(root, 'adapters', 'lib', 'modules.py'), 'emit-claude-code'],
      { encoding: 'utf8' })
    if (r.stdout) console.log(`  ${r.stdout.trim()}`)
    if (r.status !== 0) console.error(`  ✗ re-emit failed: ${(r.stderr || '').trim().split('\n')[0]}`)
  }
  console.log('  Specialist modules now load through the 100x-resolver catalog instead of')
  console.log('  sitting in every session. Undo with: 100xprism slim --skills=all')
  console.log('  Slim your repos too with: 100xprism slim --all-projects\n')
}

module.exports = { run }
