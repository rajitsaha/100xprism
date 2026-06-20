'use strict'

// Verifies adapters/lib/modules.py `emit-claude-code` prunes stale 100x-dev
// artifacts on re-run (skills + slash-command aliases for removed/merged
// modules) while never touching the user's own hand-authored skills/commands.

const { test } = require('node:test')
const assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const REPO = path.resolve(__dirname, '..')
const MODULES_PY = path.join(REPO, 'adapters', 'lib', 'modules.py')

function emit(home) {
  return spawnSync('python3', [MODULES_PY, 'emit-claude-code'], {
    encoding: 'utf8',
    env: { ...process.env, HOME: home },
  })
}

test('emit-claude-code prunes a removed module but keeps user-authored skills/commands', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), '100x-cc-'))
  const skills = path.join(home, '.claude', 'skills')
  const commands = path.join(home, '.claude', 'commands')
  fs.mkdirSync(skills, { recursive: true })
  fs.mkdirSync(commands, { recursive: true })

  // A module merged away in this release (in REMOVED_MODULES) — must be pruned
  // even though this install predates the manifest/marker.
  fs.mkdirSync(path.join(skills, 'systems-architect'))
  fs.writeFileSync(path.join(skills, 'systems-architect', 'SKILL.md'), 'old\n')
  // A skill the user wrote themselves — must survive (no 100x-dev marker).
  fs.mkdirSync(path.join(skills, 'my-custom-skill'))
  fs.writeFileSync(path.join(skills, 'my-custom-skill', 'SKILL.md'), 'mine\n')
  // A command the user wrote themselves — must survive (no alias marker).
  fs.writeFileSync(path.join(commands, 'my-cmd.md'), 'mine\n')

  const r = emit(home)
  assert.equal(r.status, 0, r.stderr)

  assert.ok(!fs.existsSync(path.join(skills, 'systems-architect')), 'removed module pruned')
  assert.ok(fs.existsSync(path.join(skills, 'my-custom-skill')), 'user skill kept')
  assert.ok(fs.existsSync(path.join(commands, 'my-cmd.md')), 'user command kept')
  assert.ok(fs.existsSync(path.join(skills, 'enterprise-design')), 'current module written')
  // Each emitted skill carries the generation marker, and a manifest is written.
  assert.ok(fs.existsSync(path.join(skills, 'enterprise-design', '.100x-dev-generated')))
  const manifest = JSON.parse(fs.readFileSync(path.join(skills, '.100x-dev-manifest.json'), 'utf8'))
  assert.ok(manifest.skills.includes('enterprise-design'))
  assert.ok(!manifest.skills.includes('systems-architect'))
})

test('emit-claude-code prunes a future-removed module via manifest/marker', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), '100x-cc2-'))
  const skills = path.join(home, '.claude', 'skills')
  const commands = path.join(home, '.claude', 'commands')

  // First emit establishes the manifest + markers.
  assert.equal(emit(home).status, 0)

  // Simulate a module that existed last emit (marker + manifest + alias) but is
  // now gone upstream — not in the hardcoded REMOVED_MODULES list.
  fs.mkdirSync(path.join(skills, 'ghost-module'))
  fs.writeFileSync(path.join(skills, 'ghost-module', 'SKILL.md'), 'x\n')
  fs.writeFileSync(path.join(skills, 'ghost-module', '.100x-dev-generated'), 'gen\n')
  fs.writeFileSync(
    path.join(commands, 'ghost.md'),
    '---\ndescription: x\n---\n\n<!-- 100x-dev generated alias — regenerate, do not edit -->\n\nUse the `ghost-module` skill.\n',
  )
  const mPath = path.join(skills, '.100x-dev-manifest.json')
  const m = JSON.parse(fs.readFileSync(mPath, 'utf8'))
  m.skills.push('ghost-module')
  m.commands.push('ghost')
  fs.writeFileSync(mPath, JSON.stringify(m))

  assert.equal(emit(home).status, 0)
  assert.ok(!fs.existsSync(path.join(skills, 'ghost-module')), 'manifest-tracked skill pruned')
  assert.ok(!fs.existsSync(path.join(commands, 'ghost.md')), 'marker-tagged alias pruned')
})
