import assert from 'node:assert/strict'
import test from 'node:test'

import { evidenceToMarkdown, filterEvidenceEntries } from '../src/lib/projectEvidence.ts'

const entries = [
  {
    id: 'tool-1', task_id: 'task-1', category: 'tools' as const, status: 'failed',
    title: 'run_command', created_at: '2026-08-14 10:00:00',
    details: { args: { command: 'pytest -q', cwd: 'C:/workspace' }, result: { returncode: 1 } },
  },
  {
    id: 'file-1', task_id: 'task-1', category: 'files' as const, status: 'changed',
    title: '1 个文件变更', created_at: '2026-08-14 10:01:00', details: { files: ['app.py'] },
  },
]

test('filterEvidenceEntries filters category and failure independently', () => {
  assert.deepEqual(filterEvidenceEntries(entries, 'tools', false).map(item => item.id), ['tool-1'])
  assert.deepEqual(filterEvidenceEntries(entries, 'all', true).map(item => item.id), ['tool-1'])
})

test('evidenceToMarkdown includes project identity and structured command evidence', () => {
  const markdown = evidenceToMarkdown({
    project_id: 'project-demo', workspace_root: 'C:/workspace', task: {}, task_history: [], entries,
    events: [], tool_runs: [], changesets: [], artifacts: [], developer_layers: [],
  })

  assert.match(markdown, /# 项目证据记录/)
  assert.match(markdown, /pytest -q/)
  assert.match(markdown, /C:\/workspace/)
  assert.match(markdown, /returncode/)
})
