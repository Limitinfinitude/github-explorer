import React, { useMemo } from 'react'
import {
  Activity, AlertCircle, Check, CircleDot, FileText, FolderPlus,
  FolderSearch2, GitFork, Loader2, PackageCheck, Pencil, Search, Server, ShieldCheck,
  SquareTerminal, Wrench,
  type LucideIcon,
} from 'lucide-react'
import type { Step } from '../../types'
import { summarizeWorkChain } from '../../lib/workChain'

const ICONS: Record<string, LucideIcon> = {
  list_directory: FolderSearch2,
  read_file: FileText,
  search_text: Search,
  repo_map: FolderSearch2,
  detect_project: PackageCheck,
  create_directory: FolderPlus,
  edit_files: Pencil,
  clone_repository: GitFork,
  run_command: SquareTerminal,
  ensure_venv: PackageCheck,
  install_dependencies: PackageCheck,
  verify_project: ShieldCheck,
  start_process: Server,
  get_process: Activity,
  list_processes: Activity,
  stop_process: Server,
  check_port: Activity,
  wait_http: Activity,
  http_request: Activity,
  http_request_batch: Activity,
}

const LABELS: Record<string, string> = {
  list_directory: '查看目录',
  read_file: '读取文件',
  search_text: '搜索文本',
  repo_map: '项目结构',
  detect_project: '项目识别',
  create_directory: '创建目录',
  edit_files: '修改文件',
  clone_repository: '克隆仓库',
  run_command: '执行命令',
  ensure_venv: '准备环境',
  install_dependencies: '安装依赖',
  verify_project: '验证项目',
  start_process: '启动进程',
  get_process: '查看进程',
  list_processes: '进程列表',
  stop_process: '停止进程',
  check_port: '检查端口',
  wait_http: '等待服务',
  http_request: 'HTTP 请求',
  http_request_batch: '批量请求',
}

function outputSummary(step: Step): string {
  if (!step.done) return ''
  if (step.error) {
    const text = step.error.replace(/\s+/g, ' ').trim()
    return text.length > 64 ? `${text.slice(0, 64)}…` : text
  }
  if (step.output) {
    const line = step.output.split('\n').find(line => line.trim())?.trim() ?? ''
    return line.length > 64 ? `${line.slice(0, 64)}…` : line
  }
  return ''
}

export function WorkChain({
  steps,
  elapsed,
  showSummary = false,
  }: { steps: Step[]; elapsed: number; showSummary?: boolean }) {
  const summary = useMemo(() => summarizeWorkChain(steps), [steps])
  if (!Array.isArray(steps) || steps.length === 0) return null
  const running = steps.some(step => !step.done)
  const statusLabel = running
    ? '进行中'
    : summary.failed
    ? `${summary.failed} 项未恢复`
    : summary.recovered ? `已完成 · ${summary.recovered} 项已恢复` : '已完成'
  return (
    <div className="work-chain work-chain--flat">
      {showSummary && (
        <div className="work-chain__meta-line">
          {running ? <Loader2 size={12} className="is-spinning" /> : summary.failed ? <AlertCircle size={12} /> : <Check size={12} />}
          <span className={summary.failed ? 'is-failed' : running ? 'is-running' : ''}>{statusLabel}</span>
          <span>{steps.length} 个操作 · {elapsed} 秒</span>
          {summary.groups.map(group => (
            <span key={group.key} className={`work-chain__mini-group ${group.failed ? 'is-failed' : group.recovered ? 'is-recovered' : ''}`}>
              <CircleDot size={9} />
              {group.label}<b>{group.count}</b>
            </span>
          ))}
        </div>
      )}
      <div className="tool-cards">
        {steps.map((step, index) => {
          const Icon = ICONS[step.toolName || ''] || Wrench
          const label = LABELS[step.toolName || ''] || step.toolName || '工具调用'
          const stateClass = !step.done ? 'is-running'
            : step.status === 'failed' || step.status === 'rejected' ? 'is-failed'
            : step.status === 'interrupted' ? 'is-interrupted'
            : step.recoveredByCallId ? 'is-recovered'
            : 'is-done'
              const argText = outputSummary(step)
              return (
                <details key={step.callId ?? `tool-${index}`} className={`tool-card ${stateClass}`}>
                  <summary>
                    <Icon size={14} />
                    <span className="tool-card__name">{label}</span>
                    <span className="tool-card__summary">
                      {!step.done ? '进行中…' : argText || (step.status === 'failed' || step.status === 'rejected' ? '失败' : '完成')}
                    </span>
                    <span className="tool-card__status">
                      {!step.done ? (
                        <><span className="tool-card__spinner" />进行中</>
                      ) : step.status === 'failed' || step.status === 'rejected' ? (
                        '失败'
                      ) : step.status === 'interrupted' ? (
                        '中断'
                      ) : step.recoveredByCallId ? (
                        '已恢复'
                      ) : (
                        '完成'
                      )}
                    </span>
                  </summary>
                  {(step.error || (step.args && Object.keys(step.args).length > 0) || step.output) && (
                    <div className="tool-card__body">
                      {step.error && <pre className="tool-card__error">{step.error}</pre>}
                      {step.output && <pre className="tool-card__output">{step.output}</pre>}
                      {step.args && Object.keys(step.args).length > 0 && (
                        <pre className="tool-card__args">{JSON.stringify(step.args, null, 2)}</pre>
                      )}
                    </div>
                  )}
                </details>
              )
        })}
      </div>
    </div>
  )
}
