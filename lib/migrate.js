'use strict'

// One-time, idempotent migration for installs created under the old `100x-dev`
// name. Moves the clone dir (~/100x-dev → ~/100xprism) and the config/cache dir
// (~/.100x-dev → ~/.100xprism), and repoints the git remote at the renamed
// GitHub repo. Safe to call on every install/update: it no-ops once migrated,
// and never overwrites a new-name dir that already exists.

const { spawnSync } = require('child_process')
const fs = require('fs')
const path = require('path')
const os = require('os')

const NEW_REMOTE = 'https://github.com/rajitsaha/100xprism.git'

function migrateLegacy(opts = {}) {
  const home = opts.home || os.homedir()
  const legacyInstall = opts.legacyInstall || path.join(home, '100x-dev')
  const newInstall = opts.newInstall || path.join(home, '100xprism')
  const legacyConfig = opts.legacyConfig || path.join(home, '.100x-dev')
  const newConfig = opts.newConfig || path.join(home, '.100xprism')
  const remote = opts.remote || NEW_REMOTE
  const runGit = opts.runGit !== false
  const log = opts.log || console.log

  const actions = []

  // 1. Clone/install directory + git remote.
  if (fs.existsSync(legacyInstall) && !fs.existsSync(newInstall)) {
    fs.renameSync(legacyInstall, newInstall)
    actions.push('moved ~/100x-dev → ~/100xprism')
    if (runGit && fs.existsSync(path.join(newInstall, '.git'))) {
      const r = spawnSync('git', ['-C', newInstall, 'remote', 'set-url', 'origin', remote], { stdio: 'ignore' })
      if (!r.error && r.status === 0) actions.push('repointed git remote → 100xprism')
    }
  }

  // 2. Config/cache directory (tracked-projects, gate-cache, update-cache).
  if (fs.existsSync(legacyConfig) && !fs.existsSync(newConfig)) {
    fs.renameSync(legacyConfig, newConfig)
    actions.push('moved ~/.100x-dev → ~/.100xprism')
  }

  if (actions.length) {
    log('Migrating your install from 100x-dev to 100xPrism:')
    for (const a of actions) log('  ✓ ' + a)
    log('  (the `100x-dev` command still works as an alias)')
  }
  return actions
}

module.exports = { migrateLegacy }
