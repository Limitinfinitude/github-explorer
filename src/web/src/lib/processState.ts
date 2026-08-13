import type { AgentProcess } from '../types'

const PROCESS_STATUSES = new Set<AgentProcess['status']>([
  'running', 'stopped', 'exited', 'orphaned',
])

export function normalizeProcessStatus(status: unknown): AgentProcess['status'] {
  return PROCESS_STATUSES.has(status as AgentProcess['status'])
    ? status as AgentProcess['status']
    : 'orphaned'
}

export function reconcileProcesses(previous: AgentProcess[], live: AgentProcess[]): AgentProcess[] {
  const liveById = new Map(live.map(process => [process.processId, process]))
  const reconciled = previous.map(process => {
    const current = liveById.get(process.processId)
    if (current) {
      liveById.delete(process.processId)
      return { ...process, ...current }
    }
    return process.status === 'running' ? { ...process, status: 'orphaned' as const } : process
  })
  return [...reconciled, ...liveById.values()]
}
