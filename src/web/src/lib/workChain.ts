import type { Step } from '../types'

export type WorkChainTool = {
  name: string
  label: string
  group: string
  count: number
  failed: number
}

export type WorkChainGroup = {
  key: string
  label: string
  count: number
  failed: number
}

const TOOL_LABELS: Record<string, string> = {
  list_directory: '浏览目录',
  read_file: '读取文件',
  search_text: '搜索代码',
  repo_map: '扫描结构',
  detect_project: '识别项目',
  create_directory: '创建目录',
  edit_files: '编辑文件',
  undo_last_change: '撤销修改',
  run_command: '运行命令',
  clone_repository: '克隆仓库',
  ensure_venv: '准备虚拟环境',
  install_dependencies: '安装依赖',
  verify_project: '验证项目',
  start_process: '启动服务',
  get_process: '读取进程',
  list_processes: '查看进程',
  stop_process: '停止服务',
  check_port: '检查端口',
  wait_http: '等待服务',
}

const GROUPS: Array<{ key: string; label: string; tools: string[] }> = [
  { key: 'context', label: '读取上下文', tools: ['list_directory', 'read_file', 'search_text', 'repo_map', 'detect_project'] },
  { key: 'files', label: '修改文件', tools: ['create_directory', 'edit_files', 'undo_last_change', 'clone_repository'] },
  { key: 'commands', label: '运行与验证', tools: ['run_command', 'ensure_venv', 'install_dependencies', 'verify_project'] },
  { key: 'process', label: '管理服务', tools: ['start_process', 'get_process', 'list_processes', 'stop_process'] },
  { key: 'network', label: '检查服务', tools: ['check_port', 'wait_http'] },
]

function groupFor(name: string) {
  return GROUPS.find(group => group.tools.includes(name)) ?? { key: 'other', label: '其他操作', tools: [] }
}

export function summarizeWorkChain(steps: Step[]) {
  const tools: WorkChainTool[] = []
  const byKey = new Map<string, WorkChainTool>()
  let completed = 0
  let failed = 0

  steps.forEach((step, index) => {
    const match = step.text.match(/^([A-Za-z_][\w.-]*)\(\.\.\.\)/)
    const name = match?.[1] ?? `step-${index}`
    const label = match ? (TOOL_LABELS[name] ?? name) : step.text
    const group = match ? groupFor(name) : { key: 'other', label: '其他操作', tools: [] }
    const isFailed = /失败|error|failed/i.test(step.text)
    const key = match ? name : `${name}:${label}`
    const existing = byKey.get(key)
    if (existing) {
      existing.count += 1
      if (isFailed) existing.failed += 1
    } else {
      const tool = { name, label, group: group.key, count: 1, failed: isFailed ? 1 : 0 }
      tools.push(tool)
      byKey.set(key, tool)
    }
    if (step.done) completed += 1
    if (isFailed) failed += 1
  })

  const groups: WorkChainGroup[] = []
  const groupsByKey = new Map<string, WorkChainGroup>()
  tools.forEach(tool => {
    const definition = tool.group === 'other'
      ? { key: 'other', label: '其他操作' }
      : GROUPS.find(group => group.key === tool.group)!
    const existing = groupsByKey.get(definition.key)
    if (existing) {
      existing.count += tool.count
      existing.failed += tool.failed
    } else {
      const group = { key: definition.key, label: definition.label, count: tool.count, failed: tool.failed }
      groups.push(group)
      groupsByKey.set(group.key, group)
    }
  })

  return { tools, groups, completed, failed }
}
