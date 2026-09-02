import React, { useState } from 'react'
import {
  Activity, ChevronDown, FileText, FolderPlus, FolderSearch2, GitFork, Globe, PackageCheck,
  Pencil, Search, Server, ShieldCheck, SquareTerminal, Wrench, type LucideIcon,
} from 'lucide-react'
import type { Step } from '../../types'
import { useStopBubbleRef } from '../../lib/stopBubble'
import { parseUnifiedDiff, editsToDiff, diffStats, type DiffFile } from '../../lib/diffView'
import { DiffFileView } from './DiffView'

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
  web_fetch: Globe,
  web_search: Search,
  use_skill: Wrench,
  spawn_subagent: Activity,
  spawn_subagents: Activity,
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
  web_fetch: '网页抓取',
  web_search: '网页搜索',
  use_skill: '调用技能',
  spawn_subagent: '子代理',
  spawn_subagents: '并行子代理',
}

/** 参数 → 中文动作短语（工具卡副标题用）：挑最有信息量的参数渲染成「动词 + 宾语」。 */
function argPhrase(step: Step): string {
  const args = step.args
  if (!args || typeof args !== 'object') return ''
  const name = step.toolName || ''
  const pick = (key: string): string => {
    const value = args[key]
    return typeof value === 'string' ? value.trim() : ''
  }
  if (name === 'read_file') { const p = pick('path'); return p ? `读取 ${p}` : '' }
  if (name === 'list_directory') { const p = pick('path'); return p ? `查看 ${p}` : '' }
  if (name === 'create_directory') { const p = pick('path'); return p ? `创建 ${p}` : '' }
  if (name === 'search_text') {
    const q = pick('query') || pick('pattern')
    const scope = pick('path') || pick('directory')
    return q ? `搜索 "${q}"${scope ? `，范围 ${scope}` : ''}` : ''
  }
  if (name === 'edit_files') {
    const edits = args.edits
    const files = Array.isArray(edits)
      ? [...new Set(edits.filter(e => e && typeof e === 'object' && typeof e.path === 'string').map(e => e.path))]
      : []
    return files.length ? `修改 ${files.length} 个文件（${files.slice(0, 2).join('、')}${files.length > 2 ? ' 等' : ''}）` : ''
  }
  if (name === 'run_command') { const c = pick('command'); return c ? c.slice(0, 70) : '' }
  if (name === 'clone_repository') { const u = pick('url'); return u ? `克隆 ${u}` : '' }
  if (name === 'web_fetch') { const u = pick('url'); return u ? u : '' }
  if (name === 'web_search') { const q = pick('query'); return q ? `搜索 "${q}"` : '' }
  if (name === 'http_request' || name === 'wait_http') {
    const u = pick('url')
    const method = pick('method')
    return u ? `${method ? method + ' ' : ''}${u}` : ''
  }
  if (name === 'check_port') {
    const host = pick('host') || 'localhost'
    const port = args.port
    return port ? `${host}:${port}` : ''
  }
  if (name === 'start_process') { const c = pick('command'); return c ? `启动 ${c.slice(0, 50)}` : '' }
  if (name === 'spawn_subagent' || name === 'spawn_subagents') {
    const task = pick('task') || pick('goal')
    return task ? task.slice(0, 50) : ''
  }
  if (name === 'use_skill') { const s = pick('skill') || pick('name'); return s ? `技能 ${s}` : '' }
  // 通用：第一个字符串参数
  const first = Object.values(args).find(v => typeof v === 'string' && v.trim())
  return typeof first === 'string' ? first.trim().slice(0, 60) : ''
}

/** 输出 → 中文结果摘要：成功时提取关键信息，失败时截取错误。 */
function outputSummary(step: Step): string {
  if (!step.done) return ''
  if (step.error) {
    const text = step.error.replace(/\s+/g, ' ').trim()
    return text.length > 64 ? `${text.slice(0, 64)}…` : text
  }
  if (step.output) {
    // 尝试从 JSON 输出提取人类可读信息
    const first = step.output.split('\n').find(line => line.trim())?.trim() ?? ''
    const compact = first.replace(/\s+/g, ' ')
    return compact.length > 64 ? `${compact.slice(0, 64)}…` : compact
  }
  return ''
}

function stateClassOf(step: Step): string {
  if (!step.done) return 'is-running'
  if (step.status === 'failed' || step.status === 'rejected') return 'is-failed'
  if (step.status === 'interrupted') return 'is-interrupted'
  if (step.recoveredByCallId) return 'is-recovered'
  return 'is-done'
}

/** edit_files 的 diff 文件列表：优先解析后端 unified diff（data.diff 或 output 内嵌的完整结果 JSON），缺失时用 args.edits 回退。 */
function editDiffFiles(step: Step): DiffFile[] | null {
  const raw = step.data?.diff
  if (typeof raw === 'string' && raw.trim()) {
    const parsed = parseUnifiedDiff(raw)
    if (parsed.length > 0) return parsed
  }
  // 结果超长被截断时，output 可能是序列化 ToolResult JSON 的预览，其中可能含完整 diff
  const out = step.output?.trim()
  if (out && out.startsWith('{')) {
    try {
      const obj = JSON.parse(out) as { data?: { diff?: unknown } }
      const embedded = obj.data?.diff
      if (typeof embedded === 'string' && embedded.trim()) {
        const parsed = parseUnifiedDiff(embedded)
        if (parsed.length > 0) return parsed
      }
    } catch { /* 截断的 JSON 解析失败 → 走 args 回退 */ }
  }
  const edits = step.args?.edits
  if (Array.isArray(edits)) {
    const fallback = editsToDiff(edits as Array<Record<string, unknown>>)
    if (fallback.length > 0) return fallback
  }
  return null
}

function StatusBadge({ step }: { step: Step }) {
  return (
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
  )
}

/** 读取文件：静态行，只有路径与状态，不可点击展开。 */
function ReadCard({ step, label, Icon }: { step: Step; label: string; Icon: LucideIcon }) {
  let phrase = argPhrase(step)
  const start = step.args?.start_line
  const end = step.args?.end_line
  if (typeof start === 'number' && phrase) {
    phrase += typeof end === 'number' ? `（第 ${start}-${end} 行）` : `（从第 ${start} 行起）`
  }
  const error = step.error?.replace(/\s+/g, ' ').trim()
  return (
    <div className={`tool-card tool-card--read ${stateClassOf(step)}`}>
      <Icon size={14} />
      <span className="tool-card__name">{label}</span>
      <span className="tool-card__summary" title={error || phrase}>
        {error || phrase || '完成'}
      </span>
      <StatusBadge step={step} />
    </div>
  )
}

/** 修改文件：可展开，正文只展示改动位置（diff），不展示全量内容。 */
function EditCard({ step, label, Icon }: { step: Step; label: string; Icon: LucideIcon }) {
  const stopRef = useStopBubbleRef<HTMLDetailsElement>()
  const [open, setOpen] = useState(false)
  const files = editDiffFiles(step)
  const stats = files ? diffStats(files) : { add: 0, del: 0 }
  const summary = (() => {
    if (files && files.length > 0) {
      const paths = files.map(f => f.path).slice(0, 2)
      return `修改 ${files.length} 个文件（${paths.join('、')}${files.length > 2 ? ' 等' : ''}）`
    }
    return argPhrase(step) || outputSummary(step) || '完成'
  })()
  return (
    <details
      className={`tool-card ${stateClassOf(step)} ${open ? 'is-open' : ''}`}
      ref={stopRef}
      onToggle={e => {
        if (e.target === e.currentTarget) setOpen((e.target as HTMLDetailsElement).open)
      }}
    >
      <summary>
        <Icon size={14} />
        <span className="tool-card__name">{label}</span>
        <span className="tool-card__summary">{summary}</span>
        {(stats.add > 0 || stats.del > 0) && (
          <span className="tool-card__diffstat">
            <b className="is-add">+{stats.add}</b>
            <b className="is-del">−{stats.del}</b>
          </span>
        )}
        <StatusBadge step={step} />
        <ChevronDown size={12} className="tool-card__chevron" />
      </summary>
      <div className="tool-card__body">
        {step.error && <pre className="tool-card__error">{step.error}</pre>}
        {files && files.length > 0 ? (
          <div className="diff-view">
            {files.map(file => <DiffFileView key={file.path} file={file} />)}
          </div>
        ) : (
          step.output && <pre className="tool-card__output">{step.output}</pre>
        )}
      </div>
    </details>
  )
}

/** 单个工具调用卡（可折叠）：图标 + 中文名 + 参数中文短语 + 状态徽章 + 展开详情。 */
export function ToolCard({ step }: { step: Step }) {
  const stopRef = useStopBubbleRef<HTMLDetailsElement>()
  const Icon = ICONS[step.toolName || ''] || Wrench
  const label = LABELS[step.toolName || ''] || step.toolName || '工具调用'
  const phrase = argPhrase(step)
  const resultText = outputSummary(step)

  if (step.toolName === 'read_file') {
    return <ReadCard step={step} label={label} Icon={Icon} />
  }
  if (step.toolName === 'edit_files') {
    return <EditCard step={step} label={label} Icon={Icon} />
  }

  const stateClass = stateClassOf(step)
  return (
    <details key={step.callId ?? `tool-${label}`} className={`tool-card ${stateClass}`} ref={stopRef}>
      <summary>
        <Icon size={14} />
        <span className="tool-card__name">{label}</span>
        <span className="tool-card__summary">
          {!step.done
            ? (phrase || '进行中…')
            : phrase || resultText || (step.status === 'failed' || step.status === 'rejected' ? '失败' : '完成')}
        </span>
        <StatusBadge step={step} />
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
}
