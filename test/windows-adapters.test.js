'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('fs')
const os = require('os')
const path = require('path')
const {
  parseFrontmatter,
  emitClaudeModules,
  scaffoldClaudeMd,
  mergePluginsJson,
  emitCursorRules,
  pruneDeprecatedArtifacts,
  pruneTrackedProjects,
  emitCodexProject,
  addTrackedProject,
} = require('../lib/adapters/windows')

function makeTmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), '100x-test-'))
}

// Build a fake modules/ dir: each entry is { slug, frontmatter, body }.
function fakeModules(dir, mods) {
  for (const m of mods) {
    const md = path.join(dir, m.slug)
    fs.mkdirSync(md, { recursive: true })
    const fm = Object.entries(m.fm || {}).map(([k, v]) => `${k}: ${v}`).join('\n')
    fs.writeFileSync(path.join(md, 'SKILL.md'), `---\n${fm}\n---\n\n${m.body || 'body'}\n`)
  }
}

test('emitClaudeModules writes skills + slash aliases from modules/', () => {
  const modulesDir = makeTmpDir(), skills = makeTmpDir(), commands = makeTmpDir()
  fakeModules(modulesDir, [
    { slug: 'gate', fm: { name: 'gate', description: 'Quality gate.', slash_command: '/gate' } },
    { slug: 'fix-bugs', fm: { name: 'fix-bugs', description: 'Fix bugs.', slash_command: '/fix' } },
    { slug: 'copywriting', fm: { name: 'copywriting', description: 'Write copy.' } }, // no slash
    { slug: '_lib', fm: {}, body: '' }, // shared-ref dir — but has SKILL.md here; skip via missing? keep simple
  ])
  // _lib in the real repo has no SKILL.md; emulate that:
  fs.rmSync(path.join(modulesDir, '_lib', 'SKILL.md'))

  const r = emitClaudeModules(modulesDir, skills, commands)
  assert.equal(r.skills, 2, 'must-have module plus resolver emitted')
  assert.ok(fs.existsSync(path.join(skills, 'gate', 'SKILL.md')))
  assert.ok(fs.existsSync(path.join(skills, 'gate', '.100xprism-generated')), 'marker written')
  // An alias is written ONLY when the command name differs from the slug. Claude
  // Code already exposes every skill as /<slug>, so a same-name alias double-lists
  // the module and pays its description twice. Matches cmd_emit_claude_code in
  // adapters/lib/modules.py, which the JS path used to diverge from.
  assert.ok(!fs.existsSync(path.join(commands, 'gate.md')), 'no alias when /gate === slug')
  assert.ok(!fs.existsSync(path.join(commands, 'fix.md')), 'routed alias is not resident')
  assert.ok(fs.existsSync(path.join(commands, '100x.md')), 'one generic routed command is resident')
  assert.ok(!fs.existsSync(path.join(commands, 'copywriting.md')), 'no alias without slash_command')
  const manifest = JSON.parse(fs.readFileSync(path.join(skills, '.100xprism-manifest.json'), 'utf8'))
  assert.deepEqual(manifest.skills, ['100x-resolver', 'gate'])
  assert.deepEqual(manifest.commands, ['100x'])
})

test('emitClaudeModules prunes removed modules but keeps user-authored skills/commands', () => {
  const modulesDir = makeTmpDir(), skills = makeTmpDir(), commands = makeTmpDir()
  fakeModules(modulesDir, [{ slug: 'gate', fm: { name: 'gate', description: 'Quality gate.', slash_command: '/gate' } }])

  // Pre-existing: a merged-away module (in REMOVED_MODULES, no marker), a user skill, a user command.
  fs.mkdirSync(path.join(skills, 'systems-architect'))
  fs.writeFileSync(path.join(skills, 'systems-architect', 'SKILL.md'), 'old')
  fs.mkdirSync(path.join(skills, 'my-skill'))
  fs.writeFileSync(path.join(skills, 'my-skill', 'SKILL.md'), 'mine')
  fs.writeFileSync(path.join(commands, 'my-cmd.md'), 'mine')

  const r = emitClaudeModules(modulesDir, skills, commands)
  assert.ok(r.prunedSkills >= 1)
  assert.ok(!fs.existsSync(path.join(skills, 'systems-architect')), 'removed module pruned')
  assert.ok(fs.existsSync(path.join(skills, 'my-skill')), 'user skill kept')
  assert.ok(fs.existsSync(path.join(commands, 'my-cmd.md')), 'user command kept')

  // A future-removed module tracked only via manifest/marker is pruned on re-run.
  fs.mkdirSync(path.join(skills, 'ghost'))
  fs.writeFileSync(path.join(skills, 'ghost', 'SKILL.md'), 'x')
  fs.writeFileSync(path.join(skills, 'ghost', '.100xprism-generated'), 'gen')
  emitClaudeModules(modulesDir, skills, commands)
  assert.ok(!fs.existsSync(path.join(skills, 'ghost')), 'marker-tagged orphan pruned')
})

test('scaffoldClaudeMd writes a router-shaped CLAUDE.md plus an on-demand config', () => {
  const projectDir = makeTmpDir()
  scaffoldClaudeMd(projectDir)
  const content = fs.readFileSync(path.join(projectDir, 'CLAUDE.md'), 'utf8')
  assert.ok(content.includes(path.basename(projectDir)))
  assert.ok(content.includes('## Project Overview'))
  assert.ok(content.includes('## Key Conventions'))
  assert.ok(content.includes('## Reference docs (read on demand)'))
  assert.ok(content.includes('## Rules'))

  // The always-on file must stay small; detail belongs in docs/ behind the router.
  assert.ok(content.length < 1800, `scaffold is ${content.length} bytes — keep it lean`)

  // Machine-readable config moved out of the always-on file. Keys must stay
  // flush-left: /db and friends match them with an anchored grep.
  const cfg = fs.readFileSync(path.join(projectDir, '.claude', '100xprism.yml'), 'utf8')
  for (const key of ['# engine:', '# gcp_project:', '# production_url:', '# security_exceptions:']) {
    assert.ok(cfg.includes(`\n${key}`), `${key} present at column 0`)
  }
  assert.ok(!content.includes('## Database'), 'db config no longer in CLAUDE.md')
})

test('scaffoldClaudeMd does not clobber an existing 100xprism.yml', () => {
  const projectDir = makeTmpDir()
  fs.mkdirSync(path.join(projectDir, '.claude'), { recursive: true })
  fs.writeFileSync(path.join(projectDir, '.claude', '100xprism.yml'), 'engine: postgres\n')
  scaffoldClaudeMd(projectDir)
  assert.equal(
    fs.readFileSync(path.join(projectDir, '.claude', '100xprism.yml'), 'utf8'),
    'engine: postgres\n',
  )
})

test('scaffoldClaudeMd skips if CLAUDE.md already exists', () => {
  const projectDir = makeTmpDir()
  fs.writeFileSync(path.join(projectDir, 'CLAUDE.md'), 'existing')
  scaffoldClaudeMd(projectDir)
  assert.equal(fs.readFileSync(path.join(projectDir, 'CLAUDE.md'), 'utf8'), 'existing')
})

test('scaffoldClaudeMd skips if .cursorrules already exists', () => {
  const projectDir = makeTmpDir()
  fs.writeFileSync(path.join(projectDir, '.cursorrules'), 'existing')
  scaffoldClaudeMd(projectDir)
  assert.ok(!fs.existsSync(path.join(projectDir, 'CLAUDE.md')))
})

test('mergePluginsJson adds plugins to settings.json', () => {
  const settingsFile = path.join(makeTmpDir(), 'settings.json')
  const pluginsFile = path.join(makeTmpDir(), 'plugins.json')
  fs.writeFileSync(settingsFile, JSON.stringify({ enabledPlugins: {} }))
  fs.writeFileSync(pluginsFile, JSON.stringify({ plugins: ['plugin-a', 'plugin-b'] }))
  mergePluginsJson(pluginsFile, settingsFile)
  const settings = JSON.parse(fs.readFileSync(settingsFile, 'utf8'))
  assert.equal(settings.enabledPlugins['plugin-a'], true)
  assert.equal(settings.enabledPlugins['plugin-b'], true)
})

test('mergePluginsJson is idempotent', () => {
  const settingsFile = path.join(makeTmpDir(), 'settings.json')
  const pluginsFile = path.join(makeTmpDir(), 'plugins.json')
  fs.writeFileSync(settingsFile, JSON.stringify({ enabledPlugins: { 'plugin-a': true } }))
  fs.writeFileSync(pluginsFile, JSON.stringify({ plugins: ['plugin-a'] }))
  mergePluginsJson(pluginsFile, settingsFile)
  const settings = JSON.parse(fs.readFileSync(settingsFile, 'utf8'))
  assert.equal(Object.keys(settings.enabledPlugins).length, 1)
})

test('mergePluginsJson removes a dropped managed plugin but keeps user plugins', () => {
  const settingsFile = path.join(makeTmpDir(), 'settings.json')
  const pluginsFile = path.join(makeTmpDir(), 'plugins.json')
  fs.writeFileSync(settingsFile, JSON.stringify({ enabledPlugins: { 'user-only': true } }))

  // First run: declare a + b (seed managed set, add both, remove nothing).
  fs.writeFileSync(pluginsFile, JSON.stringify({ plugins: ['plugin-a', 'plugin-b'] }))
  mergePluginsJson(pluginsFile, settingsFile)

  // Second run: drop plugin-b from plugins.json -> it should be removed.
  fs.writeFileSync(pluginsFile, JSON.stringify({ plugins: ['plugin-a'] }))
  mergePluginsJson(pluginsFile, settingsFile)

  const enabled = JSON.parse(fs.readFileSync(settingsFile, 'utf8')).enabledPlugins
  assert.equal('plugin-b' in enabled, false, 'dropped managed plugin removed')
  assert.equal(enabled['plugin-a'], true, 'still-declared plugin kept')
  assert.equal(enabled['user-only'], true, 'user-managed plugin preserved')
})

test('emitCursorRules writes one .mdc per module with tier-driven alwaysApply', () => {
  const modulesDir = makeTmpDir()
  const projectDir = makeTmpDir()
  fakeModules(modulesDir, [
    { slug: 'gate', fm: { name: 'gate', category: 'quality', tier: 'core', slash_command: '/gate', description: 'Quality gate.' }, body: 'GATE BODY' },
    { slug: 'copywriting', fm: { name: 'copywriting', category: 'marketing', tier: 'on-demand', description: 'Write copy.' }, body: 'COPY BODY' },
  ])
  fs.writeFileSync(path.join(projectDir, '.100xprism.json'), JSON.stringify({ profiles: ['all'] }))
  const count = emitCursorRules(modulesDir, projectDir)
  assert.equal(count, 2, 'wrote a rule per module')

  const rulesDir = path.join(projectDir, '.cursor', 'rules')
  const gate = fs.readFileSync(path.join(rulesDir, 'gate.mdc'), 'utf8')
  const copy = fs.readFileSync(path.join(rulesDir, 'copywriting.mdc'), 'utf8')

  assert.match(gate, /alwaysApply: true/, 'core module is resident')
  assert.match(copy, /alwaysApply: false/, 'on-demand module is agent-requested')
  // Bodies ship in every rule file — Cursor loads them, it does not inline them
  // into an always-on blob the way the removed concat adapters did.
  assert.ok(gate.includes('GATE BODY'), 'core body present in its own rule file')
  assert.ok(copy.includes('COPY BODY'), 'on-demand body present in its own rule file')
  assert.match(gate, /generated by 100xprism/, 'carries the generation marker used for pruning')
})

// Regression: the stale-rule fixture used to be hand-written with the marker on
// line 5, while the real emitter puts it on line 7 — so the test passed against a
// file the emitter never produces, and pruning was broken in production for every
// removed module. This test now prunes REAL emitter output: generate, drop a
// module, re-emit, and require the orphan to be gone.
test('emitCursorRules prunes real generated rules for modules that disappeared', () => {
  const modulesDir = makeTmpDir()
  const projectDir = makeTmpDir()
  const rulesDir = path.join(projectDir, '.cursor', 'rules')

  fakeModules(modulesDir, [
    { slug: 'gate', fm: { name: 'gate', category: 'quality', tier: 'core', slash_command: '/gate', description: 'Quality gate.' }, body: 'GATE BODY' },
    { slug: 'copywriting', fm: { name: 'copywriting', category: 'marketing', tier: 'on-demand', description: 'Write copy.' }, body: 'COPY BODY' },
  ])
  fs.writeFileSync(path.join(projectDir, '.100xprism.json'), JSON.stringify({ profiles: ['all'] }))
  emitCursorRules(modulesDir, projectDir)
  assert.ok(fs.existsSync(path.join(rulesDir, 'copywriting.mdc')), 'precondition: rule was generated')

  // Guard the exact off-by-one that caused the bug: the marker must fall inside
  // the window the pruner actually scans.
  const generated = fs.readFileSync(path.join(rulesDir, 'copywriting.mdc'), 'utf8')
  const markerLine = generated.split('\n').findIndex(l => l.includes('generated by 100xprism')) + 1
  assert.ok(markerLine > 0, 'generated rule carries the marker')
  assert.ok(markerLine <= 10, `marker on line ${markerLine} must be within the 10-line prune window`)

  // The module goes away upstream; a user-authored rule sits alongside it.
  fs.rmSync(path.join(modulesDir, 'copywriting'), { recursive: true })
  fs.writeFileSync(path.join(rulesDir, 'mine.mdc'), '---\ndescription: hand written\n---\n\nmy own rule\n')
  emitCursorRules(modulesDir, projectDir)

  assert.ok(!fs.existsSync(path.join(rulesDir, 'copywriting.mdc')), 'orphaned generated rule removed')
  assert.ok(fs.existsSync(path.join(rulesDir, 'mine.mdc')), 'user-authored rule preserved')
  assert.ok(fs.existsSync(path.join(rulesDir, 'gate.mdc')), 'current module still written')
})

test('emitCodexProject emits Codex-native repo skills and portable hooks', () => {
  const modulesDir = makeTmpDir()
  const projectDir = makeTmpDir()
  const hooksDir = makeTmpDir()
  fakeModules(modulesDir, [
    { slug: 'gate', fm: { name: 'gate', category: 'quality', tier: 'core', slash_command: '/gate', description: 'Quality gate.' }, body: 'GATE BODY' },
    { slug: 'copywriting', fm: { name: 'copywriting', category: 'marketing', tier: 'on-demand', description: 'Write copy.' }, body: 'COPY BODY' },
  ])
  fs.writeFileSync(path.join(hooksDir, 'hooks.manifest.json'), JSON.stringify({
    hooks: [{
      id: 'gate-on-commit',
      event: 'PreToolUse',
      matcher: 'Bash',
      script: 'pretooluse-gate.py',
      default: true,
      description: 'Block git commit/push unless /gate passed.',
    }],
  }))

  const previous = process.env.PRISM_HOOKS
  process.env.PRISM_HOOKS = '1'
  let r
  try {
    r = emitCodexProject(modulesDir, projectDir, hooksDir)
  } finally {
    if (previous === undefined) delete process.env.PRISM_HOOKS
    else process.env.PRISM_HOOKS = previous
  }

  assert.equal(r.skills, 2)
  assert.ok(fs.existsSync(path.join(projectDir, 'AGENTS.md')))
  assert.ok(fs.existsSync(path.join(projectDir, '.agents', 'skills', 'gate', 'SKILL.md')))
  assert.ok(fs.existsSync(path.join(projectDir, '.agents', 'skills', '100x-resolver', 'SKILL.md')))
  assert.ok(fs.existsSync(path.join(projectDir, '.agents', '100xprism-catalog', 'copywriting', 'SKILL.md')))
  const hooksText = fs.readFileSync(path.join(projectDir, '.codex', 'hooks.json'), 'utf8')
  assert.match(hooksText, /\.codex\/100xprism-hooks\/run-hook\.py/)
  assert.doesNotMatch(hooksText, new RegExp(modulesDir.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  assert.ok(fs.existsSync(path.join(projectDir, '.codex', '100xprism-hooks', 'run-hook.py')))
})

test('Windows and Python Codex emitters write the same hook wrapper template', () => {
  const repo = path.join(__dirname, '..')
  const modulesDir = path.join(repo, 'modules')
  const projectDir = makeTmpDir()
  const pyProjectDir = makeTmpDir()
  const hooksDir = path.join(repo, 'hooks')

  emitCodexProject(modulesDir, projectDir, hooksDir)
  require('node:child_process').execFileSync(
    'python3',
    [path.join(repo, 'adapters', 'lib', 'modules.py'), 'emit-codex', pyProjectDir],
    { cwd: repo, encoding: 'utf8' },
  )

  const jsWrapper = fs.readFileSync(path.join(projectDir, '.codex', '100xprism-hooks', 'run-hook.py'), 'utf8')
  const pyWrapper = fs.readFileSync(path.join(pyProjectDir, '.codex', '100xprism-hooks', 'run-hook.py'), 'utf8')
  assert.equal(jsWrapper, pyWrapper)
})

test('addTrackedProject writes path to file', () => {
  const trackedFile = path.join(makeTmpDir(), 'tracked-projects')
  addTrackedProject('/some/project', trackedFile)
  assert.ok(fs.readFileSync(trackedFile, 'utf8').includes('/some/project'))
})

test('addTrackedProject is idempotent', () => {
  const trackedFile = path.join(makeTmpDir(), 'tracked-projects')
  addTrackedProject('/some/project', trackedFile)
  addTrackedProject('/some/project', trackedFile)
  const lines = fs.readFileSync(trackedFile, 'utf8').trim().split('\n')
  assert.equal(lines.filter(l => l === '/some/project').length, 1)
})

// The native-Windows path never runs adapters/lib/deprecated.sh, so the JS mirror
// must enforce the same three guarantees. Without these, the CHANGELOG's
// "migration is automatic" claim is false on Windows.
test('pruneDeprecatedArtifacts mirrors the shell guarantees on Windows', () => {
  const project = makeTmpDir()
  const backupRoot = makeTmpDir()
  const header = '# 100x Dev — Modules\n# Generated by 100xprism\n\nbody\n'

  fs.writeFileSync(path.join(project, '.windsurfrules'), header)
  fs.writeFileSync(path.join(project, 'GEMINI.md'), '# hand written, not ours\n')
  fs.writeFileSync(path.join(project, 'CLAUDE.md'), '# supported tool\n')

  const r = pruneDeprecatedArtifacts(project, backupRoot)

  assert.equal(r.removed, 1, 'only the generated artifact was removed')
  assert.equal(r.failed, 0)
  assert.ok(!fs.existsSync(path.join(project, '.windsurfrules')), 'generated file removed')
  assert.ok(fs.existsSync(path.join(project, 'GEMINI.md')), 'hand-written file preserved')
  assert.ok(fs.existsSync(path.join(project, 'CLAUDE.md')), 'supported tool untouched')

  // Located by search rather than by a hard-coded layout, so the assertion stays
  // valid as the backup keying changes (it moved from basename to full path).
  const found = []
  const walk = d => fs.readdirSync(d, { withFileTypes: true }).forEach(e => {
    const p = path.join(d, e.name)
    e.isDirectory() ? walk(p) : (e.name === '.windsurfrules' && found.push(p))
  })
  walk(backupRoot)
  assert.equal(found.length, 1, 'removal was backed up first')
  assert.equal(fs.readFileSync(found[0], 'utf8'), header, 'backup is byte-identical')
})

test('pruneTrackedProjects walks every registered project', () => {
  const a = makeTmpDir(), b = makeTmpDir(), gone = path.join(makeTmpDir(), 'deleted')
  const backupRoot = makeTmpDir()
  const header = '# 100x Dev\n# Generated by 100xprism\n\nbody\n'
  fs.writeFileSync(path.join(a, 'GEMINI.md'), header)
  fs.writeFileSync(path.join(b, 'ANTIGRAVITY.md'), header)

  const trackedFile = path.join(makeTmpDir(), 'tracked-projects')
  fs.writeFileSync(trackedFile, `${a}\n${gone}\n\n${b}\n`)

  const r = pruneTrackedProjects(trackedFile, backupRoot)

  assert.equal(r.removed, 2, 'pruned across both live projects')
  assert.equal(r.projects, 2)
  assert.equal(r.failed, 0, 'a deleted/blank tracked path is skipped, not counted as failure')
})

// Regression: parseFrontmatter tested for a literal '---\n', but Git on Windows
// checks out CRLF by default and Node returns those bytes verbatim — so on the
// one platform this file exists to serve, every module parsed as empty
// frontmatter with the whole SKILL.md as its body. Python's read_text() does
// universal-newline translation, which is why only the JS side broke.
test('parseFrontmatter handles CRLF checkouts', () => {
  const { fm, body } = parseFrontmatter('---\r\nname: gate\r\ntier: core\r\n---\r\n\r\nBODY\r\n')
  assert.equal(fm.name, 'gate', 'frontmatter parsed from a CRLF file')
  assert.equal(fm.tier, 'core')
  assert.match(body, /BODY/)
})

test('emitCursorRules produces correct rules from a CRLF module tree', () => {
  const modulesDir = makeTmpDir()
  const projectDir = makeTmpDir()
  const md = path.join(modulesDir, 'gate')
  fs.mkdirSync(md, { recursive: true })
  fs.writeFileSync(path.join(md, 'SKILL.md'),
    '---\r\nname: gate\r\ncategory: quality\r\ntier: core\r\ndescription: Quality gate.\r\n---\r\n\r\nGATE BODY\r\n')

  emitCursorRules(modulesDir, projectDir)
  const rule = fs.readFileSync(path.join(projectDir, '.cursor', 'rules', 'gate.mdc'), 'utf8')

  assert.match(rule, /description: Quality gate\./, 'description survives CRLF')
  assert.match(rule, /alwaysApply: true/, 'core tier survives CRLF')
  assert.ok(!rule.includes('name: gate'), 'frontmatter not leaked into the body')
})

test('pruneDeprecatedArtifacts enforces containment and the header window', () => {
  const project = makeTmpDir()
  const backupRoot = makeTmpDir()
  const outside = makeTmpDir()
  fs.writeFileSync(path.join(outside, 'copilot-instructions.md'), '# Generated by 100xprism\n')
  fs.symlinkSync(outside, path.join(project, '.github'), 'dir')

  // Marker present, but far below the header region: not ours to delete.
  const deepMarker = '# mine\n' + 'filler\n'.repeat(20) + 'Generated by 100xprism\n'
  fs.writeFileSync(path.join(project, 'GEMINI.md'), deepMarker)

  const r = pruneDeprecatedArtifacts(project, backupRoot)

  assert.equal(r.removed, 0, 'neither the escaping symlink nor the deep marker authorised a delete')
  assert.ok(fs.existsSync(path.join(outside, 'copilot-instructions.md')), 'file outside the project survives')
  assert.equal(fs.readFileSync(path.join(project, 'GEMINI.md'), 'utf8'), deepMarker)
})

test('pruneDeprecatedArtifacts keys backups by full path, not basename', () => {
  const root = makeTmpDir()
  const backupRoot = makeTmpDir()
  const a = path.join(root, 'client', 'app')
  const b = path.join(root, 'internal', 'app')
  for (const [dir, mark] of [[a, 'AAA'], [b, 'BBB']]) {
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(path.join(dir, 'GEMINI.md'), `# Generated by 100xprism\n${mark}\n`)
  }

  pruneDeprecatedArtifacts(a, backupRoot)
  pruneDeprecatedArtifacts(b, backupRoot)

  const found = []
  const walk = d => fs.readdirSync(d, { withFileTypes: true }).forEach(e => {
    const p = path.join(d, e.name)
    e.isDirectory() ? walk(p) : (e.name === 'GEMINI.md' && found.push(fs.readFileSync(p, 'utf8')))
  })
  walk(backupRoot)

  assert.equal(found.length, 2, 'both backups survive despite the shared basename')
  assert.ok(found.join('|').includes('AAA') && found.join('|').includes('BBB'))
})

test('Windows slot keys are injective across separator/underscore variants', () => {
  // Matches the shell test: sanitising separators is lossy, so C:\a_b\c and
  // C:\a\b_c (or /tmp/a_b/c and /tmp/a/b_c) must not share a backup slot.
  const root = makeTmpDir()
  const backupRoot = makeTmpDir()
  const a = path.join(root, 'a_b', 'c')
  const b = path.join(root, 'a', 'b_c')
  for (const [dir, mark] of [[a, 'AAA'], [b, 'BBB']]) {
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(path.join(dir, 'GEMINI.md'), `# Generated by 100xprism\n${mark}\n`)
  }

  pruneDeprecatedArtifacts(a, backupRoot)
  pruneDeprecatedArtifacts(b, backupRoot)

  const found = []
  const walk = d => fs.readdirSync(d, { withFileTypes: true }).forEach(e => {
    const p = path.join(d, e.name)
    e.isDirectory() ? walk(p) : (e.name === 'GEMINI.md' && found.push(fs.readFileSync(p, 'utf8')))
  })
  walk(backupRoot)

  assert.equal(found.length, 2, 'lossy sanitisation must not merge the two slots')
  assert.ok(found.join('|').includes('AAA') && found.join('|').includes('BBB'))
})

test('pruneTrackedProjects reports failures instead of claiming success', () => {
  // A tracked path that exists but whose artifact cannot be backed up must be
  // counted as a failure and left in place, not silently reported as pruned.
  const project = makeTmpDir()
  fs.writeFileSync(path.join(project, 'GEMINI.md'), '# Generated by 100xprism\nbody\n')
  const trackedFile = path.join(makeTmpDir(), 'tracked-projects')
  fs.writeFileSync(trackedFile, project + '\n')

  // A file where the backup root must be a directory makes mkdir/copy fail.
  const blocked = path.join(makeTmpDir(), 'not-a-dir')
  fs.writeFileSync(blocked, 'x')

  const r = pruneTrackedProjects(trackedFile, blocked)

  assert.equal(r.removed, 0, 'nothing removed when the backup cannot be written')
  assert.equal(r.failed, 1, 'failure counted')
  assert.ok(fs.existsSync(path.join(project, 'GEMINI.md')), 'original left in place')
})

test('emitCodexProject never overwrites a hand-written AGENTS.md', () => {
  const modulesDir = makeTmpDir()
  const projectDir = makeTmpDir()
  const hooksDir = makeTmpDir()
  fakeModules(modulesDir, [
    { slug: 'gate', fm: { name: 'gate', category: 'quality', tier: 'core', slash_command: '/gate', description: 'Quality gate.' }, body: 'GATE BODY' },
  ])
  fs.writeFileSync(path.join(hooksDir, 'hooks.manifest.json'), JSON.stringify({ hooks: [] }))
  const agentsPath = path.join(projectDir, 'AGENTS.md')
  fs.writeFileSync(agentsPath, '# My repo contract\n')
  emitCodexProject(modulesDir, projectDir, hooksDir)
  assert.equal(fs.readFileSync(agentsPath, 'utf8'), '# My repo contract\n')

  const mentions = '# My repo contract\n\nSee `# Generated by 100xprism` headers.\n'
  fs.writeFileSync(agentsPath, mentions)
  emitCodexProject(modulesDir, projectDir, hooksDir)
  assert.equal(fs.readFileSync(agentsPath, 'utf8'), mentions, 'a marker mention is not proof of ownership')

  fs.rmSync(agentsPath)
  emitCodexProject(modulesDir, projectDir, hooksDir)
  fs.appendFileSync(agentsPath, 'stale line\n')
  emitCodexProject(modulesDir, projectDir, hooksDir)
  assert.doesNotMatch(fs.readFileSync(agentsPath, 'utf8'), /stale line/)
})
