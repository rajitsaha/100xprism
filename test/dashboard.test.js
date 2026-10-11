'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const {
  DASHBOARD_URL,
  dashboardStartCommand,
  dashboardStartedMessage,
  dashboardStartInstructions,
} = require('../lib/dashboard')
const { reportScript } = require('../lib/tokens')

test('dashboard started message is reserved for actual start paths', () => {
  assert.equal(dashboardStartedMessage(), `📊 AI economics dashboard → ${DASHBOARD_URL}`)
})

test('dashboard default instructions say it is opt-in and provide start command', () => {
  assert.equal(dashboardStartCommand(), '100xprism tokens')

  const instructions = dashboardStartInstructions()
  assert.match(instructions, /not started by default/)
  assert.match(instructions, /Start and open it with:/)
  assert.match(instructions, /100xprism tokens/)
  assert.doesNotMatch(instructions, /dashboard → http:\/\/127\.0\.0\.1:8787/)
})

test('tokens JSON and tool-filter requests use the fast report path', () => {
  assert.equal(reportScript(['--json']), 'token_report.py')
  assert.equal(reportScript(['--tool', 'codex']), 'token_report.py')
  assert.equal(reportScript(['--oneline']), 'token_report.py')
  assert.equal(reportScript(['--print']), 'token-dashboard.py')
  assert.equal(reportScript([]), 'token-dashboard.py')
})
