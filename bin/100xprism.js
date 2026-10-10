#!/usr/bin/env node
'use strict'

const HELP = `
Usage: 100xprism <command>

Commands:
  install    Clean old artifacts, then install global Claude Code setup
  init       Per-project setup — run from your project root
  update     Pull latest workflows and regenerate tracked projects
  check      Check for a newer version without applying
  slim       Shrink the always-on context: keep must-have skills, route the rest
  optimize   Apply the lean context policy (slim compatibility successor)
  audit      Inventory standing context and drift; --fix repairs dead hooks, v1 leftovers, missing catalog
  hermes     Install/refresh modules as Hermes/OpenClaw skills (auto-runs in install/update when Hermes is detected)
  tokens     Start/open the AI economics dashboard
  dashboard  Alias for tokens
  value      Print value report for a directory
  uninstall  Remove skills, aliases, hooks, catalog, links (--projects, --purge for more)

Examples:
  npm install -g 100xprism && 100xprism install
  cd my-project && 100xprism init
  100xprism update
  100xprism slim                  # slim this repo + user scope (reversible)
  100xprism slim --dry-run        # show what it would change
  100xprism slim --all-projects   # every project 100xprism has touched
  100xprism slim --skills=must    # most aggressive; --skills=all reverts
  100xprism audit --json           # machine-readable standing-context estimate
  100xprism optimize --all-projects
  100xprism install --dashboard  # optionally start dashboard after install
  100xprism tokens               # start and open dashboard
  100xprism tokens --no-open      # serve without opening browser
  100xprism value                 # report shipped outcomes for cwd
  100xprism uninstall
`.trimStart()

const [,, cmd, ...args] = process.argv

switch (cmd) {
  case 'install': require('../lib/install').run(args); break
  case 'init':    require('../lib/init').run(args);    break
  case 'update':  require('../lib/update').run(args);  break
  case 'check':   require('../lib/update').run(['--check-only']); break
  case 'slim':    require('../lib/slim').run(args);    break
  case 'optimize': require('../lib/slim').run(args);    break
  case 'audit': require('../lib/audit').run(args); break
  case 'hermes':  require('../lib/hermes').run();      break
  case 'tokens':
  case 'dashboard': require('../lib/tokens').runDashboard(args); break
  case 'value': require('../lib/tokens').runValue(args); break
  case 'uninstall': require('../lib/uninstall').run(args); break
  default:
    process.stdout.write(HELP)
    process.exit(cmd ? 1 : 0)
}
