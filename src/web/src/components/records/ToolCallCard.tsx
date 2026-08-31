import React, { useState } from 'react'
import { ChevronDown, FileCode2, FileText, Globe, Play, Search, TerminalSquare, Wrench } from 'lucide-react'
import { JsonView } from './JsonView'

export type ToolCallStatus = 'success' | 'failed' | 'recovered' | 'pending'

const TOOL_ICONS: Record<string, typeof Wrench> = {
  run_command: TerminalSquare,
  start_process: Play, stop_process: Play, get_process: Play, list_processes: Play, wait_http: Play,
  read_file: FileText, list_directory: FileText, create_directory: FileText, repo_map: FileText,
  edit_files: FileCode2, undo_last_change: FileCode2,
  search_text: Search, detect_project: Search,
  http_request: Globe, http_request_batch: Globe, check_port: Globe, web_fetch: Globe, clone_repository: Globe,
  verify_project: Play, ensure_venv: Play, install_dependencies: Play,
}

const STATUS_LABELS: Record<ToolCallStatus, string> = {
  success: '成功', failed: '失败', recovered: '已恢复', pending: '未返回',
}

function formatDuration(ms?: number): string {
  if (!ms || ms <= 0) return ''
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`
  return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`
}

/** 从 args 里提取最有信息量的主参数做摘要行。 */
function primaryArg(args: unknown): string {
  if (!args || typeof args !== 'object') return ''
  const record = args as Record<string, unknown>
  for (const key of ['command', 'path', 'query', 'url', 'task']) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) {
      return value.length > 120 ? `${value.slice(0, 120)}…` : value
    }
  }
  if (Array.isArray(record.edits) && record.edits.length) return `edits×${record.edits.length}`
  if (Array.isArray(record.tools) && record.tools.length) return `tools×${record.tools.length}`
  const first = Object.entries(record)[0]
  if (!first) return ''
  const text = typeof first[1] === 'string' ? first[1] : JSON.stringify(first[1])
  return `${first[0]}: ${String(text).slice(0, 80)}`
}

/**
 * 单次工具调用卡：工具调用与其结果合并为一张卡（状态色描边 + 主参数摘要 + 可展开完整入参/结果）。
 * args/result 支持事件 payload 或 tool_runs 行两种来源；都没有时只渲染摘要头。
 */
export function ToolCallCard({
  name, args, result, status = 'success', durationMs, defaultOpen = false,
}: {
  name: string
  args?: unknown
  result?: unknown
  status?: ToolCallStatus
  durationMs?: number
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const Icon = TOOL_ICONS[name] || Wrench
  const summary = primaryArg(args)
  const resultRecord = (result && typeof result === 'object' ? result : undefined) as Record<string, unknown> | undefined
  const errorMessage = typeof resultRecord?.error === 'string' ? resultRecord.error : ''
  const hasBody = (args !== undefined && args !== null && Object.keys(args as object).length > 0) || resultRecord !== undefined
  const duration = formatDuration(durationMs)

  return (
    <div className={`toolcall toolcall--${status}`}>
      <button type="button" className="toolcall__head" onClick={() => hasBody && setOpen(v => !v)} aria-expanded={hasBody ? open : undefined}>
        <Icon size={13} className="toolcall__icon" />
        <span className="toolcall__name">{name}</span>
        {summary && <span className="toolcall__summary" title={summary}>{summary}</span>}
        <span className="toolcall__spacer" />
        {duration && <span className="toolcall__duration">{duration}</span>}
        <span className={`toolcall__status toolcall__status--${status}`}>{STATUS_LABELS[status]}</span>
        {hasBody && <ChevronDown size={12} className={`toolcall__chevron ${open ? 'is-open' : ''}`} />}
      </button>
      {open && hasBody && (
        <div className="toolcall__body">
          {errorMessage && <div className="toolcall__error">{errorMessage}</div>}
          <JsonView label="入参" value={args} />
          <JsonView label="结果" value={result} />
        </div>
      )}
    </div>
  )
}
