import assert from 'node:assert/strict'
import test from 'node:test'

import { workspaceFromStream } from '../src/lib/workspaceState.ts'

test('stream workspace initializes an empty selection', () => {
  assert.equal(workspaceFromStream('', 'E:/projects/app'), 'E:/projects/app')
})

test('stale stream workspace cannot overwrite an explicit selection', () => {
  assert.equal(
    workspaceFromStream('E:/projects/selected', 'E:/projects/stale'),
    'E:/projects/selected',
  )
})
