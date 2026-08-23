import assert from 'node:assert/strict'
import test from 'node:test'

import { localCoverageLabels } from '../src/lib/observability.ts'

test('local observability coverage is rendered in stable Chinese labels', () => {
  assert.deepEqual(
    localCoverageLabels(['model', 'tool', 'approval', 'file', 'verification', 'process', 'terminal']),
    ['模型', '工具', '审批', '文件', '验证', '进程', '终态'],
  )
})

test('new local coverage kinds remain visible without a frontend release', () => {
  assert.deepEqual(localCoverageLabels(['model', 'custom-event']), ['模型', 'custom-event'])
})
