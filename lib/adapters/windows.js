'use strict'

const fs = require('fs')
const path = require('path')
const { spawnSync } = require('child_process')
const { sweepLegacyV1Commands, isGeneratedAgents } = require('../cleanup')

// ── v2 module emit (pure-JS port of adapters/lib/modules.py emit-claude-code) ──
// Windows has no bash, so the Node path can't call install.sh/update.sh. This
// mirrors the Python emitter: parse modules/<slug>/SKILL.md, write Claude Code
// skills + slash-command aliases, and prune anything we previously emitted that
// no longer exists — without touching the user's own skills/commands.

const GENERATED_MARKER = '.100xprism-generated'
const ALIAS_MARKER = '<!-- 100xprism generated alias — regenerate, do not edit -->'
const MANIFEST_NAME = '.100xprism-manifest.json'
// Modules removed/merged upstream — cleaned up even for installs that predate the
// manifest/marker. Safe to trim once installs have cycled through a v2 update.
const REMOVED_MODULES = new Set(['systems-architect', 'conversion-copy'])

const MODEL_TIER_HINT = {
  haiku: 'fast / low-cost (mechanical task)',
  sonnet: 'balanced (moderate reasoning)',
  opus: 'most capable (deep reasoning)',
}
const CATEGORY_ORDER = [
  'lifecycle', 'quality', 'engineering', 'data', 'design', 'docs', 'marketing', 'uncategorized',
]

// ── Retention / profiles ──────────────────────────────────────────────────────
// Port of the same block in adapters/lib/modules.py — keep the two in sync. See
// that file for why a module's *installation* has a standing token cost while its
// body does not, and what each retention class means.
const RETENTION_CLASSES = new Set(['must', 'profile', 'resolver'])
const MUST_HAVE = new Set([
  'gate', 'commit', 'push', 'branch', 'pr', 'release',
  'test', 'lint', 'security', 'docs', 'eval', 'pair-loop',
])
const RESOLVER_CATEGORIES = new Set(['marketing', 'design'])
const CATEGORY_PROFILES = {
  lifecycle: ['core'],
  quality: ['core'],
  engineering: ['code'],
  docs: ['code'],
  data: ['data'],
  design: ['design'],
  marketing: ['growth'],
}
const PROJECT_CONFIG = '.100xprism.json'
const RESOLVER_SLUG = '100x-resolver'
const CATALOG_DIRNAME = '100xprism-catalog'
const RESOLVER_DESCRIPTION =
  'Catalog of specialist 100xprism workflows kept out of the always-on index — ' +
  'marketing, SEO, CRO, copywriting, growth, pricing, sales, design, accessibility, ' +
  'motion, and data-visualization playbooks. Read this file\'s table to find the right ' +
  'one, then read that module\'s SKILL.md path before starting the work.'

const PROFILE_MARKERS = {
  code: [
    'package.json', 'pyproject.toml', 'setup.py', 'requirements.txt', 'go.mod',
    'Cargo.toml', 'pom.xml', 'build.gradle', 'Gemfile', 'composer.json',
    'tsconfig.json', 'CMakeLists.txt',
  ],
  data: [
    'terraform', 'dbt_project.yml', 'docker-compose.yml', 'docker-compose.yaml',
    'migrations', 'alembic.ini', 'Dockerfile',
  ],
  design: [
    'tailwind.config.js', 'tailwind.config.ts', 'tailwind.config.cjs',
    '.storybook', 'components', 'src/components',
  ],
  growth: [
    'content', 'posts', '_posts', 'blog', 'marketing', 'landing',
    'next-sitemap.config.js', 'public/robots.txt',
  ],
}

function retentionOf(fm, slug, category, slashCommand) {
  const declared = (fm.retention || '').trim()
  if (RETENTION_CLASSES.has(declared)) return declared
  if (MUST_HAVE.has(slug)) return 'must'
  if (slashCommand) return 'profile'
  if (RESOLVER_CATEGORIES.has(category)) return 'resolver'
  return 'profile'
}

function profilesOf(fm, category) {
  const declared = (fm.profiles || '').replace(/,/g, ' ').split(/\s+/).filter(Boolean)
  if (declared.length) return declared
  return (CATEGORY_PROFILES[category] || ['core']).slice()
}

function detectProfiles(projectDir) {
  const found = ['core']
  for (const profile of ['code', 'data', 'design', 'growth']) {
    if (PROFILE_MARKERS[profile].some(mk => fs.existsSync(path.join(projectDir, mk)))) {
      found.push(profile)
    }
  }
  if (found.length === 1) found.push('growth')
  return found
}

function readProjectConfig(projectDir) {
  try {
    return JSON.parse(fs.readFileSync(path.join(projectDir, PROJECT_CONFIG), 'utf8'))
  } catch { return {} }
}

// Profiles to filter by; [] means must-only and null explicitly means all.
function activeProfiles(projectDir) {
  const env = (process.env.PRISM_PROFILES || '').trim()
  if (env) {
    const vals = env.replace(/,/g, ' ').split(/\s+/).filter(Boolean)
    if (vals.includes('all')) return null
    return [...new Set([...vals, 'core'])].sort()
  }
  let cfg = readProjectConfig(projectDir).profiles
  if (!cfg || (Array.isArray(cfg) && cfg.length === 0)) return []
  if (typeof cfg === 'string') cfg = [cfg]
  if (cfg.includes('all')) return null
  return [...new Set([...cfg.map(String).map(s => s.trim()).filter(Boolean), 'core'])].sort()
}

function selectModules(modules, profiles) {
  const keep = [], catalog = []
  for (const m of modules) {
    if (m.retention === 'resolver') catalog.push(m)
    else if (m.retention === 'must' || profiles === null) keep.push(m)
    else if (m.profiles.some(p => profiles.includes(p))) keep.push(m)
    else catalog.push(m)
  }
  return { keep, catalog }
}

function userSkillsMode() {
  const env = (process.env.PRISM_SKILLS || '').trim().toLowerCase()
  if (['all', 'profile', 'must'].includes(env)) return env
  if (env) return 'must'
  try {
    const cfg = JSON.parse(fs.readFileSync(
      path.join(require('os').homedir(), '.100xprism', 'config.json'), 'utf8'))
    const mode = String(cfg.skills || 'must').trim().toLowerCase()
    return ['all', 'profile', 'must'].includes(mode) ? mode : 'must'
  } catch { return 'must' }
}

function splitByMode(modules, mode) {
  if (mode === 'all') return { installed: modules.slice(), catalog: [] }
  const installed = mode === 'must'
    ? modules.filter(m => m.retention === 'must')
    : modules.filter(m => m.retention === 'must' || m.retention === 'profile')
  const kept = new Set(installed.map(m => m.slug))
  return { installed, catalog: modules.filter(m => !kept.has(m.slug)) }
}

function renderCatalogTable(catalog, pathTemplate) {
  if (!catalog.length) return '_No catalog modules — every module is installed as a skill._\n'
  const byCat = {}
  for (const m of catalog) (byCat[m.category] = byCat[m.category] || []).push(m)
  const lines = []
  for (const cat of Object.keys(byCat).sort((a, b) => categorySortKey(a) - categorySortKey(b))) {
    lines.push(`\n### ${cat.charAt(0).toUpperCase() + cat.slice(1)}\n`)
    lines.push('| Module | Use it when | Read |')
    lines.push('|---|---|---|')
    for (const m of byCat[cat].slice().sort((a, b) => a.slug.localeCompare(b.slug))) {
      const trigger = shortDescription(m.description).replace(/\|/g, '\\|')
      lines.push(`| \`${m.slug}\` | ${trigger} | \`${pathTemplate.replace('{slug}', m.slug)}\` |`)
    }
  }
  return lines.join('\n') + '\n'
}

function renderResolver(catalog, pathTemplate) {
  return '---\n' +
    `name: ${RESOLVER_SLUG}\n` +
    `description: ${RESOLVER_DESCRIPTION}\n` +
    'category: docs\n' +
    'tier: on-demand\n' +
    '---\n\n' +
    `# 100xprism catalog (${catalog.length} modules)\n\n` +
    'These modules are deliberately not in the always-on skill index. Find the row ' +
    'that matches the task, **read the file in its `Read` column**, then follow it as ' +
    'you would any skill. If nothing matches, proceed without one.\n' +
    renderCatalogTable(catalog, pathTemplate)
}

function renderGenericRoute(argumentExpr = '$ARGUMENTS') {
  return '---\n' +
    'description: Load any routed 100xprism workflow by slug without indexing every workflow.\n' +
    'argument-hint: <workflow> [arguments]\n' +
    '---\n\n' + ALIAS_MARKER + '\n\n' +
    'Use the `100x-resolver` skill. Find the workflow named by the first argument, ' +
    'read its catalog SKILL.md path, and follow it with the remaining arguments.\n\n' +
    argumentExpr + '\n'
}

// Park catalog bodies outside anything the tool indexes. Cleared and rewritten
// wholesale so a module that graduates back into the index leaves no stale copy.
function writeCatalogBodies(modulesDir, catalog, catalogDir) {
  fs.rmSync(catalogDir, { recursive: true, force: true })
  if (!catalog.length) return
  fs.mkdirSync(catalogDir, { recursive: true })
  for (const m of catalog) {
    const target = path.join(catalogDir, m.slug)
    fs.cpSync(path.join(modulesDir, m.slug), target, { recursive: true })
    fs.writeFileSync(path.join(target, GENERATED_MARKER),
      'Generated by 100xprism from modules/<slug>/SKILL.md. Regenerate instead of editing here.\n')
  }
}

function tierAnnotation(model) {
  const hint = MODEL_TIER_HINT[(model || '').trim()]
  return hint ? `_Suggested model tier: ${hint}_` : ''
}

function shortDescription(desc) {
  let d = (desc || '').split('. ')[0]
  if (d.length > 140) d = d.slice(0, 137) + '...'
  return d
}

// Mirror modules.py split_frontmatter: simple `key: value` block with indented
// continuation lines folded into the previous key.
function parseFrontmatter(text) {
  // Normalise CRLF first. Git on Windows checks out with CRLF by default, and
  // Node's readFileSync returns those bytes verbatim, so a literal '---\n' test
  // failed on exactly the platform this file exists to serve — yielding empty
  // frontmatter and the whole SKILL.md as its body. Python's Path.read_text()
  // does universal-newline translation, which is why the emitters only matched
  // on LF checkouts.
  text = text.replace(/\r\n/g, '\n')
  if (!text.startsWith('---\n')) return { fm: {}, body: text }
  const end = text.indexOf('\n---\n', 4)
  if (end === -1) return { fm: {}, body: text }
  const block = text.slice(4, end)
  const body = text.slice(end + 5)
  const fm = {}
  let currentKey = null
  for (const line of block.split('\n')) {
    if (!line.trim()) continue
    if (/^\s/.test(line) && currentKey) {
      fm[currentKey] = `${fm[currentKey]} ${line.trim()}`.trim()
      continue
    }
    const idx = line.indexOf(':')
    if (idx !== -1) {
      currentKey = line.slice(0, idx).trim()
      fm[currentKey] = line.slice(idx + 1).trim()
    }
  }
  return { fm, body }
}

function listModules(modulesDir) {
  const out = []
  let entries = []
  try {
    entries = fs.readdirSync(modulesDir, { withFileTypes: true })
  } catch {
    return out
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue
    const skillMd = path.join(modulesDir, entry.name, 'SKILL.md')
    if (!fs.existsSync(skillMd)) continue // skips shared-reference dirs like _lib/
    const { fm, body } = parseFrontmatter(fs.readFileSync(skillMd, 'utf8'))
    const category = fm.category || 'uncategorized'
    const slash = (fm.slash_command || '').replace(/^\//, '')
    out.push({
      slug: entry.name,
      category,
      tier: fm.tier || 'on-demand',
      retention: retentionOf(fm, entry.name, category, slash),
      profiles: profilesOf(fm, category),
      slash,
      description: fm.description || '',
      model: fm.model || '',
      fm,
      body,
    })
  }
  out.sort((a, b) =>
    (a.tier !== 'core') - (b.tier !== 'core') ||
    a.category.localeCompare(b.category) ||
    a.slug.localeCompare(b.slug))
  return out
}

function renderCommandAlias(m) {
  const lines = ['---', `description: ${shortDescription(m.description)}`]
  if (m.model) lines.push(`model: ${m.model}`)
  const allowed = (m.fm['allowed-tools'] || '').trim()
  if (allowed) lines.push(`allowed-tools: ${allowed}`)
  if (m.body.includes('$ARGUMENTS') || m.body.includes('$1')) lines.push('argument-hint: [arguments]')
  lines.push('---', '', ALIAS_MARKER, '', `Use the \`${m.fm.name || m.slug}\` skill.`, '', '$ARGUMENTS', '')
  return lines.join('\n')
}

function readManifest(skillsDir) {
  try {
    return JSON.parse(fs.readFileSync(path.join(skillsDir, MANIFEST_NAME), 'utf8'))
  } catch {
    return {}
  }
}

// Emit all modules as Claude Code skills + slash aliases, then prune stale ones.
// Returns { skills, commands, prunedSkills, prunedCommands }.
function emitClaudeModules(modulesDir, skillsDir, commandsDir) {
  fs.mkdirSync(skillsDir, { recursive: true })
  fs.mkdirSync(commandsDir, { recursive: true })
  sweepLegacyV1Commands(commandsDir)

  const prev = readManifest(skillsDir)
  const prevSkills = new Set(prev.skills || [])
  const prevCmds = new Set(prev.commands || [])

  const modules = listModules(modulesDir)
  const currentSkills = []
  const currentCmds = []
  const { installed, catalog } = splitByMode(modules, userSkillsMode())

  for (const m of installed) {
    const target = path.join(skillsDir, m.slug)
    fs.rmSync(target, { recursive: true, force: true })
    fs.cpSync(path.join(modulesDir, m.slug), target, { recursive: true })
    fs.writeFileSync(path.join(target, GENERATED_MARKER),
      'Generated by 100xprism from modules/<slug>/SKILL.md. Regenerate instead of editing here.\n')
    currentSkills.push(m.slug)
    // Alias ONLY when the command name differs from the slug (e.g. fix-bugs → /fix).
    // Claude Code already exposes every skill as /<slug>, so a same-name alias would
    // double-list the module in the session index and pay its description twice.
    // Mirrors cmd_emit_claude_code in adapters/lib/modules.py.
    if (m.slash && m.slash !== m.slug) {
      fs.writeFileSync(path.join(commandsDir, `${m.slash}.md`), renderCommandAlias(m))
      currentCmds.push(m.slash)
    }
  }

  // Catalog bodies live beside ~/.claude, not inside skills/ — Claude Code indexes
  // every SKILL.md under skills/, so parking them there would keep charging the
  // description rent this split exists to remove.
  const catalogDir = path.join(require('os').homedir(), '.100xprism', CATALOG_DIRNAME)
  writeCatalogBodies(modulesDir, catalog, catalogDir)
  if (catalog.length) {
    const resolverTarget = path.join(skillsDir, RESOLVER_SLUG)
    fs.rmSync(resolverTarget, { recursive: true, force: true })
    fs.mkdirSync(resolverTarget, { recursive: true })
    fs.writeFileSync(path.join(resolverTarget, 'SKILL.md'),
      renderResolver(catalog, path.join(catalogDir, '{slug}', 'SKILL.md')))
    fs.writeFileSync(path.join(resolverTarget, GENERATED_MARKER),
      'Generated by 100xprism. Regenerate instead of editing here.\n')
    currentSkills.push(RESOLVER_SLUG)
    fs.writeFileSync(path.join(commandsDir, '100x.md'), renderGenericRoute())
    currentCmds.push('100x')
  }

  const curSkillSet = new Set(currentSkills)
  const curCmdSet = new Set(currentCmds)

  // Prune skills we previously emitted / marked / shipped-as-removed that are gone now.
  const orphanSkills = new Set([...prevSkills, ...REMOVED_MODULES].filter(s => !curSkillSet.has(s)))
  for (const child of fs.readdirSync(skillsDir, { withFileTypes: true })) {
    if (child.isDirectory() && !curSkillSet.has(child.name) &&
        fs.existsSync(path.join(skillsDir, child.name, GENERATED_MARKER))) {
      orphanSkills.add(child.name)
    }
  }
  let prunedSkills = 0
  for (const slug of orphanSkills) {
    const p = path.join(skillsDir, slug)
    const ours = fs.existsSync(path.join(p, GENERATED_MARKER)) || prevSkills.has(slug) || REMOVED_MODULES.has(slug)
    if (fs.existsSync(p) && ours) { fs.rmSync(p, { recursive: true, force: true }); prunedSkills++ }
  }

  // Prune slash-command aliases we previously wrote (marker-guarded).
  let prunedCommands = 0
  for (const name of prevCmds) {
    if (curCmdSet.has(name)) continue
    const f = path.join(commandsDir, `${name}.md`)
    try {
      if (fs.existsSync(f) && fs.readFileSync(f, 'utf8').includes(ALIAS_MARKER)) {
        fs.unlinkSync(f); prunedCommands++
      }
    } catch { /* ignore */ }
  }

  fs.writeFileSync(path.join(skillsDir, MANIFEST_NAME),
    JSON.stringify({ skills: currentSkills.slice().sort(), commands: currentCmds.slice().sort() }, null, 2) + '\n')

  return { skills: currentSkills.length, commands: currentCmds.length, prunedSkills, prunedCommands }
}

function categorySortKey(c) {
  const i = CATEGORY_ORDER.indexOf(c)
  return i === -1 ? CATEGORY_ORDER.length : i
}

function emitIndex(modules) {
  const byCat = {}
  for (const m of modules) (byCat[m.category] = byCat[m.category] || []).push(m)
  const lines = []
  for (const cat of Object.keys(byCat).sort((a, b) => categorySortKey(a) - categorySortKey(b))) {
    lines.push(`**${cat.charAt(0).toUpperCase() + cat.slice(1)}** (${byCat[cat].length}):`)
    for (const m of byCat[cat]) {
      const slash = m.slash ? ` \`/${m.slash}\`` : ''
      const tier = tierAnnotation(m.model)
      lines.push(`- \`${m.slug}\`${slash} — ${shortDescription(m.description)}${tier ? ' ' + tier : ''}`)
    }
    lines.push('')
  }
  return lines.join('\n').replace(/\s+$/, '') + '\n'
}

// Keep in sync with CURSOR_MARKER / CURSOR_MARKER_LINES in adapters/lib/modules.py.
const CURSOR_MARKER = 'generated by 100xprism'
const CURSOR_MARKER_LINES = 10

// Per-rule Cursor output (port of modules.py cmd_emit_cursor): one
// .cursor/rules/<slug>.mdc per module. Core modules are always in context;
// on-demand modules are agent-requested from a tight first-sentence description.
function emitCursorRules(modulesDir, projectPath) {
  const rulesDir = path.join(projectPath, '.cursor', 'rules')
  if (fs.existsSync(rulesDir)) {
    // Remove only files we previously wrote (those carrying our generation marker).
    // Must match cmd_emit_cursor in adapters/lib/modules.py: the marker is on
    // line 7, below the frontmatter, so a 6-line window matches nothing.
    for (const f of fs.readdirSync(rulesDir).filter(n => n.endsWith('.mdc'))) {
      const full = path.join(rulesDir, f)
      try {
        const head = fs.readFileSync(full, 'utf8').split('\n').slice(0, CURSOR_MARKER_LINES)
        if (head.some(line => line.includes(CURSOR_MARKER))) fs.unlinkSync(full)
      } catch { /* unreadable file — leave it alone */ }
    }
  }
  fs.mkdirSync(rulesDir, { recursive: true })

  const modules = listModules(modulesDir)

  // Fresh emits are must-only; null is reserved for explicit `profiles: ["all"]`.
  const profiles = activeProfiles(projectPath)
  const { keep, catalog } = profiles === null
    ? { keep: modules, catalog: [] }
    : selectModules(modules, profiles)

  const writeRule = (slug, description, alwaysApply, model, body) => {
    const fm = [
      '---',
      `description: ${description}`,
      'globs:',
      `alwaysApply: ${alwaysApply}`,
      '---',
      '',
      `<!-- generated by 100xprism from modules/${slug}/SKILL.md -->`,
      '',
    ]
    const tier = tierAnnotation(model)
    if (tier) fm.push(tier, '')
    fs.writeFileSync(path.join(rulesDir, `${slug}.mdc`), fm.join('\n') + body.replace(/^\n+/, ''))
  }

  for (const m of keep) {
    writeRule(m.slug, shortDescription(m.description),
      m.tier === 'core' ? 'true' : 'false', m.model, m.body)
  }

  const catalogDir = path.join(projectPath, '.cursor', CATALOG_DIRNAME)
  writeCatalogBodies(modulesDir, catalog, catalogDir)
  if (catalog.length) {
    // The resolver is itself a rule, so Cursor can request it by description —
    // one description standing in for the descriptions of everything it lists.
    const { body } = parseFrontmatter(
      renderResolver(catalog, `.cursor/${CATALOG_DIRNAME}/{slug}/SKILL.md`))
    writeRule(RESOLVER_SLUG, RESOLVER_DESCRIPTION, 'false', '', body)
  }

  return keep.length + (catalog.length ? 1 : 0)
}


function renderCodexAgents(modules) {
  const out = [
    '# 100x Dev for Codex\n',
    '# Generated by 100xprism (https://github.com/rajitsaha/100xprism)\n',
    '# Source of truth: modules/<slug>/SKILL.md. Regenerate instead of hand-editing.\n\n',
    '## How Codex Should Use 100x Dev\n\n',
    '- Full reusable workflows live in `.agents/skills/<slug>/SKILL.md` so Codex can load them on demand.\n',
    '- When the user names a 100xprism slash workflow like `/gate`, treat it as a request to use the matching skill listed below.\n',
    '- Prefer explicit skill invocation (`$gate`, `$commit`, `$test`, etc.) or `/skills` when available; custom prompt slash commands are intentionally not generated.\n',
    '- Codex hooks, when generated, live in `.codex/hooks.json`. Review and trust them with `/hooks` before expecting enforcement.\n',
    '- Claude Code plugins in `plugins/plugins.json` are not Codex plugins. Use Codex `/plugins` for Codex-native plugins and app/MCP integrations.\n\n',
    '## 100xprism Command Map\n\n',
  ]

  for (const m of modules.filter(m => m.slash).sort((a, b) => a.slash.localeCompare(b.slash))) {
    out.push(`- \`/${m.slash}\` → \`$${m.slug}\` — ${shortDescription(m.description)}\n`)
  }

  out.push('\n## Available Skills\n\n')
  out.push('Codex can invoke these implicitly from their descriptions or explicitly by `$name` / `/skills`.\n\n')
  out.push(emitIndex(modules))
  return out.join('')
}

function codexHookCommand(script) {
  return `python3 .codex/100xprism-hooks/run-hook.py ${JSON.stringify(script).slice(1, -1)}`
}

function hookEnabled(hook) {
  const env = hook.toggle_env
  if (env && Object.prototype.hasOwnProperty.call(process.env, env)) {
    const val = String(process.env[env]).trim().toLowerCase()
    if (['1', 'true', 'yes', 'on'].includes(val)) return true
    if (['0', 'false', 'no', 'off', ''].includes(val)) return false
  }
  return Boolean(hook.default)
}

function codexHookWrapper(manifest) {
  const allowed = (manifest.hooks || [])
    .map(hook => hook && hook.script)
    .filter(Boolean)
    .sort()
  const template = fs.readFileSync(
    path.join(__dirname, '..', '..', 'adapters', 'templates', 'codex-run-hook.py'),
    'utf8',
  )
  return template.replace('__ALLOWED_HOOKS_JSON__', JSON.stringify(allowed))
}

function emitCodexProject(modulesDir, projectPath, hooksManifestPathOrDir) {
  const modules = listModules(modulesDir)
  const absProject = path.resolve(projectPath)

  const profiles = activeProfiles(absProject)
  const { keep, catalog } = profiles === null
    ? { keep: modules, catalog: [] }
    : selectModules(modules, profiles)

  fs.mkdirSync(absProject, { recursive: true })
  // Only (re)write an AGENTS.md we generated; a hand-written one is the
  // project's own contract and must survive optimize/update runs.
  const agentsFile = path.join(absProject, 'AGENTS.md')
  if (!fs.existsSync(agentsFile) || isGeneratedAgents(fs.readFileSync(agentsFile, 'utf8'))) {
    fs.writeFileSync(agentsFile, renderCodexAgents(keep))
  }

  const skillsDir = path.join(absProject, '.agents', 'skills')
  if (fs.existsSync(skillsDir)) {
    for (const child of fs.readdirSync(skillsDir, { withFileTypes: true })) {
      if (!child.isDirectory()) continue
      const target = path.join(skillsDir, child.name)
      if (fs.existsSync(path.join(target, GENERATED_MARKER))) {
        fs.rmSync(target, { recursive: true, force: true })
      }
    }
  }
  fs.mkdirSync(skillsDir, { recursive: true })

  let skillCount = 0
  for (const m of keep) {
    const source = path.join(modulesDir, m.slug)
    const target = path.join(skillsDir, m.slug)
    if (fs.existsSync(target)) {
      if (fs.existsSync(path.join(target, GENERATED_MARKER))) {
        fs.rmSync(target, { recursive: true, force: true })
      } else {
        console.error(`skipped existing non-100xprism skill: ${target}`)
        continue
      }
    }
    fs.cpSync(source, target, { recursive: true })
    fs.writeFileSync(
      path.join(target, GENERATED_MARKER),
      'Generated by 100xprism from modules/<slug>/SKILL.md. Regenerate instead of editing here.\n',
    )
    skillCount++
  }

  const catalogDir = path.join(absProject, '.agents', CATALOG_DIRNAME)
  writeCatalogBodies(modulesDir, catalog, catalogDir)
  if (catalog.length) {
    const target = path.join(skillsDir, RESOLVER_SLUG)
    fs.rmSync(target, { recursive: true, force: true })
    fs.mkdirSync(target, { recursive: true })
    fs.writeFileSync(path.join(target, 'SKILL.md'),
      renderResolver(catalog, `.agents/${CATALOG_DIRNAME}/{slug}/SKILL.md`))
    fs.writeFileSync(path.join(target, GENERATED_MARKER),
      'Generated by 100xprism. Regenerate instead of editing here.\n')
    skillCount++
  }

  const manifestPath = fs.statSync(hooksManifestPathOrDir).isDirectory()
    ? path.join(hooksManifestPathOrDir, 'hooks.manifest.json')
    : hooksManifestPathOrDir
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
  const codexDir = path.join(absProject, '.codex')
  const wrapperDir = path.join(codexDir, '100xprism-hooks')
  fs.mkdirSync(wrapperDir, { recursive: true })
  const wrapper = path.join(wrapperDir, 'run-hook.py')
  fs.writeFileSync(wrapper, codexHookWrapper(manifest))
  try { fs.chmodSync(wrapper, 0o755) } catch { /* ignore chmod on restricted filesystems */ }

  const hooks = {}
  const hooksOptedIn = ['1', 'true', 'yes', 'on'].includes(
    String(process.env.PRISM_HOOKS || '').trim().toLowerCase())
  for (const hook of manifest.hooks || []) {
    if (!hooksOptedIn || !hookEnabled(hook)) continue
    hooks[hook.event] = hooks[hook.event] || []
    hooks[hook.event].push({
      matcher: hook.matcher,
      hooks: [{
        type: 'command',
        command: codexHookCommand(hook.script),
        statusMessage: `100xprism: ${hook.description}`,
      }],
    })
  }
  fs.writeFileSync(path.join(codexDir, 'hooks.json'), JSON.stringify({ hooks }, null, 2) + '\n')

  return { skills: skillCount, hookGroups: Object.values(hooks).reduce((n, groups) => n + groups.length, 0) }
}

function scaffoldClaudeMd(projectPath) {
  const existing = ['CLAUDE.md', 'AGENTS.md', '.cursorrules']
  if (existing.some(f => fs.existsSync(path.join(projectPath, f)))) return

  // Keep in sync with the scaffold in adapters/claude-code.sh. CLAUDE.md is
  // re-sent on every turn, so it holds only what a model cannot infer from the
  // code; machine-readable config goes to .claude/100xprism.yml, read on demand.
  const projectName = path.basename(path.resolve(projectPath))
  fs.writeFileSync(path.join(projectPath, 'CLAUDE.md'), `# ${projectName} — Project Instructions

<!-- Generated by 100xprism. Every line here is re-sent on EVERY turn — keep it to
     facts a model cannot infer from the code, and push detail into docs/ below. -->

## Project Overview

<!-- Stack, hosting, database, auth — one bullet each. -->
- TODO

## Key Conventions

<!-- Non-obvious invariants ONLY: the things that cost a debugging session when
     guessed wrong (ID types, middleware ordering, enum/name mismatches, and so on).
     Anything a competent model would already do correctly does not belong here. -->
- TODO

## Reference docs (read on demand)

<!-- The router. Add a row per area; the agent reads the doc only when it applies,
     so detail here is free. Delete the placeholder rows you do not need. -->

| When you are… | Read |
|---|---|
| writing or running tests | \`docs/testing.md\` |
| reasoning about services, data flow, layout | \`docs/architecture.md\` |
| bumping deps / triaging security findings | \`docs/security-exceptions.md\` |

## Rules

<!-- Project-specific rules for Claude. /update-claude appends to this section. -->
`)

  const claudeDir = path.join(projectPath, '.claude')
  fs.mkdirSync(claudeDir, { recursive: true })
  const configPath = path.join(claudeDir, '100xprism.yml')
  if (!fs.existsSync(configPath)) {
    fs.writeFileSync(configPath, `# 100xprism project config — read on demand, NOT loaded into every turn.
# Consumed by /db, /query, /gate, /cloud-security and /launch.
# Everything is optional; delete what does not apply.
#
# Keys must stay flush-left (column 0): the modules match them with an anchored
# grep, so an indented key is silently invisible.

# --- Database (/db, /query) ---
# engine: postgres          # postgres | mysql | sqlite | snowflake | bigquery
#                           # | athena | databricks | presto | oracle | cloud-sql
# connection: default
# connections:
#   default:
#     host: localhost
#     port: 5432
#     name: mydb
#     user: myuser
#     auth: env:DB_PASSWORD   # env:VAR_NAME | secret:SECRET_NAME | prompt

# --- Cloud / GCP (/gate, /cloud-security, /launch) ---
# gcp_project: my-gcp-project
# cloud_run_service: my-service
# region: us-central1

# --- Production (/launch, /push) ---
# production_url: https://example.com
# health_url: https://example.com/health

# --- Known findings /security should skip ---
# security_exceptions:
#   - CVE-2023-XXXX: false positive in test dependency
`)
  }
  console.log(`  → Scaffolded CLAUDE.md + .claude/100xprism.yml in ${projectPath} ✓`)
}

// Reconcile enabledPlugins with plugins.json: ADD newly-declared plugins and
// REMOVE ones 100xprism previously installed but has since dropped — without
// touching plugins the user enabled themselves. The "managed" set is tracked in
// a sidecar beside settings.json (mirrors adapters/lib/sync_plugins.py). On the
// a plugin is owned only when this function actually adds it. Already-enabled
// plugins stay user-owned even when current policy recommends them.
function mergePluginsJson(pluginsFile, settingsFile) {
  if (!fs.existsSync(settingsFile)) {
    fs.mkdirSync(path.dirname(settingsFile), { recursive: true })
    fs.writeFileSync(settingsFile, '{}')
  }
  const pluginsData = JSON.parse(fs.readFileSync(pluginsFile, 'utf8'))
  const settings = JSON.parse(fs.readFileSync(settingsFile, 'utf8'))
  const enabled = settings.enabledPlugins || {}
  const desired = pluginsData.plugins || []
  const desiredSet = new Set(desired)

  const stateFile = path.join(path.dirname(settingsFile), '.100xprism-plugins.json')
  let state = {}
  try { state = JSON.parse(fs.readFileSync(stateFile, 'utf8')) } catch { state = {} }
  const managed = new Set('managed' in state ? (state.managed || []) : [])

  for (const p of desired) {
    if (!(p in enabled)) {
      enabled[p] = true   // never flip an existing value
      managed.add(p)      // own only entries this sync actually added
    }
  }
  for (const p of managed) {
    if (!desiredSet.has(p)) delete enabled[p] // we installed it; it's gone now
  }

  settings.enabledPlugins = enabled
  settings.extraKnownMarketplaces = {
    ...settings.extraKnownMarketplaces,
    ...(pluginsData.extraKnownMarketplaces || {}),
  }
  fs.writeFileSync(settingsFile, JSON.stringify(settings, null, 2))
  fs.writeFileSync(stateFile, JSON.stringify({
    managed: [...managed].filter(p => desiredSet.has(p)).sort(),
  }, null, 2))
}

function addTrackedProject(projectPath, trackedFile) {
  fs.mkdirSync(path.dirname(trackedFile), { recursive: true })
  const existing = fs.existsSync(trackedFile)
    ? fs.readFileSync(trackedFile, 'utf8').split('\n').filter(Boolean)
    : []
  if (!existing.includes(projectPath)) {
    fs.appendFileSync(trackedFile, projectPath + '\n')
  }
}

function installGlobalWindows(installDir) {
  const { claudeDir, claudeCommandsDir, claudeSettingsFile } = require('../platform')
  const skillsDir = path.join(claudeDir, 'skills')
  const r = emitClaudeModules(path.join(installDir, 'modules'), skillsDir, claudeCommandsDir)
  mergePluginsJson(path.join(installDir, 'plugins', 'plugins.json'), claudeSettingsFile)
  const pruned = (r.prunedSkills || r.prunedCommands)
    ? ` (pruned ${r.prunedSkills} stale skill(s), ${r.prunedCommands} stale alias(es))`
    : ''
  console.log(`✓ ${r.skills} skills + ${r.commands} slash aliases installed to ~/.claude/${pruned}`)
  console.log('✓ Plugins merged into ~/.claude/settings.json')
  console.log('\nNext: cd into a project and run  100xprism init  to set it up.')
}

function initProjectWindows(installDir, projectPath) {
  const { trackedProjectsFile } = require('../platform')
  const modulesDir = path.join(installDir, 'modules')
  const absProject = path.resolve(projectPath)
  const readline = require('readline')
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout })

  console.log('\n100x Dev — Project Setup')
  console.log(`  Project: ${absProject}\n`)

  const tools = [
    { label: 'Claude Code (CLAUDE.md scaffold)', key: 'claude' },
    { label: 'Cursor (.cursor/rules/)', key: 'cursor' },
    { label: 'Codex (AGENTS.md)', key: 'codex' },
  ]
  const selected = {}

  function promptNext(idx) {
    if (idx >= tools.length) {
      rl.close()
      applyAdapters()
      return
    }
    rl.question(`  Set up ${tools[idx].label}? [y/N] `, ans => {
      selected[tools[idx].key] = /^y$/i.test(ans.trim())
      promptNext(idx + 1)
    })
  }

  function applyAdapters() {
    if (selected.claude) scaffoldClaudeMd(absProject)
    if (selected.cursor) {
      const n = emitCursorRules(modulesDir, absProject)
      console.log(`  → Generated ${n} Cursor rules in .cursor/rules/ ✓`)
    }
    if (selected.codex) {
      const r = emitCodexProject(modulesDir, absProject, path.join(installDir, 'hooks'))
      console.log(`  → Generated Codex AGENTS.md + ${r.skills} repo skills + hooks ✓`)
    }
    // Artifacts from tools dropped in v3.0.0. This is the path that reaches repos
    // `update` cannot see — cloned fresh, or set up on another machine.
    const backupRoot = path.join(require('os').homedir(), '.100xprism', 'removed-artifacts', backupStamp())
    const pruned = pruneDeprecatedArtifacts(absProject, backupRoot)
    if (pruned.removed > 0) {
      console.log(`  → Removed ${pruned.removed} file(s) from tools dropped in v3.0.0`)
      console.log(`     Backups: ${backupRoot}`)
    }
    if (pruned.failed > 0) {
      console.log(`  → ${pruned.failed} deprecated file(s) could not be removed and were left in place`)
    }

    addTrackedProject(absProject, trackedProjectsFile)
    console.log('\n✓ Project set up!')
  }

  promptNext(0)
}

// Mirror of adapters/lib/deprecated.sh for the native-Windows path, which never
// runs the shell scripts. Same three guarantees: ownership (our marker, in the
// header region only), containment (resolved path must stay inside the resolved
// project, so a symlinked .github cannot escape), and recoverability (copy to
// ~/.100xprism/removed-artifacts/<timestamp>/ before removing, and only remove
// if that copy landed).
const DEPRECATED_ARTIFACTS = [
  '.windsurfrules',
  'GEMINI.md',
  'ANTIGRAVITY.md',
  path.join('.github', 'copilot-instructions.md'),
]
const DEPRECATED_MARKER = 'Generated by 100xprism'
const DEPRECATED_MARKER_LINES = 10

// YYYYMMDD-HHMMSS-<pid>, matching `date +%Y%m%d-%H%M%S-$$` in deprecated.sh. The
// pid disambiguates two runs starting within the same second, which would
// otherwise share a root and let the later run overwrite the earlier backups.
function backupStamp() {
  const d = new Date()
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-` +
    `${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}-${process.pid}`
}

function pruneDeprecatedArtifacts(projectPath, backupRoot) {
  let removed = 0
  let failed = 0

  let realProject
  try { realProject = fs.realpathSync(projectPath) } catch { return { removed, failed } }

  // Key by the full resolved path, not the basename: /work/client/app and
  // /work/internal/app collide on basename, and the later backup would overwrite
  // the earlier one while both originals were deleted. Sanitising separators is
  // itself lossy (C:\a_b\c and C:\a\b_c both collapse), so percent-encode '%'
  // and '_' FIRST — after translating separators to '_', every underscore can
  // only have come from a separator. Injective outright; matches deprecated.sh.
  const slot = realProject
    .replace(/^[/\\]/, '')
    .replace(/%/g, '%25')
    .replace(/_/g, '%5F')
    .replace(/[/\\:]/g, '_')

  for (const rel of DEPRECATED_ARTIFACTS) {
    const target = path.join(projectPath, rel)
    // statSync is inside the try: the file can vanish between existsSync and
    // statSync, and a throw here would abort the whole init/update run.
    try {
      if (!fs.existsSync(target) || !fs.statSync(target).isFile()) continue
    } catch { continue }

    let realParent
    try { realParent = fs.realpathSync(path.dirname(target)) } catch { continue }
    if (realParent !== realProject && !realParent.startsWith(realProject + path.sep)) {
      console.log(`     skipped ${rel} (resolves outside the project)`)
      continue
    }

    let head
    try {
      head = fs.readFileSync(target, 'utf8').split('\n').slice(0, DEPRECATED_MARKER_LINES)
    } catch { continue }
    if (!head.some(line => line.includes(DEPRECATED_MARKER))) continue

    const backup = path.join(backupRoot, slot, rel)
    try {
      fs.mkdirSync(path.dirname(backup), { recursive: true })
      fs.copyFileSync(target, backup)
    } catch {
      console.log(`     FAILED to back up ${rel} — leaving it in place`)
      failed++
      continue
    }

    try {
      fs.unlinkSync(target)
      removed++
      console.log(`     removed ${rel}`)
    } catch {
      console.log(`     FAILED to remove ${rel}`)
      failed++
    }
  }

  return { removed, failed }
}

// Prunes across every project the installer has ever registered on this machine.
function pruneTrackedProjects(trackedFile, backupRoot) {
  if (!fs.existsSync(trackedFile)) return { removed: 0, failed: 0, projects: 0 }
  let removed = 0, failed = 0, projects = 0
  for (const line of fs.readFileSync(trackedFile, 'utf8').split('\n')) {
    const p = line.trim()
    if (!p || !fs.existsSync(p)) continue
    const r = pruneDeprecatedArtifacts(p, backupRoot)
    if (r.removed > 0) projects++
    removed += r.removed
    failed += r.failed
  }
  return { removed, failed, projects }
}

function updateWindows(installDir, checkOnly) {
  const fetchResult = spawnSync('git', ['-C', installDir, 'fetch', 'origin', 'main', '--quiet'], { stdio: 'inherit' })
  if (fetchResult.status !== 0) { console.error('git fetch failed'); process.exit(1) }

  const local  = spawnSync('git', ['-C', installDir, 'rev-parse', 'HEAD']).stdout.toString().trim()
  const remote = spawnSync('git', ['-C', installDir, 'rev-parse', 'origin/main']).stdout.toString().trim()

  // Reconcile projects even when the repo is already current. The v3 rollout
  // pulls the new code with the OLD process still running, so the first update
  // that installs pruning cannot also run it — without this, an existing Windows
  // install would never migrate until some unrelated future commit landed.
  // Mirrors the same fix on the shell path in update.sh.
  if (local === remote) {
    console.log('✓ Already up to date.')
    // NEVER reconcile under checkOnly: `100xprism check` is notify-only, and
    // reconciliation DELETES deprecated artifacts from the user's repositories.
    if (checkOnly) {
      console.log('  → --check-only: skipping project reconciliation (it would delete deprecated files)')
    } else {
      reconcileTrackedProjects()
    }
    return
  }

  if (checkOnly) { console.log('Update available. Run: 100xprism update'); return }

  const pullResult = spawnSync('git', ['-C', installDir, 'pull', '--rebase', 'origin', 'main', '--quiet'], { stdio: 'inherit' })
  if (pullResult.status !== 0) { console.error('git pull failed'); process.exit(1) }

  installGlobalWindows(installDir)
  reconcileTrackedProjects()
  console.log('✓ 100xprism updated!')
}

// One backup root for the whole run, so removals report a single location.
function reconcileTrackedProjects() {
  const { trackedProjectsFile } = require('../platform')
  const backupRoot = path.join(require('os').homedir(), '.100xprism', 'removed-artifacts', backupStamp())
  const pruned = pruneTrackedProjects(trackedProjectsFile, backupRoot)
  if (pruned.removed > 0) {
    console.log(`  → Pruned ${pruned.removed} deprecated file(s) across ${pruned.projects} project(s) ✓`)
    console.log(`     Backups: ${backupRoot}`)
    console.log("     These are usually committed — review with 'git status' and commit the deletions.")
  }
  if (pruned.failed > 0) {
    console.log(`  → ${pruned.failed} deprecated file(s) could not be removed and were left in place`)
    // Surface incomplete reconciliation to automation instead of exiting 0, but
    // via exitCode so the rest of the update (and any --dashboard launch) still
    // completes. Matches the shell path's non-zero exit.
    process.exitCode = 1
  }
}

module.exports = {
  parseFrontmatter,
  shortDescription,
  listModules,
  retentionOf,
  profilesOf,
  detectProfiles,
  activeProfiles,
  selectModules,
  userSkillsMode,
  splitByMode,
  renderResolver,
  writeCatalogBodies,
  renderCommandAlias,
  emitClaudeModules,
  emitCursorRules,
  pruneDeprecatedArtifacts,
  pruneTrackedProjects,
  renderCodexAgents,
  emitCodexProject,
  scaffoldClaudeMd,
  mergePluginsJson,
  addTrackedProject,
  installGlobalWindows,
  initProjectWindows,
  updateWindows,
}
