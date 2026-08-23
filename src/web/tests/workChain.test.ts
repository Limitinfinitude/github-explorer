import assert from 'node:assert/strict'
import test from 'node:test'

import { summarizeWorkChain } from '../src/lib/workChain.ts'

test('tool steps are grouped into readable stages and repeated tools are counted', () => {
  const summary = summarizeWorkChain([
    { icon: 'tool', text: 'list_directory(...) 完成', done: true },
    { icon: 'tool', text: 'list_directory(...) 完成', done: true },
    { icon: 'tool', text: 'list_directory(...) 完成', done: true },
    { icon: 'tool', text: 'edit_files(...) 完成', done: true },
    { icon: 'tool', text: 'read_file(...) 完成', done: true },
    { icon: 'tool', text: 'start_process(...) 完成', done: true },
    { icon: 'tool', text: 'check_port(...) 完成', done: true },
    { icon: 'tool', text: 'wait_http(...) 完成', done: true },
    { icon: 'tool', text: 'stop_process(...) 完成', done: true },
  ])

  assert.deepEqual(summary.groups.map(group => [group.label, group.count]), [
    ['读取上下文', 4],
    ['修改文件', 1],
    ['管理服务', 2],
    ['检查服务', 2],
  ])
  assert.deepEqual(summary.tools.map(tool => [tool.name, tool.count]), [
    ['list_directory', 3],
    ['edit_files', 1],
    ['read_file', 1],
    ['start_process', 1],
    ['check_port', 1],
    ['wait_http', 1],
    ['stop_process', 1],
  ])
  assert.equal(summary.completed, 9)
  assert.equal(summary.failed, 0)
})

test('failed and unknown steps remain visible', () => {
  const summary = summarizeWorkChain([
    { icon: 'tool', text: 'run_command(...) 失败', done: true },
    { icon: 'activity', text: '重新规划', done: true },
  ])

  assert.equal(summary.failed, 1)
  assert.equal(summary.tools[0].failed, 1)
  assert.equal(summary.tools[1].label, '重新规划')
})

test('recovered failures stay visible without counting as final failures', () => {
  const summary = summarizeWorkChain([
    {
      icon: 'tool', text: 'run_command(...) 失败', done: true,
      callId: 'call-failed', toolName: 'run_command', status: 'failed',
      recoveredByCallId: 'call-retry',
    },
    {
      icon: 'tool', text: 'run_command(...) 完成', done: true,
      callId: 'call-retry', toolName: 'run_command', status: 'succeeded',
    },
  ])

  assert.equal(summary.failed, 0)
  assert.equal(summary.recovered, 1)
  assert.equal(summary.tools[0].failed, 0)
  assert.equal(summary.tools[0].recovered, 1)
  assert.equal(summary.groups[0].failed, 0)
  assert.equal(summary.groups[0].recovered, 1)
})

test('unrecovered structured failure remains a final failure', () => {
  const summary = summarizeWorkChain([
    {
      icon: 'tool', text: 'run_command(...) 失败', done: true,
      callId: 'call-failed', toolName: 'run_command', status: 'failed',
    },
  ])

  assert.equal(summary.failed, 1)
  assert.equal(summary.recovered, 0)
  assert.equal(summary.tools[0].failed, 1)
})
