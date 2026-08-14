import type { ProjectEvidence, ProjectEvidenceEntry, ProjectEvidenceFilter } from '../types'

export function filterEvidenceEntries(
  entries: ProjectEvidenceEntry[], filter: ProjectEvidenceFilter, failuresOnly = false,
) {
  return entries.filter(entry => {
    if (failuresOnly && entry.status !== 'failed') return false
    if (filter === 'all') return true
    if (filter === 'failed') return entry.status === 'failed'
    if (filter === 'recovered') return entry.status === 'recovered'
    return entry.category === filter
  })
}

function jsonBlock(value: unknown) {
  return `\`\`\`json\n${JSON.stringify(value, null, 2)}\n\`\`\``
}

export function evidenceToMarkdown(evidence: ProjectEvidence) {
  const lines = [
    '# 项目证据记录', '',
    `- 项目：${evidence.project_id}`,
    `- 工作区：${evidence.workspace_root || '未绑定'}`,
    `- 导出时间：${new Date().toISOString()}`,
    '', '## 任务历史', '',
  ]
  for (const task of evidence.task_history) {
    lines.push(`- ${task.created_at || '未知时间'} · ${task.status} · ${task.message} (${task.task_id})`)
  }
  lines.push('', '## 执行证据', '')
  for (const entry of evidence.entries) {
    lines.push(
      `### ${entry.title}`,
      '',
      `- 类型：${entry.category}`,
      `- 状态：${entry.status}`,
      `- 任务：${entry.task_id}`,
      `- 时间：${entry.created_at || '未知'}`,
      '',
      jsonBlock(entry.details),
      '',
    )
  }
  return lines.join('\n')
}
