import assert from 'node:assert/strict'
import test from 'node:test'

import { formatLocalTimestamp, relativeMessageTime } from '../src/lib/time.ts'

test('sqlite UTC timestamps are rendered in the requested local timezone', () => {
  assert.equal(
    formatLocalTimestamp('2026-08-10 03:57:07', 'Asia/Shanghai'),
    '2026-08-10 11:57:07',
  )
})


test('relative message time uses canonical timestamp instead of message id shape', () => {
  const now = Date.parse('2026-08-14T10:00:30Z')

  assert.equal(relativeMessageTime('2026-08-14 10:00:25', now), '刚刚')
  assert.equal(relativeMessageTime('not-a-date', now), '')
})
