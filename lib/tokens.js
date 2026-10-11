'use strict'

const { spawnSync } = require('child_process')
const path = require('path')

const repoRoot = path.join(__dirname, '..')

function runPythonScript(script, args = []) {
  const result = spawnSync('python3', [path.join(repoRoot, 'scripts', script), ...args], {
    stdio: 'inherit',
  })
  if (result.status !== 0) process.exit(result.status ?? 1)
}

function reportScript(args = []) {
  return args.includes('--json') || args.includes('--tool') || args.includes('--oneline')
    ? 'token_report.py'
    : 'token-dashboard.py'
}

function runDashboard(args = []) {
  const { createLifecycle, BANNER } = require('./token-economics')
  const maintenance = ['--verify-history', '--enable-content-search', '--disable-content-search']
  if (process.platform !== 'darwin') {
    console.error(BANNER)
    if (args.some(a => maintenance.includes(a))) return
    runPythonScript(reportScript(args), args)
    return
  }
  const { stopDashboard, startOwnedDashboard } = require('./dashboard')
  const lifecycle = createLifecycle({stopDashboard})
  if (args.some(a => maintenance.includes(a))) {
    if (!lifecycle.ownedAgent()) {
      const stable = require('./platform').installDir
      lifecycle.install(require('fs').existsSync(path.join(stable,'scripts','token-collector.py')) ? stable : repoRoot)
    }
    if (args.includes('--enable-content-search')) {
      console.log('Local search indexes user prompts and assistant prose only. Text remains on this device.')
      lifecycle.contentSearch(true)
    } else if (args.includes('--disable-content-search')) {
      lifecycle.contentSearch(false)
      console.log('Indexed content removed from retained databases. Filesystem backups may still contain earlier text.')
    } else lifecycle.collect(['--verify-history'])
    return
  }
  if (args.includes('--json') || args.includes('--tool') || args.includes('--oneline') || args.includes('--print')) {
    if (!lifecycle.ownedAgent()) {
      const stable = require('./platform').installDir
      lifecycle.install(require('fs').existsSync(path.join(stable,'scripts','token-collector.py')) ? stable : repoRoot)
    }
    const generation=lifecycle.active()
    const result=spawnSync(lifecycle.python(), ['-B',path.join(generation,'scripts',reportScript(args)),...args,
      ...(reportScript(args)==='token-dashboard.py' ? ['--db',path.join(generation,'token-economics.sqlite3'),'--root',lifecycle.root] : [])],{stdio:'inherit'})
    if (result.status!==0) process.exitCode=result.status ?? 1
    return
  }
  startOwnedDashboard(args)
}

function runValue(args = []) {
  runPythonScript('value-report.py', args)
}

module.exports = { runDashboard, runValue, reportScript }
