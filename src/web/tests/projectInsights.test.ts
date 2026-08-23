import assert from 'node:assert/strict'
import test from 'node:test'

import { processIdentityLabel, qualityState } from '../src/lib/projectInsights.ts'


test('process identity requires owned listener evidence', () => {
  assert.equal(processIdentityLabel({
    status: 'running', process_id: 'p1', launcher_pid: 42,
    listener_pids: [84], process_tree_pids: [42, 84], owned: true,
  }), '端口归属已确认')
  assert.equal(processIdentityLabel({
    status: 'running', process_id: 'p1', launcher_pid: 42,
  }), '进程运行中，端口尚未确认')
  assert.equal(processIdentityLabel({ status: 'orphaned', process_id: 'p1' }), '进程已失联')
})


test('quality state prioritizes false completion and incomplete risk', () => {
  assert.deepEqual(qualityState({ false_completion: true, false_incomplete: false }), {
    tone: 'danger', label: '完成状态不可信',
  })
  assert.deepEqual(qualityState({ false_completion: false, false_incomplete: true }), {
    tone: 'warning', label: '可能误判未完成',
  })
  assert.deepEqual(qualityState({ false_completion: false, false_incomplete: false }), {
    tone: 'success', label: '事实与终态一致',
  })
})
