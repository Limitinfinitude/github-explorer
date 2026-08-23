import assert from 'node:assert/strict'
import test from 'node:test'

import { api } from '../src/lib/api.ts'


test('starts an agent task before subscribing to replayable events', async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = []
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (input, init) => {
    const url = String(input)
    calls.push({ url, init })
    if (url === '/api/agent/tasks/start') {
      return new Response(JSON.stringify({
        task_id: 'task-1', session_id: 'session-1', workspace: 'E:/project', status: 'pending',
      }), { status: 202, headers: { 'Content-Type': 'application/json' } })
    }
    return new Response([
      'data: {"type":"token","content":"完成","sequence":2}',
      '',
      'data: {"type":"done","content":"完成","status":"completed","sequence":3}',
      '',
    ].join('\n'), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
  }

  try {
    const started = await api.startAgentTask('检查项目', 'session-1', 'E:/project')
    const controller = new AbortController()
    const events = []
    for await (const event of api.taskEvents(started.task_id, controller.signal, 1)) {
      events.push(event)
    }

    assert.equal(calls[0].url, '/api/agent/tasks/start')
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      message: '检查项目', session_id: 'session-1', agent_mode: true, workspace: 'E:/project',
    })
    assert.equal(calls[1].url, '/api/agent/tasks/task-1/events?after_sequence=1')
    assert.deepEqual(events.map(event => event.type), ['token', 'done'])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('flushes a final SSE frame without a trailing newline', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(
    'data: {"type":"done","content":"final","status":"completed","sequence":4}',
    { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
  )

  try {
    const events = []
    for await (const event of api.taskEvents('task-1', new AbortController().signal, 3)) events.push(event)
    assert.deepEqual(events, [{ type: 'done', content: 'final', status: 'completed', sequence: 4 }])
  } finally {
    globalThis.fetch = originalFetch
  }
})


test('starts a real project action through the shared task runtime', async () => {
  const originalFetch = globalThis.fetch
  let requested = ''
  globalThis.fetch = async input => {
    requested = String(input)
    return new Response(JSON.stringify({
      project_id: 'project-a', action: 'inspect', task_id: 'task-a',
      session_id: 'project-session-a', workspace: 'E:/project', status: 'pending',
    }), { status: 202, headers: { 'Content-Type': 'application/json' } })
  }

  try {
    const result = await api.startProjectAction('project-a', 'inspect')
    assert.equal(requested, '/api/projects/project-a/actions/inspect')
    assert.equal(result.session_id, 'project-session-a')
    assert.equal(result.task_id, 'task-a')
  } finally {
    globalThis.fetch = originalFetch
  }
})
