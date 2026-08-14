const coverageLabels: Record<string, string> = {
  model: '模型',
  tool: '工具',
  approval: '审批',
  file: '文件',
  verification: '验证',
  process: '进程',
  terminal: '终态',
}

export function localCoverageLabels(coverage: string[] | null | undefined): string[] {
  return (coverage || []).map(item => coverageLabels[item] || item)
}
