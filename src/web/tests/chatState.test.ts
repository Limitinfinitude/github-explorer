import assert from 'node:assert/strict'
import test from 'node:test'

import { appendNewChat, ensureChatSessionId, ensureSessionChat } from '../src/lib/chatState.ts'

test('ensureChatSessionId keeps a valid unique session id', () => {
  const chat = { id: 3, title: 't', sessionId: 'session-3-1000', messages: [], created: 1 }
  assert.equal(ensureChatSessionId(chat).sessionId, 'session-3-1000')
})

test('ensureChatSessionId replaces missing or default session id', () => {
  const missing = { id: 4, title: 't', sessionId: '', messages: [], created: 1 }
  const fromDefault = { id: 5, title: 't', sessionId: 'default', messages: [], created: 1 }
  const fixedMissing = ensureChatSessionId(missing)
  const fixedDefault = ensureChatSessionId(fromDefault)
  assert.ok(fixedMissing.sessionId.startsWith('session-4-'))
  assert.notEqual(fixedMissing.sessionId, '')
  assert.ok(fixedDefault.sessionId.startsWith('session-5-'))
  assert.notEqual(fixedDefault.sessionId, 'default')
})

test('new task is appended as the active empty session', () => {
  const previous = [{
    id: 7,
    title: '旧任务',
    sessionId: 'session-7',
    messages: [{ id: 'm1', role: 'user' as const, content: '旧消息', time: '2026-08-13T00:00:00Z' }],
    created: 1,
  }]

  const result = appendNewChat(previous, 8, 1000)

  assert.equal(result.activeChatId, 8)
  assert.equal(result.chats.length, 2)
  assert.deepEqual(result.chats[1], {
    id: 8,
    title: '新对话',
    sessionId: 'session-8-1000',
    messages: [],
    created: 1000,
  })
})


test('project session reuses one chat instead of creating duplicates', () => {
  const existing = [{
    id: 7, title: 'demo 项目', sessionId: 'project-session-demo', messages: [], created: 1,
  }]

  const reused = ensureSessionChat(existing, 8, 1000, 'project-session-demo', 'new title')
  const created = ensureSessionChat(existing, 8, 1000, 'project-session-other', 'other 项目')

  assert.equal(reused.activeChatId, 7)
  assert.equal(reused.chats.length, 1)
  assert.equal(created.activeChatId, 8)
  assert.equal(created.chats[1].sessionId, 'project-session-other')
  assert.equal(created.chats[1].title, 'other 项目')
})


test('project action appears as the user message when its session opens', () => {
  const message = {
    id: 'project-action-1', role: 'user' as const, content: '项目体检', time: '2026-08-14T10:00:00Z',
  }

  const result = ensureSessionChat(
    [], 8, 1000, 'project-session-demo', 'demo 项目', message,
  )

  assert.deepEqual(result.chat.messages, [message])
  assert.deepEqual(result.chats[0].messages, [message])
  assert.equal(result.chat.title, 'demo 项目')
})
