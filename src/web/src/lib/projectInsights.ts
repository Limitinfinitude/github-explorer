export function processIdentityLabel(process: Record<string, unknown>) {
  if (process.status === 'orphaned') return '进程已失联'
  if (process.status !== 'running') return '没有运行中的服务'
  if (process.owned === true && Array.isArray(process.listener_pids) && process.listener_pids.length > 0) {
    return '端口归属已确认'
  }
  return '进程运行中，端口尚未确认'
}

export function qualityState(metrics: { false_completion: boolean; false_incomplete: boolean }) {
  if (metrics.false_completion) return { tone: 'danger', label: '完成状态不可信' } as const
  if (metrics.false_incomplete) return { tone: 'warning', label: '可能误判未完成' } as const
  return { tone: 'success', label: '事实与终态一致' } as const
}
