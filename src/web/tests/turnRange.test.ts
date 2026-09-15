import assert from 'node:assert/strict'
import test from 'node:test'

import { removeTurn, turnRange } from '../src/lib/turnRange.ts'

const user = (id: string) => ({ id, role: 'user' as const, content: id, time: '' })
const assistant = (id: string) => ({ id, role: 'assistant' as const, content: id, time: '' })

test('删除 AI 回复时连同前面的用户提问一起删除', () => {
  const msgs = [user('u1'), assistant('a1'), user('u2'), assistant('a2')]
  const range = turnRange(msgs, 'a2')
  assert.deepEqual(range, { start: 2, end: 4 })
  assert.deepEqual(removeTurn(msgs, 'a2').map(m => m.id), ['u1', 'a1'])
})

test('删除用户提问时连同它的回复一起删除', () => {
  const msgs = [user('u1'), assistant('a1'), user('u2'), assistant('a2')]
  assert.deepEqual(removeTurn(msgs, 'u1').map(m => m.id), ['u2', 'a2'])
})

test('一轮中的多条回复一起删除', () => {
  const msgs = [user('u1'), assistant('a1'), assistant('a2'), user('u2')]
  assert.deepEqual(removeTurn(msgs, 'a1').map(m => m.id), ['u2'])
})

test('没有回复的用户消息只删除自己', () => {
  const msgs = [user('u1'), user('u2')]
  assert.deepEqual(removeTurn(msgs, 'u2').map(m => m.id), ['u1'])
})

test('开头的孤立 assistant 消息只删除自己', () => {
  const msgs = [assistant('a0'), user('u1'), assistant('a1')]
  assert.deepEqual(removeTurn(msgs, 'a0').map(m => m.id), ['u1', 'a1'])
})

test('找不到消息时原样返回', () => {
  const msgs = [user('u1'), assistant('a1')]
  assert.equal(removeTurn(msgs, 'missing').length, 2)
  assert.equal(turnRange(msgs, 'missing'), null)
})
