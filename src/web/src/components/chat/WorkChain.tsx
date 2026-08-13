import React, { useMemo, useState } from 'react'
import {
  Activity, AlertCircle, Check, ChevronRight, CircleDot, FileText, FolderPlus,
  FolderSearch2, GitFork, PackageCheck, Pencil, Search, Server, ShieldCheck,
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
}

export function WorkChain({
  steps,
  elapsed,
  defaultOpen = false,
  }: { steps: Step[]; elapsed: number; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const summary = useMemo(() => summarizeWorkChain(steps), [steps])
  if (!steps.length) return null
  const statusLabel = summary.failed
    ? `${summary.failed} 项未恢复`
    : summary.recovered ? `已完成 · ${summary.recovered} 项已恢复` : '已完成'
  return (
    <div className="work-chain">
      <button
        onClick={() => setOpen(o => !o)}
        className="work-chain__toggle"
        aria-expanded={open}
      >
        <ChevronRight size={13} className={open ? 'is-open' : ''} />
        <span className={`work-chain__status ${summary.failed ? 'is-failed' : ''}`}>
          {summary.failed ? <AlertCircle size={13} /> : <Check size={13} />}
          {statusLabel}
        </span>
        <span className="work-chain__meta">{steps.length} 个操作 · {elapsed} 秒</span>
      </button>
      {open && (
        <div className="work-chain__details">
          <div className="work-chain__groups">
            {summary.groups.map(group => (
              <span key={group.key} className={`work-chain__group ${group.failed ? 'is-failed' : group.recovered ? 'is-recovered' : ''}`}>
                <CircleDot size={11} />
                {group.label} <b>{group.count}</b>
              </span>
            ))}
          </div>
          <div className="work-chain__steps">
            {summary.tools.map(tool => {
              const Icon = ICONS[tool.name] || Wrench
              return (
                <div key={`${tool.group}-${tool.name}`} className={`work-chain__step ${tool.failed ? 'is-failed' : tool.recovered ? 'is-recovered' : 'is-done'}`}>
                  <Icon size={13} />
                  <span>{tool.label}{tool.count > 1 ? ` ×${tool.count}` : ''}</span>
                  {tool.failed > 0 && <small>{tool.failed} 失败</small>}
                  {tool.failed === 0 && tool.recovered > 0 && <small>{tool.recovered} 已恢复</small>}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
