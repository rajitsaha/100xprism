#!/usr/bin/env node
'use strict'

const HELP = `
Usage: 100xprism <command>

Commands:
  install    Global setup — copy workflows to ~/.claude/commands/, install plugins
  init       Per-project setup — run from your project root
  update     Pull latest workflows and regenerate tracked projects
  check      Check for a newer version without applying

Examples:
  npm install -g 100xprism && 100xprism install
  cd my-project && 100xprism init
  100xprism update
`.trimStart()

const [,, cmd, ...args] = process.argv

switch (cmd) {
  case 'install': require('../lib/install').run(args); break
  case 'init':    require('../lib/init').run(args);    break
  case 'update':  require('../lib/update').run(args);  break
  case 'check':   require('../lib/update').run(['--check-only']); break
  default:
    process.stdout.write(HELP)
    process.exit(cmd ? 1 : 0)
}
