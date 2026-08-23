import test from 'node:test'
import assert from 'node:assert/strict'
import { historyToMessages } from '../src/lib/chatHistory.ts'

test('converts canonical session history into stable UI messages', () => {
  const messages = historyToMessages('session-1', [
    { role: 'user', content: '你好', timestamp: '2026-08-10 10:00:00' },
    { role: 'system', content: 'ignore', timestamp: '2026-08-10 10:00:01' },
    { role: 'assistant', content: '你好！', timestamp: '2026-08-10 10:00:02' },
  ])

  assert.deepEqual(messages.map(message => ({
    id: message.id,
    role: message.role,
    content: message.content,
    time: message.time,
  })), [
    { id: 'session-1:0', role: 'user', content: '你好', time: '2026-08-10 10:00:00' },
    { id: 'session-1:1', role: 'assistant', content: '你好！', time: '2026-08-10 10:00:02' },
  ])
})
