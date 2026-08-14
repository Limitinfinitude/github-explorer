import React, { useEffect, useMemo, useState } from 'react'
import { Activity, BookOpen, Check, ChevronDown, CircleAlert, Copy, Download, FileCode2, FolderOpen, GitBranch, MessageSquare, PackageCheck, Play, RefreshCw, Search, ShieldCheck, TerminalSquare, Waypoints } from 'lucide-react'
import { api } from '../../lib/api'
import { evidenceToMarkdown, filterEvidenceEntries } from '../../lib/projectEvidence'
import { formatLocalTimestamp } from '../../lib/time'
import { processIdentityLabel, qualityState } from '../../lib/projectInsights'
import type { ProjectEvidence, ProjectEvidenceFilter, ProjectOverview, ProjectSummary } from '../../types'

const STAGES = [
  { id: 'inspect', label: '项目体检', icon: Search },
  { id: 'run', label: '跑起来', icon: Play },
  { id: 'understand', label: '看懂', icon: FileCode2 },
  { id: 'experiment', label: '实验室', icon: TerminalSquare },
  { id: 'verify', label: '验证', icon: ShieldCheck },
  { id: 'record', label: '记录', icon: Activity },
]

function statusLabel(status: string) {
  return ({ completed: '已完成', incomplete: '未完成', failed: '失败', waiting_approval: '等待确认', running: '进行中', not_started: '未开始' } as Record<string, string>)[status] || status
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

export function ProjectWorkspaceView({
  onOpenProjectConversation,
}: {
  onOpenProjectConversation: (sessionId: string, title: string, userMessage?: string) => void
}) {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [projectId, setProjectId] = useState('')
  const [overview, setOverview] = useState<ProjectOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionPending, setActionPending] = useState('')
  const [actionError, setActionError] = useState('')

  async function refresh() {
    setLoading(true); setError('')
    try {
      const next = await api.getProjects()
      setProjects(next)
      const selected = next.some(project => project.project_id === projectId) ? projectId : (next[0]?.project_id || '')
      setProjectId(selected)
      setOverview(selected ? await api.getProjectOverview(selected) : null)
    } catch (err) { setError(err instanceof Error ? err.message : '读取项目工作台失败') } finally { setLoading(false) }
  }
  useEffect(() => { void refresh() }, [])

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
      onOpenProjectConversation(started.session_id, `${projectTitle} 项目`, label)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : '无法启动项目动作')
    } finally {
      setActionPending('')
    }
  }
  return (
    <div className="project-workspace-view">
      <header className="project-workspace__header">
        <div><div className="project-workspace__eyebrow"><FolderOpen size={13} /> PROJECT WORKBENCH</div><h1>项目工作台</h1><p>从跑通到看懂，再到可追溯的二改实验。</p></div>
        <button type="button" className="icon-button" onClick={() => void refresh()} title="刷新项目状态" aria-label="刷新项目状态"><RefreshCw size={15} /></button>
      </header>
      {loading ? <div className="project-state">正在读取最近项目…</div> : error ? <div className="project-state project-state--error"><CircleAlert size={16} />{error}</div> : !overview ? <div className="project-state"><strong>还没有项目任务</strong><span>先在对话中导入或创建一个项目，工作台会在这里保留运行和实验记录。</span></div> : (
        <>
          {projects.length > 1 && <label className="project-picker"><span>当前项目</span><select value={projectId} onChange={event => { const id = event.target.value; setProjectId(id); setLoading(true); api.getProjectOverview(id).then(setOverview).catch(err => setError(err instanceof Error ? err.message : '读取项目失败')).finally(() => setLoading(false)) }}>{projects.map(project => <option key={project.project_id} value={project.project_id}>{project.workspace_root}</option>)}</select></label>}
          <section className="project-hero">
            <div className="project-hero__icon"><GitBranch size={18} /></div>
            <div className="project-hero__main"><strong>{overview.summary.message}</strong><code>{overview.workspace_root || '工作区尚未绑定'}</code></div>
            <span className={`project-status project-status--${overview.stage_status}`}>{statusLabel(overview.stage_status)}</span>
            <button type="button" className="project-open-chat" onClick={() => onOpenProjectConversation(overview.project_session_id, `${projectTitle} 项目`)}><MessageSquare size={14} />打开项目对话</button>
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
            <div><small>终态仲裁</small><strong className={`project-trust--${quality?.tone || 'neutral'}`}>{quality?.label || '等待执行事实'}</strong></div>
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
          <EvidenceDrawer projectId={projectId} overview={overview} />
        </>
      )}
    </div>
  )
}
