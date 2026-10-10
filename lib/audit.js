'use strict'

const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawnSync } = require('child_process')
const cleanup = require('./cleanup')

const SCHEMA_VERSION = 1

function readJson(file, fallback = {}) {
  try {
    const value = JSON.parse(fs.readFileSync(file, 'utf8'))
    return value && typeof value === 'object' && !Array.isArray(value) ? value : fallback
  } catch { return fallback }
}

function descriptionChars(files) {
  let chars = 0
  for (const file of files) {
    try {
      const line = fs.readFileSync(file, 'utf8').split(/\r?\n/).slice(0, 40)
        .find(value => value.startsWith('description:'))
      if (line) chars += line.slice('description:'.length).trim().length
    } catch { /* unreadable artifacts are omitted */ }
  }
  return chars
}

function filesIn(dir, predicate) {
  try {
    return fs.readdirSync(dir, { withFileTypes: true })
      .filter(entry => entry.isFile() && predicate(entry.name))
      .map(entry => path.join(dir, entry.name))
  } catch { return [] }
}

function skillFiles(dir) {
  try {
    return fs.readdirSync(dir, { withFileTypes: true })
      .filter(entry => entry.isDirectory() && fs.existsSync(path.join(dir, entry.name, 'SKILL.md')))
      .map(entry => path.join(dir, entry.name, 'SKILL.md'))
  } catch { return [] }
}

function toolRow(files) {
  return { indexed: files.length, description_chars: descriptionChars(files) }
}

// Installed state that no longer matches what 100xprism would write.
function detectDrift(home) {
  const claudeDir = path.join(home, '.claude')
  const drift = []
  for (const command of cleanup.brokenManagedHooks(path.join(claudeDir, 'settings.json'))) {
    drift.push({ kind: 'broken-hook', detail: command })
  }
  for (const file of cleanup.legacyV1Matches(path.join(claudeDir, 'commands'))) {
    drift.push({ kind: 'legacy-v1', detail: file })
  }
  const resolver = path.join(claudeDir, 'skills', cleanup.RESOLVER_SLUG, 'SKILL.md')
  const catalog = path.join(home, '.100xprism', cleanup.CATALOG_DIRNAME)
  let catalogEntries = 0
  try { catalogEntries = fs.readdirSync(catalog).length } catch { /* absent */ }
  if (fs.existsSync(resolver) && !catalogEntries) {
    drift.push({ kind: 'missing-catalog', detail: catalog })
  }
  return drift
}

function fixDrift(home, repoRoot, drift) {
  const claudeDir = path.join(home, '.claude')
  const kinds = new Set(drift.map(d => d.kind))
  if (kinds.has('broken-hook')) cleanup.removeBrokenManagedHooks(path.join(claudeDir, 'settings.json'))
  if (kinds.has('legacy-v1')) cleanup.sweepLegacyV1Commands(path.join(claudeDir, 'commands'))
  if (kinds.has('missing-catalog')) {
    if (process.platform === 'win32') {
      require('./adapters/windows').installGlobalWindows(repoRoot)
    } else {
      spawnSync('python3', [path.join(repoRoot, 'adapters', 'lib', 'modules.py'), 'emit-claude-code'], {
        stdio: 'ignore', env: { ...process.env, HOME: home },
      })
    }
  }
}

function audit(projectPath = process.cwd(), options = {}) {
  const home = options.home || os.homedir()
  const repoRoot = options.repoRoot || path.resolve(__dirname, '..')
  const project = path.resolve(projectPath)

  const claudeSkills = skillFiles(path.join(home, '.claude', 'skills'))
  const claudeCommands = filesIn(path.join(home, '.claude', 'commands'), name => name.endsWith('.md'))
  const cursorRules = filesIn(path.join(project, '.cursor', 'rules'), name => name.endsWith('.mdc'))
  const codexSkills = skillFiles(path.join(project, '.agents', 'skills'))
  const piSkills = skillFiles(path.join(project, '.pi', 'skills'))

  const tools = {
    claude: { ...toolRow(claudeSkills), aliases: claudeCommands.length,
      alias_description_chars: descriptionChars(claudeCommands) },
    cursor: toolRow(cursorRules),
    codex: toolRow(codexSkills),
    pi: toolRow(piSkills),
  }

  const instructions = []
  for (const name of ['CLAUDE.md', 'AGENTS.md', '.cursorrules']) {
    const file = path.join(project, name)
    try { instructions.push({ file: name, bytes: fs.statSync(file).size }) } catch { /* absent */ }
  }

  const settings = readJson(path.join(home, '.claude', 'settings.json'))
  const enabledPlugins = settings.enabledPlugins && typeof settings.enabledPlugins === 'object'
    ? settings.enabledPlugins : {}
  const policy = readJson(path.join(repoRoot, 'plugins', 'plugins.json'))
  const hookGroups = settings.hooks && typeof settings.hooks === 'object' ? settings.hooks : {}
  const hookCount = entries => Array.isArray(entries)
    ? entries.reduce((n, entry) => n + (Array.isArray(entry.hooks) ? entry.hooks.length : 0), 0) : 0
  const hooksTotal = Object.values(hookGroups).reduce((n, entries) => n + hookCount(entries), 0)

  const instructionBytes = instructions.reduce((n, row) => n + row.bytes, 0)
  const descriptionBytes = Object.values(tools).reduce(
    (n, row) => n + (row.description_chars || 0) + (row.alias_description_chars || 0), 0)
  const standingBytes = instructionBytes + descriptionBytes

  return {
    schema_version: SCHEMA_VERSION,
    project,
    measurement: 'estimate',
    estimate_method: 'UTF-8 instruction bytes plus indexed description characters divided by four',
    tools,
    instructions,
    plugins: {
      enabled: Object.values(enabledPlugins).filter(Boolean).length,
      managed_core: (policy.plugins || []).filter(id => enabledPlugins[id] === true).length,
      core_policy: policy.plugins || [],
      recommended: policy.recommended || {},
      manual: policy.manual || [],
    },
    hooks: { total: hooksTotal, session_start: hookCount(hookGroups.SessionStart) },
    standing_context: {
      measured_bytes: standingBytes,
      estimated_tokens: Math.round(standingBytes / 4),
    },
    drift: detectDrift(home),
  }
}

function printText(report) {
  console.log(`100xprism context audit — ${report.project}`)
  console.log(`Standing context estimate: ~${report.standing_context.estimated_tokens.toLocaleString()} tokens (${report.standing_context.measured_bytes.toLocaleString()} measured bytes/chars)`)
  for (const [tool, row] of Object.entries(report.tools)) {
    console.log(`- ${tool}: ${row.indexed} indexed${row.aliases == null ? '' : `; ${row.aliases} aliases`}`)
  }
  console.log(`- Claude plugins: ${report.plugins.enabled} enabled; ${report.plugins.managed_core} selected core`)
  console.log(`- Claude hooks: ${report.hooks.total}; SessionStart: ${report.hooks.session_start}`)
  if (report.drift.length) {
    console.log(`Drift: ${report.drift.length} issue(s) — run \`100xprism audit --fix\` to repair`)
    for (const d of report.drift) console.log(`  ! ${d.kind}: ${d.detail}`)
  }
  console.log('Measurement: estimate (not provider-billed usage). Use `100xprism tokens --json` for local provider counters.')
}

function run(args = []) {
  const json = args.includes('--json')
  const positional = args.filter(arg => !arg.startsWith('-'))
  let report = audit(positional[0] || process.cwd())
  if (args.includes('--fix') && report.drift.length) {
    fixDrift(os.homedir(), path.resolve(__dirname, '..'), report.drift)
    if (!json) console.log(`Repaired ${report.drift.length} drift issue(s).`)
    report = audit(positional[0] || process.cwd())
  }
  if (json) process.stdout.write(JSON.stringify(report, null, 2) + '\n')
  else printText(report)
}

module.exports = { audit, detectDrift, fixDrift, run, SCHEMA_VERSION }
