import React, { useCallback, useEffect, useState } from 'react'
import { Activity, AlertCircle, CheckCircle2, ChevronDown, Clock3, Database, RefreshCw, XCircle } from 'lucide-react'
import { api } from '../../lib/api'
import { formatLocalTimestamp } from '../../lib/time'
import type { AgentTrace, AgentTraceDetail, ObservabilityStatus } from '../../types'

function TraceDetails({ taskId }: { taskId: string }) {
  const [detail, setDetail] = useState<AgentTraceDetail | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    api.getTraceDetail(taskId).then(setDetail).catch(() => setDetail(null)).finally(() => setLoading(false))
  }, [taskId])

  if (loading) return <div className="activity-trace__detail muted">正在读取工具明细…</div>
  if (!detail) return <div className="activity-trace__detail muted">暂无明细</div>
  return (
    <div className="activity-trace__detail">
      {detail.activity.tool_runs.length > 0 && (
        <div className="activity-tool-list">
          {detail.activity.tool_runs.map((run, index) => (
            <div key={`${run.tool_name}-${index}`} className="activity-tool-row">
              <span>{run.tool_name}</span>
              <span className={run.result.success ? 'trace-ok' : 'trace-fail'}>{run.result.success ? '成功' : '失败'}</span>
              <time>{formatLocalTimestamp(run.created_at)}</time>
            </div>
          ))}
        </div>
      )}
      {detail.activity.changesets.length > 0 && (
        <div className="activity-change-list">
          <label>文件变更</label>
          {detail.activity.changesets.flatMap(change => change.files).map(file => <code key={file}>{file}</code>)}
        </div>
      )}
    </div>
  )
}

function TraceRow({ trace }: { trace: AgentTrace }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={`activity-trace ${trace.failed_tool_count > 0 || trace.status === 'failed' ? 'has-failure' : ''}`}>
      <button type="button" className="activity-trace__summary" onClick={() => setOpen(value => !value)} aria-expanded={open}>
        <span className="activity-trace__icon">
          {trace.status === 'completed' ? <CheckCircle2 size={16} /> : trace.status === 'failed' ? <XCircle size={16} /> : <Clock3 size={16} />}
        </span>
        <span className="activity-trace__main">
          <strong>{trace.message || '未命名任务'}</strong>
          <small>{trace.task_id} · {formatLocalTimestamp(trace.updated_at)}</small>
        </span>
        <span className="activity-trace__metrics">
          <b>{trace.tool_count}</b><small>工具</small>
          <b>{trace.changed_file_count}</b><small>文件</small>
          <span className={`trace-badge trace-badge--${trace.verification}`}>{trace.verification === 'passed' ? '验证通过' : trace.verification === 'failed' ? '验证失败' : '未验证'}</span>
        </span>
        <ChevronDown size={15} className={open ? 'is-open' : ''} />
      </button>
      {open && <TraceDetails taskId={trace.task_id} />}
    </div>
  )
}

export function ActivityView() {
  const [traces, setTraces] = useState<AgentTrace[]>([])
  const [observability, setObservability] = useState<ObservabilityStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const refresh = useCallback(() => {
    setLoading(true)
    setError('')
    Promise.all([api.getTraces(), api.getObservability()])
      .then(([nextTraces, nextStatus]) => { setTraces(nextTraces); setObservability(nextStatus) })
      .catch(err => setError(err instanceof Error ? err.message : '读取运行记录失败'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  return (
    <div className="activity-view">
      <header className="activity-view__header">
        <div>
          <div className="activity-view__eyebrow"><Activity size={13} /> OBSERVABILITY</div>
          <h1>运行记录</h1>
          <p>每次 Agent 任务的本地执行事实，便于回看工具、变更和验证。</p>
        </div>
        <button type="button" className="icon-button" onClick={refresh} title="刷新运行记录" aria-label="刷新运行记录"><RefreshCw size={15} /></button>
      </header>

      <section className="observability-strip">
        <div className="observability-card"><Database size={16} /><div><strong>本地记录</strong><span>{observability?.local.enabled ? 'SQLite 已启用' : '未启用'}</span></div><CheckCircle2 size={15} className="trace-ok" /></div>
        <div className="observability-card"><Activity size={16} /><div><strong>LangSmith</strong><span>{observability?.langsmith.enabled ? `已配置 · ${observability.langsmith.project}` : '可选，当前未启用'}</span></div>{observability?.langsmith.enabled ? <CheckCircle2 size={15} className="trace-ok" /> : <AlertCircle size={15} className="trace-muted" />}</div>
      </section>

      <section className="activity-view__list">
        <div className="activity-view__list-header"><h2>最近任务</h2><span>{traces.length} 条记录</span></div>
        {loading ? <div className="activity-empty">正在读取本地记录…</div> : error ? <div className="activity-empty activity-empty--error">{error}<button type="button" onClick={refresh}>重试</button></div> : traces.length === 0 ? <div className="activity-empty">还没有运行记录</div> : traces.map(trace => <TraceRow key={trace.task_id} trace={trace} />)}
      </section>
    </div>
  )
}
