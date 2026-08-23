import assert from 'node:assert/strict'
import test from 'node:test'

import { isRecoverableTaskStatus } from '../src/lib/taskRecovery.ts'


test('restores every nonterminal task state after remount', () => {
  for (const status of ['pending', 'queued', 'running', 'waiting_approval']) {
    assert.equal(isRecoverableTaskStatus(status), true, status)
  }
  for (const status of ['completed', 'incomplete', 'failed', 'cancelled', 'interrupted']) {
    assert.equal(isRecoverableTaskStatus(status), false, status)
  }
})
