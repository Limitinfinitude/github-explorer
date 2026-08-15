export function processIdentityLabel(process: Record<string, unknown>) {
  if (process.status === 'orphaned') return '进程已失联'
  if (process.status !== 'running') return '没有运行中的服务'
  if (process.owned === true && Array.isArray(process.listener_pids) && process.listener_pids.length > 0) {
    return '端口归属已确认'
  }
  return '进程运行中，端口尚未确认'
}

export function qualityState(metrics: { false_completion: boolean; false_incomplete: boolean; completion_evidence?: string; terminal_reason?: string }) {
  if (metrics.false_completion) return { tone: 'danger', label: '完成状态不可信' } as const
  if (metrics.false_incomplete) return { tone: 'warning', label: '可能误判未完成' } as const
  if (metrics.completion_evidence === 'verified') return { tone: 'success', label: '作品验收已验证' } as const
  if (metrics.completion_evidence === 'partial') return { tone: 'warning', label: '作品部分完成' } as const
  return { tone: 'success', label: '事实与终态一致' } as const
}

export function terminalReasonLabel(reason?: string) {
  return ({
    completed: '正常完成',
    stage_budget_exhausted: '阶段预算已用尽',
    diagnostic_budget_exhausted: '诊断预算已用尽',
    approval_pending: '等待审批',
    tool_repair_exhausted: '工具修复已耗尽',
    unrecovered_tool_failure: '存在未恢复工具失败',
    interrupted: '运行被中断',
    cancelled: '任务已取消',
    model_error: '模型请求失败',
    no_execution_facts: '没有执行事实',
    running: '仍在运行',
  } as Record<string, string>)[reason || ''] || '未分类结束'
}
