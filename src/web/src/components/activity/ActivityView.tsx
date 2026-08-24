import React, { useCallback, useEffect, useState } from 'react'
import { Activity, AlertTriangle, CheckCircle2, ChevronDown, Clock3, Database, Gauge, RefreshCw, Wrench, XCircle } from 'lucide-react'
import { api } from '../../lib/api'
import { localCoverageLabels } from '../../lib/observability'
import { formatLocalTimestamp } from '../../lib/time'
import type { AgentEvent, AgentTrace, AgentTraceDetail, ObservabilityStatus } from '../../types'

type TokenUsage = {
  total: { calls: number; input_tokens: number; output_tokens: number; total_tokens: number; cache_hit_tokens?: number; cache_hit_rate?: number | null }
  last_5h: { calls: number; input_tokens: number; output_tokens: number; total_tokens: number; cache_hit_tokens?: number; cache_hit_rate?: number | null }
  by_day: Array<{ date: string; input: number; output: number; calls: number; total_tokens: number }>
}

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)}K`
  return String(value)
}

function TokenUsageStrip() {
  const [usage, setUsage] = useState<TokenUsage | null>(null)

  const load = useCallback(() => {
    fetch('/api/agent/token-usage?days=7')
      .then(res => (res.ok ? res.json() : null))
      .then(data => setUsage(data))
      .catch(() => setUsage(null))
  }, [])

  useEffect(() => { load() }, [load])

  if (!usage) return null
  const days = usage.by_day.slice(-7)
  const maxDay = Math.max(1, ...days.map(d => d.total_tokens))
  return (
    <div className="token-usage-strip" aria-label="Token 消耗统计">
      <div className="token-usage-strip__headline">
        <span>近 5 小时 <strong>{formatTokens(usage.last_5h.total_tokens)}</strong>（{usage.last_5h.calls} 次调用）</span>
        <span>7 日累计 <strong>{formatTokens(usage.total.total_tokens)}</strong></span>
        <span className="token-usage-strip__hint">输入 {formatTokens(usage.total.input_tokens)} / 输出 {formatTokens(usage.total.output_tokens)}</span>
        {usage.total.cache_hit_rate != null && (
          <span className="token-usage-strip__hint">缓存命中 {(usage.total.cache_hit_rate * 100).toFixed(1)}%</span>
        )}
      </div>
      <div className="token-usage-strip__bars">
        {days.map(day => (
          <div key={day.date} className="token-usage-strip__bar" title={`${day.date}：${day.total_tokens.toLocaleString()} tokens（${day.calls} 次调用）`}>
            <div className="token-usage-strip__bar-fill" style={{ height: `${Math.max(4, Math.round(day.total_tokens / maxDay * 100))}%` }} />
            <span>{day.date.slice(5)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function eventLabel(event: AgentEvent): string {
  const name = typeof event.payload.name === 'string' ? event.payload.name : ''
  if (event.type === 'tool_call') return name ? `调用 ${name}` : '调用工具'
  if (event.type === 'tool_result') return name ? `${name} 返回结果` : '工具返回结果'
  if (event.type === 'tool_recovered') return name ? `${name} 失败已恢复` : '工具失败已恢复'
  if (event.type === 'model_request_completed') {
    const usage = event.payload.usage as Record<string, unknown> | undefined
    const latency = typeof event.payload.latency_ms === 'number' ? `${Math.round(event.payload.latency_ms)} ms` : ''
    const tokens = typeof usage?.total_tokens === 'number' ? `${usage.total_tokens} tokens` : ''
    return ['模型响应', latency, tokens].filter(Boolean).join(' · ')
  }
  return {
    task_started: '任务开始',
    task_completed: '任务完成',
    task_failed: '任务失败',
    task_waiting_approval: '等待确认',
    task_finished: '任务结束（未完成）',
    model_request_started: '模型请求',
    model_request_failed: '模型请求失败',
    approval_required: '请求操作确认',
    approval_resolved: event.payload.approved ? '已批准操作' : '已拒绝操作',
    file_changed: '文件已变更',
    verification: '验证完成',
    process_started: '进程已启动',
    process_verified: '进程与端口归属已确认',
    finalization: '事实账本已最终化',
    tool_repair_exhausted: '工具参数修复机会已用尽',
    stage_budget_exhausted: '阶段预算已用尽',
    runtime_reconciled: '重启状态已对账',
    context_compacted: '上下文已压缩',
    model_response_truncated: '模型输出被截断',
    error: '运行错误',
  }[event.type] || event.type
}

function terminalReasonLabel(reason?: string) {
  return ({
    completed: '正常完成',
    stage_budget_exhausted: '阶段预算阻塞',
    diagnostic_budget_exhausted: '诊断预算阻塞',
    approval_pending: '等待审批',
    tool_repair_exhausted: '工具修复耗尽',
    unrecovered_tool_failure: '工具失败未恢复',
    interrupted: '运行被中断',
    cancelled: '已取消',
    model_error: '模型请求失败',
    no_execution_facts: '没有执行事实',
    running: '仍在运行',
  } as Record<string, string>)[reason || ''] || '未分类结束'
}

type TraceFilters = {
  status: string
  terminal_reason: string
  completion_evidence: string
  workspace: string
  from: string
  to: string
}

const EMPTY_TRACE_FILTERS: TraceFilters = {
  status: '',
  terminal_reason: '',
  completion_evidence: '',
  workspace: '',
  from: '',
  to: '',
}

const STAGE_LABELS: Record<string, string> = {
  inspect: '体检', implement: '实施', test: '测试', run: '运行验收',
}

// 事件时间线中应隐藏的内部噪音事件（无信息量）
const NOISE_EVENT_TYPES = new Set([
  'thinking', 'token', 'model_request_started', 'model_request_completed',
  'model_request_retrying', 'model_request_failed',
])

// 按 stage 聚合工具调用，形成语义阶段摘要
function aggregateStages(events: AgentEvent[]): Array<{ stage: string; toolCount: number; failed: number }> {
  const stages = new Map<string, { toolCount: number; failed: number }>()
  for (const event of events) {
    if (event.type !== 'tool_call' && event.type !== 'tool_result') continue
    const stage = typeof event.payload.stage === 'string' ? event.payload.stage : 'implement'
    const current = stages.get(stage) || { toolCount: 0, failed: 0 }
    if (event.type === 'tool_call') current.toolCount += 1
    if (event.type === 'tool_result' && event.payload.success === false) current.failed += 1
    stages.set(stage, current)
  }
  return Array.from(stages.entries()).map(([stage, stats]) => ({ stage, ...stats }))
}

function TraceDetails({ taskId }: { taskId: string }) {
  const [detail, setDetail] = useState<AgentTraceDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [showRaw, setShowRaw] = useState(false)

  useEffect(() => {
    setLoading(true)
    api.getTraceDetail(taskId).then(setDetail).catch(() => setDetail(null)).finally(() => setLoading(false))
  }, [taskId])

  if (loading) return <div className="activity-trace__detail muted">正在读取工具明细…</div>
  if (!detail) return <div className="activity-trace__detail muted">暂无明细</div>
  const stageSummary = aggregateStages(detail.activity.events)
  return (
    <div className="activity-trace__detail">
      {stageSummary.length > 0 && (
        <div className="activity-stage-summary" aria-label="阶段摘要">
          {stageSummary.map(item => (
            <div key={item.stage} className={`activity-stage-summary__item ${item.failed > 0 ? 'is-failed' : ''}`}>
              <strong>{STAGE_LABELS[item.stage] || item.stage}</strong>
              <span>{item.toolCount} 次工具{item.failed > 0 ? ` · ${item.failed} 失败` : ''}</span>
            </div>
          ))}
        </div>
      )}
      {detail.activity.events?.length > 0 && (
        <>
          <button type="button" className="activity-event-toggle" onClick={() => setShowRaw(v => !v)} aria-expanded={showRaw}>
            {showRaw ? '收起详细事件' : `显示全部 ${detail.activity.events.length} 条事件`}
          </button>
          {(showRaw ? detail.activity.events : detail.activity.events.filter(event => !NOISE_EVENT_TYPES.has(event.type))).map(event => (
            <div key={`${event.sequence}-${event.type}`} className={`activity-event-row activity-event-row--${event.type}`}>
              <span className="activity-event-row__sequence">{String(event.sequence).padStart(2, '0')}</span>
              <strong>{eventLabel(event)}</strong>
              <time>{formatLocalTimestamp(event.created_at)}</time>
            </div>
          ))}
        </>
      )}
      {detail.activity.tool_runs.length > 0 && (
        <div className="activity-tool-list">
          {detail.activity.tool_runs.map((run, index) => (
            <div key={`${run.tool_name}-${index}`} className="activity-tool-row">
              <span>{run.tool_name}</span>
              <span className={run.result.success ? 'trace-ok' : run.recovered_by_call_id ? 'trace-recovered' : 'trace-fail'}>
                {run.result.success ? '成功' : run.recovered_by_call_id ? '已恢复' : '失败'}
              </span>
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

function TraceRow({ trace, onResume }: { trace: AgentTrace; onResume: (trace: AgentTrace) => void }) {
  const [open, setOpen] = useState(false)
  const evidenceLabel = trace.completion_evidence === 'verified' ? '作品已验证' : trace.completion_evidence === 'partial' ? '作品部分完成' : '无完成证据'
  const evidenceTone = trace.completion_evidence === 'verified' ? 'passed' : trace.completion_evidence === 'partial' ? 'recovered' : 'failed'
  return (
    <div className={`activity-trace ${trace.failed_tool_count > 0 || trace.status === 'failed' ? 'has-failure' : ''}`}>
      <button type="button" className="activity-trace__summary" onClick={() => setOpen(value => !value)} aria-expanded={open}>
        <span className="activity-trace__icon">
          {trace.status === 'completed' ? <CheckCircle2 size={16} /> : trace.status === 'failed' ? <XCircle size={16} /> : <Clock3 size={16} />}
        </span>
        <span className="activity-trace__main">
          <strong>{trace.message_encoding_status === 'legacy_corrupted' ? '旧标题已损坏（原文不可恢复）' : trace.message || '未命名任务'}</strong>
          <small>{trace.task_id} · {formatLocalTimestamp(trace.updated_at)}</small>
        </span>
        <span className="activity-trace__metrics">
          <b>{trace.tool_count}</b><small>工具</small>
           <b>{trace.changed_file_count}</b><small>文件</small>
           {trace.recovered_tool_count > 0 && <span className="trace-badge trace-badge--recovered">{trace.recovered_tool_count} 已恢复</span>}
           <span className={`trace-badge trace-badge--${trace.verification}`}>{trace.verification === 'passed' ? '验证通过' : trace.verification === 'failed' ? '验证失败' : '未验证'}</span>
           <span className={`trace-badge trace-badge--${evidenceTone}`}>{evidenceLabel}</span>
           <span className="trace-badge trace-badge--reason">{terminalReasonLabel(trace.terminal_reason)}</span>
           {trace.model_error_count ? <span className="trace-badge trace-badge--failed">模型错误 {trace.model_error_count}</span> : null}
           {trace.provider_truncation_count ? <span className="trace-badge trace-badge--recovered">输出截断 {trace.provider_truncation_count}</span> : null}
           {trace.message_encoding_status === 'legacy_corrupted' ? <span className="trace-badge trace-badge--failed">编码异常</span> : null}
         </span>
        <ChevronDown size={15} className={open ? 'is-open' : ''} />
      </button>
      {trace.status === 'interrupted' && trace.resume_available && (
        <div className="activity-trace__actions">
          <button type="button" onClick={() => onResume(trace)}>继续任务</button>
        </div>
      )}
      {open && <TraceDetails taskId={trace.task_id} />}
      </div>
  )
}

export function ActivityView() {
  const [traces, setTraces] = useState<AgentTrace[]>([])
  const [observability, setObservability] = useState<ObservabilityStatus | null>(null)
  const [filters, setFilters] = useState<TraceFilters>(EMPTY_TRACE_FILTERS)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const refresh = useCallback(() => {
    setLoading(true)
    setError('')
    Promise.all([api.getTraces(100, filters), api.getObservability()])
      .then(([nextTraces, nextStatus]) => { setTraces(nextTraces); setObservability(nextStatus) })
      .catch(err => setError(err instanceof Error ? err.message : '读取运行记录失败'))
      .finally(() => setLoading(false))
  }, [filters])

  const resumeTask = useCallback(async (trace: AgentTrace) => {
    try {
      await api.resumeTask(trace.session_id, trace.task_id)
      refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : '恢复任务失败')
    }
  }, [refresh])

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
        <div className="observability-card observability-card--local">
          <Database size={16} />
          <div><strong>本地观测总线</strong><span>{observability?.local.enabled ? `${observability.local.storage} 已启用 · 数据保留在本机` : '未启用'}</span></div>
          <CheckCircle2 size={15} className="trace-ok" />
          <div className="observability-coverage" aria-label="本地观测覆盖范围">
            {localCoverageLabels(observability?.local.coverage).map(item => <span key={item}>{item}</span>)}
          </div>
        </div>
        <div className="observability-summary" aria-label="本地观测汇总">
          <div><Gauge size={14} /><span>任务</span><strong>{observability?.local.summary.task_count ?? 0}</strong></div>
          <div><CheckCircle2 size={14} /><span>正常完成</span><strong>{observability?.local.summary.status_counts.completed ?? 0}</strong></div>
          <div><AlertTriangle size={14} /><span>预算阻塞</span><strong>{observability?.local.summary.budget_exhausted_count ?? 0}</strong></div>
          <div><Wrench size={14} /><span>失败 / 恢复</span><strong>{observability?.local.summary.failed_tool_count ?? 0} / {observability?.local.summary.recovered_tool_count ?? 0}</strong></div>
          <div><AlertTriangle size={14} /><span>已验证未收尾</span><strong>{observability?.local.summary.false_incomplete_count ?? 0}</strong></div>
          <div><Clock3 size={14} /><span>平均模型延迟</span><strong>{observability?.local.summary.average_model_latency_ms ?? 0} ms</strong></div>
          <div><span>Token</span><strong>{(observability?.local.summary.total_tokens ?? 0).toLocaleString()}</strong></div>
        </div>
        <TokenUsageStrip />
      </section>

      <section className="activity-view__filters" aria-label="运行记录筛选">
        <select value={filters.status} onChange={event => setFilters(current => ({ ...current, status: event.target.value }))} aria-label="任务状态">
          <option value="">全部状态</option>
          <option value="completed">已完成</option>
          <option value="incomplete">未完成</option>
          <option value="failed">失败</option>
          <option value="interrupted">中断</option>
          <option value="cancelled">已取消</option>
          <option value="running">运行中</option>
        </select>
        <select value={filters.terminal_reason} onChange={event => setFilters(current => ({ ...current, terminal_reason: event.target.value }))} aria-label="结束原因">
          <option value="">全部结束原因</option>
          <option value="completed">正常完成</option>
          <option value="stage_budget_exhausted">阶段预算阻塞</option>
          <option value="unrecovered_tool_failure">工具失败未恢复</option>
          <option value="model_error">模型请求失败</option>
          <option value="interrupted">运行中断</option>
        </select>
        <select value={filters.completion_evidence} onChange={event => setFilters(current => ({ ...current, completion_evidence: event.target.value }))} aria-label="作品证据">
          <option value="">全部证据</option>
          <option value="verified">已验证</option>
          <option value="partial">部分完成</option>
          <option value="none">无证据</option>
        </select>
        <input value={filters.workspace} onChange={event => setFilters(current => ({ ...current, workspace: event.target.value }))} placeholder="工作区路径" aria-label="工作区路径" />
        <label><span>从</span><input type="date" value={filters.from} onChange={event => setFilters(current => ({ ...current, from: event.target.value }))} aria-label="开始日期" /></label>
        <label><span>到</span><input type="date" value={filters.to} onChange={event => setFilters(current => ({ ...current, to: event.target.value }))} aria-label="结束日期" /></label>
        <button type="button" className="activity-filter-clear" onClick={() => setFilters(EMPTY_TRACE_FILTERS)} disabled={!Object.values(filters).some(Boolean)}>清除</button>
      </section>

      <section className="activity-view__list">
        <div className="activity-view__list-header"><h2>最近任务</h2><span>{traces.length} 条记录</span></div>
        {loading ? <div className="activity-empty">正在读取本地记录…</div> : error ? <div className="activity-empty activity-empty--error">{error}<button type="button" onClick={refresh}>重试</button></div> : traces.length === 0 ? <div className="activity-empty">还没有运行记录</div> : traces.map(trace => <TraceRow key={trace.task_id} trace={trace} onResume={resumeTask} />)}
      </section>
    </div>
  )
}
