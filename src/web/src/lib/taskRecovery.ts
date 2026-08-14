const RECOVERABLE_TASK_STATUSES = new Set([
  'pending', 'queued', 'running', 'waiting_approval',
])

export function isRecoverableTaskStatus(status: string): boolean {
  return RECOVERABLE_TASK_STATUSES.has(status)
}
