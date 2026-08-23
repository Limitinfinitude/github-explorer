import assert from 'node:assert/strict'
import test from 'node:test'

import { summarizeAcceptanceLedger } from '../src/lib/acceptanceLedger.ts'

test('acceptance ledger summarizes statuses and valid evidence', () => {
  const summary = summarizeAcceptanceLedger([
    {
      id: 1,
      text: '提供网页',
      status: 'passed',
      evidence: [{ type: 'file', ref: 'templates/index.html', valid: true }],
      reason: '',
    },
    {
      id: 2,
      text: '支持搜索',
      status: 'failed',
      evidence: [],
      reason: '未实现',
    },
    {
      id: 3,
      text: '提供预览',
      status: 'unverified',
      evidence: [{ type: 'check', ref: 'browser', valid: false }],
      reason: '证据不存在',
    },
  ])

  assert.deepEqual(summary, {
    passed: 1,
    failed: 1,
    unverified: 1,
    validEvidence: 1,
    total: 3,
  })
})

test('legacy messages without an acceptance ledger remain readable', () => {
  assert.deepEqual(summarizeAcceptanceLedger(undefined), {
    passed: 0,
    failed: 0,
    unverified: 0,
    validEvidence: 0,
    total: 0,
  })
})
