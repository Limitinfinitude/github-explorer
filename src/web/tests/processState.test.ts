import assert from 'node:assert/strict'
import test from 'node:test'

import { reconcileProcesses } from '../src/lib/processState.ts'

test('missing live process turns a persisted running snapshot into orphaned', () => {
  const result = reconcileProcesses([
    { processId: 'old', status: 'running', pid: 123 },
    { processId: 'done', status: 'exited', returncode: 1 },
  ], [])

  assert.deepEqual(result.map(process => [process.processId, process.status]), [
    ['old', 'orphaned'],
    ['done', 'exited'],
  ])
})

test('live process snapshots override persisted state without losing other terminal records', () => {
  const result = reconcileProcesses([
    { processId: 'live', status: 'running', cwd: 'C:/old' },
    { processId: 'stopped', status: 'stopped' },
  ], [
    { processId: 'live', status: 'exited', returncode: 0, cwd: 'C:/new' },
  ])

  assert.deepEqual(result, [
    { processId: 'live', status: 'exited', returncode: 0, cwd: 'C:/new' },
    { processId: 'stopped', status: 'stopped' },
  ])
})
