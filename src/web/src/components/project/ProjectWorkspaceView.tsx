import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Activity, BookMarked, BookOpen, Check, ChevronDown, CircleAlert, Clock, Copy, Download, FileCode2, FileText, FolderOpen, GitBranch, MessageSquare, PackageCheck, Play, RefreshCw, Search, ShieldCheck, TerminalSquare, Upload, Waypoints } from 'lucide-react'
import { api } from '../../lib/api'
import { evidenceToMarkdown, filterEvidenceEntries } from '../../lib/projectEvidence'
import { formatLocalTimestamp } from '../../lib/time'
import { processIdentityLabel, qualityState, terminalReasonLabel } from '../../lib/projectInsights'
import type { ProjectEvidence, ProjectEvidenceFilter, ProjectMemory, ProjectOverview, ProjectSummary } from '../../types'

const STAGES = [
  { id: 'inspect', label: '项目体检', icon: Search },
  { id: 'run', label: '跑起来', icon: Play },
  { id: 'understand', label: '看懂', icon: FileCode2 },
  { id: 'experiment', label: '实验室', icon: TerminalSquare },
  { id: 'verify', label: '验证', icon: ShieldCheck },
  { id: 'record', label: '记录', icon: Activity },
]

const LIVE_STATUSES = new Set(['running', 'waiting_approval', 'pending'])

function statusLabel(status: string) {
  return ({ completed: '已完成', incomplete: '未完成', failed: '失败', waiting_approval: '等待确认', running: '进行中', not_started: '未开始', pending: '排队中' } as Record<string, string>)[status] || status
}

const EVIDENCE_FILTERS: Array<{ id: ProjectEvidenceFilter; label: string }> = [
  { id: 'all', label: '全部' }, { id: 'failed', label: '失败' }, { id: 'recovered', label: '已恢复' },
  { id: 'tools', label: '工具' }, { id: 'files', label: '文件' },
  { id: 'verification', label: '验证' }, { id: 'processes', label: '进程' },
]

const CATEGORY_LABELS = {
  events: '事件', tools: '工具', files: '文件', verification: '验证', processes: '进程', observability: 'Trace',
} as const

const BUDGET_LABELS: Record<string, string> = {
  inspect: '体检', implement: '实现', test: '测试', run: '运行验收',
}

const PROJECT_ACTIONS = [
  { id: 'inspect', label: '项目体检', icon: Search },
  { id: 'prepare', label: '准备环境', icon: PackageCheck },
  { id: 'start', label: '启动并验证', icon: Play },
  { id: 'guide', label: '生成导读', icon: BookOpen },
  { id: 'verify', label: '运行验证', icon: ShieldCheck },
]

function entrySummary(details: Record<string, unknown>) {
  const args = details.args && typeof details.args === 'object' ? details.args as Record<string, unknown> : details
  const result = details.result && typeof details.result === 'object' ? details.result as Record<string, unknown> : details
  const command = args.command || details.command
  const cwd = args.cwd || result.cwd || details.cwd
  const returncode = result.returncode ?? details.returncode
  return [command && String(command), cwd && `cwd ${cwd}`, returncode !== undefined && `退出码 ${returncode}`].filter(Boolean).join(' · ')
}

function EvidenceDrawer({ projectId, overview }: { projectId: string; overview: ProjectOverview }) {
  const [open, setOpen] = useState(false)
  const [evidence, setEvidence] = useState<ProjectEvidence | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState<ProjectEvidenceFilter>('all')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    setOpen(false); setEvidence(null); setError(''); setFilter('all'); setCopied(false)
  }, [projectId])

  async function loadEvidence() {
    setLoading(true); setError('')
    try { setEvidence(await api.getProjectEvidence(projectId)) }
    catch (err) { setError(err instanceof Error ? err.message : '读取开发者证据失败') }
    finally { setLoading(false) }
  }

  async function toggle() {
    const next = !open
    setOpen(next)
    if (next && !evidence) await loadEvidence()
  }

  const visibleEntries = useMemo(
    () => evidence ? filterEvidenceEntries(evidence.entries, filter).slice(0, 200) : [],
    [evidence, filter],
  )

  async function copyEvidence() {
    if (!evidence) return
    await navigator.clipboard.writeText(evidenceToMarkdown(evidence))
    setCopied(true); window.setTimeout(() => setCopied(false), 1600)
  }

  function downloadEvidence() {
    if (!evidence) return
    const blob = new Blob([evidenceToMarkdown(evidence)], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url; anchor.download = `${evidence.project_id}-evidence.md`; anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <section className="project-evidence">
      <button type="button" className="project-evidence__trigger" onClick={() => void toggle()} aria-expanded={open}>
        <span><Activity size={15} /> 开发者证据层</span>
        <small>{overview.evidence_counts.events} 事件 · {overview.evidence_counts.tool_runs} 工具 · {overview.evidence_counts.files} 文件</small>
        <ChevronDown size={15} className={open ? 'is-open' : ''} />
      </button>
      {open && (loading ? <div className="project-evidence__empty">正在读取项目证据…</div> : error ? (
        <div className="project-evidence__error"><CircleAlert size={16} /><span>{error}</span><button type="button" onClick={() => void loadEvidence()}>重试</button></div>
      ) : evidence ? (
        <div className="project-evidence__body">
          <div className="project-evidence__toolbar">
            <code title={evidence.workspace_root}>{evidence.workspace_root || '未绑定工作区'}</code>
            <div className="project-evidence__actions">
              <button type="button" onClick={() => void copyEvidence()} title="复制为 Markdown" aria-label="复制项目证据">{copied ? <Check size={14} /> : <Copy size={14} />}</button>
              <button type="button" onClick={downloadEvidence} title="下载 Markdown" aria-label="下载项目证据"><Download size={14} /></button>
            </div>
          </div>
          <div className="project-evidence__filters" aria-label="证据筛选">
            {EVIDENCE_FILTERS.map(item => <button key={item.id} type="button" className={filter === item.id ? 'is-active' : ''} onClick={() => setFilter(item.id)}>{item.label}</button>)}
          </div>
          <div className="project-evidence__layout">
            <aside className="project-evidence__history" aria-label="项目任务历史">
              <strong>任务历史</strong><small>{evidence.task_history.length} 次</small>
              {evidence.task_history.map(item => <div key={item.task_id} className="project-history-item"><span className={`evidence-status evidence-status--${item.status}`} /> <div><b>{item.message}</b><small>{formatLocalTimestamp(item.created_at)} · {statusLabel(item.status)}</small></div></div>)}
            </aside>
            <div className="project-evidence__timeline">
              <div className="project-evidence__count">显示 {visibleEntries.length} / {evidence.entries.length} 条证据{evidence.entries.length > 200 && filter === 'all' ? '，已限制首 200 条' : ''}</div>
              {visibleEntries.length === 0 ? <div className="project-evidence__empty">当前筛选下没有证据。</div> : visibleEntries.map(entry => (
                <details key={entry.id} className="project-evidence-entry">
                  <summary>
                    <span className={`evidence-status evidence-status--${entry.status}`} />
                    <span className="project-evidence-entry__title"><b>{entry.title}</b><small>{entrySummary(entry.details) || entry.task_id}</small></span>
                    <span className="project-evidence-entry__category">{CATEGORY_LABELS[entry.category]}</span>
                    <time>{formatLocalTimestamp(entry.created_at)}</time>
                    <ChevronDown size={13} />
                  </summary>
                  <div className="project-evidence-entry__details"><div><span>任务</span><code>{entry.task_id}</code></div><pre>{JSON.stringify(entry.details, null, 2)}</pre></div>
                </details>
              ))}
            </div>
          </div>
        </div>
      ) : <div className="project-evidence__empty">暂无开发者证据。</div>)}
    </section>
  )
}

function verificationBadge(status: string) {
  if (status === 'verified') return '已验证'
  if (status === 'partial') return '部分验证'
  if (status === 'unverified') return '未验证'
  return status
}

function ProjectMemoriesDrawer({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false)
  const [memories, setMemories] = useState<ProjectMemory[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setOpen(false); setMemories([]); setError('')
  }, [projectId])

  async function load() {
    setLoading(true); setError('')
    try { setMemories(await api.getProjectMemories(projectId)) }
    catch (err) { setError(err instanceof Error ? err.message : '读取项目记忆失败') }
    finally { setLoading(false) }
  }

  async function toggle() {
    const next = !open
    setOpen(next)
    if (next) await load()
  }

  return (
    <section className="project-evidence">
      <button type="button" className="project-evidence__trigger" onClick={() => void toggle()} aria-expanded={open}>
        <span><BookMarked size={15} /> 项目记忆</span>
        <small>{memories.length ? `${memories.length} 条事实` : '任务完成后的沉淀事实'}</small>
        <ChevronDown size={15} className={open ? 'is-open' : ''} />
      </button>
      {open && (loading ? <div className="project-evidence__empty">正在读取项目记忆…</div> : error ? (
        <div className="project-evidence__error"><CircleAlert size={16} /><span>{error}</span><button type="button" onClick={() => void load()}>重试</button></div>
      ) : memories.length === 0 ? <div className="project-evidence__empty">还没有沉淀的项目事实。任务完成后，验证通过的结论会自动写入这里。</div> : (
        <div className="project-memories__body">
          <div className="project-memories__actions">
            <button type="button" onClick={() => void load()} title="刷新项目记忆" aria-label="刷新项目记忆"><RefreshCw size={13} /></button>
          </div>
          {memories.map(memory => (
            <article key={memory.id} className="project-memory">
              <p>{memory.content}</p>
              <footer>
                <span className={`project-memory__badge project-memory__badge--${memory.verification_status}`}>{verificationBadge(memory.verification_status)}</span>
                <span>置信度 {Math.round(memory.confidence * 100)}%</span>
                <span>{memory.source_type}{memory.source_ref ? ` · ${memory.source_ref.slice(0, 12)}` : ''}</span>
                <time>{formatLocalTimestamp(memory.updated_at)}</time>
              </footer>
            </article>
          ))}
        </div>
      ))}
    </section>
  )
}

function FailurePatterns({ patterns }: { patterns: ProjectOverview['failure_patterns'] }) {
  if (!patterns || patterns.length === 0) return null
  return (
    <section className="project-failures" aria-label="失败模式">
      <div className="project-failures__heading"><CircleAlert size={15} /><span>失败模式</span><small>{patterns.length} 类 · 未恢复</small></div>
      {patterns.slice(0, 5).map(pattern => (
        <div className="project-failure-item" key={`${pattern.tool_name}-${pattern.error}`}>
          <b>{pattern.tool_name} × {pattern.count}</b>
          <code>{pattern.error}</code>
        </div>
      ))}
    </section>
  )
}

export function ProjectWorkspaceView({
  onOpenProjectConversation,
}: {
  onOpenProjectConversation: (sessionId: string, title: string, userMessage?: string, project?: { projectId: string; workspace: string }) => void
}) {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [projectId, setProjectId] = useState('')
  const [overview, setOverview] = useState<ProjectOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionPending, setActionPending] = useState('')
  const [actionError, setActionError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<number | null>(null)
  const [exporting, setExporting] = useState(false)
  const [importPath, setImportPath] = useState('')
  const [importing, setImporting] = useState(false)
  const importInputRef = useRef<HTMLInputElement>(null)

  async function refresh() {
    setLoading(true); setError('')
    try {
      const next = await api.getProjects()
      setProjects(next)
      const selected = next.some(project => project.project_id === projectId) ? projectId : (next[0]?.project_id || '')
      setProjectId(selected)
      setOverview(selected ? await api.getProjectOverview(selected) : null)
      setLastUpdated(Date.now())
    } catch (err) { setError(err instanceof Error ? err.message : '读取项目工作台失败') } finally { setLoading(false) }
  }
  useEffect(() => { void refresh() }, [])

  // 实时轮询：选中项目有非终态任务时每 3 秒刷新一次概览，终态自动停止
  useEffect(() => {
    if (!projectId || !overview || !LIVE_STATUSES.has(overview.stage_status)) return
    const timer = window.setInterval(async () => {
      setRefreshing(true)
      try {
        const next = await api.getProjectOverview(projectId)
        setOverview(next)
        setLastUpdated(Date.now())
      } catch {
        // 瞬时失败保留上一次快照，下一轮继续尝试
      } finally {
        setRefreshing(false)
      }
    }, 3000)
    return () => window.clearInterval(timer)
  }, [projectId, overview?.stage_status])

  const activeStage = useMemo(() => overview?.stage || 'inspect', [overview])
  const activeProcess = overview?.active_processes.find(process => process.status === 'running') || overview?.active_processes[0]
  const quality = overview ? qualityState(overview.quality_metrics) : null
  const projectTitle = overview?.workspace_root.split(/[\\/]/).filter(Boolean).pop() || '项目'

  async function startAction(action: string) {
    if (!overview || actionPending) return
    setActionPending(action); setActionError('')
    try {
      const started = await api.startProjectAction(overview.project_id, action)
      const label = PROJECT_ACTIONS.find(item => item.id === action)?.label || '项目任务'
      onOpenProjectConversation(started.session_id, `${projectTitle} 项目`, label, {
        projectId: overview.project_id,
        workspace: overview.workspace_root,
      })
    } catch (err) {
      setActionError(err instanceof Error ? err.message : '无法启动项目动作')
    } finally {
      setActionPending('')
    }
  }

  async function exportReport() {
    if (!overview || exporting) return
    setExporting(true); setActionError('')
    try {
      const report = await api.getProjectReport(overview.project_id)
      const blob = new Blob([report.markdown], { type: 'text/markdown;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url; anchor.download = `${projectTitle || 'project'}-report.md`; anchor.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : '导出项目报告失败')
    } finally {
      setExporting(false)
    }
  }

  async function importProject(path: string) {
    const trimmed = path.trim()
    if (!trimmed || importing) return
    setImporting(true); setError(''); setActionError('')
    try {
      const result = await api.importProject(trimmed)
      setImportPath('')
      await refresh()
      const name = result.workspace.split(/[\\/]/).filter(Boolean).pop() || '项目'
      onOpenProjectConversation(result.session_id, `${name} 项目`, '项目体检已启动', {
        projectId: result.project_id,
        workspace: result.workspace,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : '导入项目失败')
    } finally {
      setImporting(false)
    }
  }

  function selectProject(id: string) {
    setProjectId(id); setLoading(true)
    api.getProjectOverview(id).then(next => { setOverview(next); setLastUpdated(Date.now()) })
      .catch(err => setError(err instanceof Error ? err.message : '读取项目失败'))
      .finally(() => setLoading(false))
  }

  return (
    <div className="project-workspace-view">
      <header className="project-workspace__header">
        <div><div className="project-workspace__eyebrow"><FolderOpen size={13} /> PROJECT WORKBENCH</div><h1>项目工作台</h1><p>从跑通到看懂，再到可追溯的二改实验。</p></div>
        <div className="project-workspace__header-actions">
          {overview && (
            <button type="button" className="icon-button" onClick={() => void exportReport()} disabled={exporting} title="导出项目报告 (Markdown)" aria-label="导出项目报告">
              <FileText size={15} />{exporting ? '生成中…' : '导出报告'}
            </button>
          )}
          <button type="button" className="icon-button" onClick={() => void refresh()} title="刷新项目状态" aria-label="刷新项目状态"><RefreshCw size={15} className={refreshing ? 'is-spinning' : ''} /></button>
        </div>
      </header>
      {lastUpdated && overview && <div className="project-workspace__updated"><Clock size={11} /> 更新于 {new Date(lastUpdated).toLocaleTimeString()}</div>}
      {loading ? <div className="project-state">正在读取最近项目…</div> : error && !overview ? <div className="project-state project-state--error"><CircleAlert size={16} />{error}</div> : !overview ? (
        <div className="project-import">
          <div className="project-import__icon"><FolderOpen size={20} /></div>
          <strong>导入一个本地项目</strong>
          <p>选择一个本地目录作为实验材料，工作台会立即启动「项目体检」，并在之后保留运行、证据与实验记录。</p>
          <div className="project-import__form">
            <input
              ref={importInputRef}
              value={importPath}
              onChange={event => setImportPath(event.target.value)}
              onKeyDown={event => { if (event.key === 'Enter') void importProject(importPath) }}
              placeholder="粘贴目录绝对路径，例如 C:\projects\demo"
              spellCheck={false}
              aria-label="项目目录绝对路径"
            />
            <button type="button" className="project-import__primary" disabled={!importPath.trim() || importing} onClick={() => void importProject(importPath)}>
              <Upload size={14} />{importing ? '正在导入并体检…' : '导入并体检'}
            </button>
          </div>
          <small>也可以在设置中配置「默认工作目录」，或先在对话里导入 GitHub 仓库。</small>
          {error && <div className="project-state project-state--error"><CircleAlert size={16} />{error}</div>}
        </div>
      ) : (
        <>
          {projects.length > 1 && <label className="project-picker"><span>当前项目</span><select value={projectId} onChange={event => selectProject(event.target.value)}>{projects.map(project => <option key={project.project_id} value={project.project_id}>{project.workspace_root}</option>)}</select></label>}
          <section className="project-hero">
            <div className="project-hero__icon"><GitBranch size={18} /></div>
            <div className="project-hero__main"><strong>{overview.summary.message}</strong><code>{overview.workspace_root || '工作区尚未绑定'}</code></div>
            <span className={`project-status project-status--${overview.stage_status}`}>{statusLabel(overview.stage_status)}</span>
            <button type="button" className="project-open-chat" onClick={() => onOpenProjectConversation(overview.project_session_id, `${projectTitle} 项目`, undefined, { projectId: overview.project_id, workspace: overview.workspace_root })}><MessageSquare size={14} />打开项目对话</button>
          </section>
          <section className="project-actions" aria-label="项目动作">
            {PROJECT_ACTIONS.map(action => { const Icon = action.icon; const pending = actionPending === action.id; return (
              <button key={action.id} type="button" disabled={Boolean(actionPending)} onClick={() => void startAction(action.id)}>
                <Icon size={15} />
                <span>{pending ? '正在启动…' : action.label}</span>
              </button>
            ) })}
            {actionError && <div className="project-actions__error" role="alert"><CircleAlert size={14} />{actionError}</div>}
          </section>
          <section className="project-stage-rail" aria-label="项目旅程">
            {STAGES.map(stage => { const Icon = stage.icon; const current = stage.id === activeStage; return <div key={stage.id} className={`project-stage ${current ? 'is-current' : ''}`}><Icon size={15} /><span>{stage.label}</span></div> })}
          </section>
          <section className="project-summary-grid">
            <div><small>当前阶段</small><strong>{STAGES.find(stage => stage.id === activeStage)?.label || '项目体检'}</strong></div>
            <div><small>下一步</small><strong>{overview.next_action}</strong></div>
            <div><small>执行事实</small><strong>{overview.evidence_counts.tool_runs} 次工具 · {overview.evidence_counts.events} 个事件</strong></div>
            <div><small>验证</small><strong>{overview.summary.verification_count ? `${overview.summary.verification_count} 项` : '尚未验证'}</strong></div>
          </section>
          <section className="project-trust-strip" aria-label="运行与可信度">
            <div className="project-trust-strip__heading"><Waypoints size={15} /><span>运行与可信度</span></div>
            <div><small>服务身份</small><strong>{activeProcess ? processIdentityLabel(activeProcess) : '尚未启动服务'}</strong></div>
            <div><small>作品完成度</small><strong className={`project-trust--${quality?.tone || 'neutral'}`}>{quality?.label || '等待执行事实'}</strong></div>
            <div><small>本轮结束原因</small><strong>{terminalReasonLabel(overview.quality_metrics.terminal_reason)}</strong></div>
            <div><small>验收清单</small><strong>{overview.quality_metrics.acceptance_total ? `${overview.quality_metrics.acceptance_passed_count}/${overview.quality_metrics.acceptance_total} 项通过` : '暂无验收清单'}</strong></div>
            <div><small>阶段预算</small><strong>{Object.values(overview.stage_budgets).reduce((sum, item) => sum + item.used, 0)} 次操作</strong></div>
            <div><small>模型成本</small><strong>{overview.quality_metrics.model_rounds} 轮 · {overview.quality_metrics.total_tokens.toLocaleString()} tokens</strong></div>
            <div className="project-budget-track" aria-label="阶段预算明细">
              {Object.entries(overview.stage_budgets).map(([stage, budget]) => (
                <span key={stage} className={budget.status === 'exhausted' ? 'is-exhausted' : ''}>
                  <small>{BUDGET_LABELS[stage] || stage}</small>
                  <b>{budget.used}/{budget.limit}</b>
                </span>
              ))}
            </div>
          </section>
          <FailurePatterns patterns={overview.failure_patterns} />
          <EvidenceDrawer projectId={projectId} overview={overview} />
          <div className="project-drawer-gap" />
          <ProjectMemoriesDrawer projectId={projectId} />
        </>
      )}
    </div>
  )
}
