import React, { useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, CircleDashed, HelpCircle, ListChecks, MessageSquare } from 'lucide-react'
import type { AgentEvent } from '../../types'
import { ToolCallCard, type ToolCallStatus } from './ToolCallCard'

// 默认先渲染的条目数（时间线可能上千条，分页避免一次渲染）
const PAGE_SIZE = 120

type TimelineItem =
  | { kind: 'round'; round: number }
  | { kind: 'tool'; callId: string; name: string; args?: unknown; result?: unknown; status: ToolCallStatus; durationMs?: number }
  | { kind: 'text'; text: string }
  | { kind: 'info'; title: string; content: string }
  | { kind: 'approval'; toolName: string; approved?: boolean }
  | { kind: 'divider'; label: string; tone: 'ok' | 'fail' | 'warn' | 'neutral' }
  | { kind: 'alert'; text: string }

const OTHER_LABELS: Record<string, string> = {
  verification: '验证完成', file_changed: '文件已变更', process_started: '进程已启动',
  process_verified: '进程与端口归属已确认', finalization: '事实账本已最终化',
  acceptance_completed: '验收清单已核对', requirement_backlog_loaded: '需求清单已加载',
  runtime_reconciled: '重启状态已对账', task_interrupted: '任务中断（可续跑）',
  task_resumed: '任务已恢复', acceptance_reformatted: '验收陈述已重写',
}

function alertLabel(type: string): string {
  return ({
    error: '运行错误', budget_warning: '预算提醒', stage_budget_exhausted: '阶段预算已用尽',
    tool_repair_exhausted: '工具参数修复机会已用尽', model_response_truncated: '模型输出被截断',
  } as Record<string, string>)[type] || type
}

/** 把事件流整理成时间线条目：tool_call/tool_result 按 call_id 配对成一张卡，model 请求事件作轮次标记不渲染。 */
export function buildTimelineItems(events: AgentEvent[]): TimelineItem[] {
  const resultByCall = new Map<string, AgentEvent>()
  const recoveredCalls = new Set<string>()
  for (const event of events) {
    const payload = event.payload as Record<string, unknown>
    if (event.type === 'tool_result') {
      const callId = String(payload.call_id || '')
      if (callId) resultByCall.set(callId, event)
    } else if (event.type === 'tool_recovered') {
      const failedId = String(payload.failed_call_id || '')
      if (failedId) recoveredCalls.add(failedId)
    }
  }

  const time = (event: AgentEvent) => Date.parse(event.created_at)
  const items: TimelineItem[] = []
  let round = 0
  for (const event of events) {
    const payload = event.payload as Record<string, unknown>
    if (event.type === 'model_request_started' || event.type === 'model_request_completed') {
      // 只用作「第 N 轮」分组标记，不渲染
      const r = payload.round
      if (typeof r === 'number' && r > 0 && r !== round) {
        round = r
        items.push({ kind: 'round', round: r })
      }
      continue
    }
    if (event.type === 'thinking' || event.type === 'token' || event.type === 'context_usage') continue
    if (event.type === 'tool_call') {
      const callId = String(payload.call_id || '')
      const result = resultByCall.get(callId)
      const status: ToolCallStatus = recoveredCalls.has(callId)
        ? 'recovered'
        : result
          ? (result.payload.success === false ? 'failed' : 'success')
          : 'pending'
      const start = time(event)
      const end = result ? time(result) : 0
      items.push({
        kind: 'tool', callId, name: String(payload.name || payload.tool_name || '工具'),
        args: payload.args, result: result?.payload, status,
        durationMs: start && end > start ? end - start : undefined,
      })
      continue
    }
    if (event.type === 'tool_result') continue // 已并入配对卡
    if (event.type === 'narration') {
      const text = String(payload.content || '').trim()
      if (text) items.push({ kind: 'text', text })
      continue
    }
    if (event.type === 'plan') {
      const steps = Array.isArray(payload.steps) ? payload.steps.map(step => String(step)) : []
      if (steps.length) items.push({ kind: 'info', title: '计划', content: steps.map((step, index) => `${index + 1}. ${step}`).join('\n') })
      continue
    }
    if (event.type === 'repo_map') {
      const content = String(payload.content || '')
      if (content) items.push({ kind: 'info', title: `仓库结构（${String(payload.files_scanned ?? '?')} 文件）`, content })
      continue
    }
    if (event.type === 'approval_required' || event.type === 'approval_resolved') {
      items.push({
        kind: 'approval',
        toolName: String(payload.tool_name || '操作'),
        approved: event.type === 'approval_resolved' ? Boolean(payload.approved) : undefined,
      })
      continue
    }
    if (event.type === 'task_started') { items.push({ kind: 'divider', label: '任务开始', tone: 'neutral' }); continue }
    if (event.type === 'task_completed') { items.push({ kind: 'divider', label: '任务完成', tone: 'ok' }); continue }
    if (event.type === 'task_failed') { items.push({ kind: 'divider', label: '任务失败', tone: 'fail' }); continue }
    if (event.type === 'task_cancelled') { items.push({ kind: 'divider', label: '任务已取消', tone: 'neutral' }); continue }
    if (event.type === 'task_waiting_approval') { items.push({ kind: 'divider', label: '等待确认', tone: 'warn' }); continue }
    if (event.type === 'context_compacted') { items.push({ kind: 'divider', label: '上下文已压缩', tone: 'neutral' }); continue }
    if (event.type === 'error' || event.type === 'budget_warning' || event.type === 'stage_budget_exhausted' || event.type === 'tool_repair_exhausted' || event.type === 'model_response_truncated') {
      const text = String(payload.message || payload.content || payload.error || '')
      items.push({ kind: 'alert', text: text ? `${alertLabel(event.type)}：${text}` : alertLabel(event.type) })
      continue
    }
    const other = OTHER_LABELS[event.type]
    if (other) items.push({ kind: 'divider', label: other, tone: 'neutral' })
    // 未识别类型静默跳过（保持时间线干净）
  }
  return items
}

/** 通用记录时间线：左竖线 + 类型化节点 + 「第 N 轮」分组。 */
export function RecordTimeline({ events }: { events: AgentEvent[] }) {
  const [limit, setLimit] = useState(PAGE_SIZE)
  const items = useMemo(() => buildTimelineItems(events), [events])

  if (items.length === 0) return <div className="record-timeline__empty">没有可展示的动作记录</div>
  const visible = items.slice(0, limit)

  return (
    <div className="record-timeline">
      {visible.map((item, index) => {
        if (item.kind === 'round') {
          return <div key={`r${index}`} className="record-round">第 {item.round} 轮</div>
        }
        if (item.kind === 'tool') {
          return (
            <div key={`t${item.callId}-${index}`} className="record-timeline__node">
              <span className={`record-timeline__dot record-timeline__dot--${item.status}`} />
              <ToolCallCard
                name={item.name}
                args={item.args}
                result={item.result}
                status={item.status}
                durationMs={item.durationMs}
              />
            </div>
          )
        }
        if (item.kind === 'text') {
          return (
            <div key={`x${index}`} className="record-timeline__node record-timeline__node--text">
              <MessageSquare size={12} className="record-timeline__glyph" />
              <p>{item.text}</p>
            </div>
          )
        }
        if (item.kind === 'info') {
          return (
            <div key={`i${index}`} className="record-timeline__node record-timeline__node--info">
              <ListChecks size={12} className="record-timeline__glyph" />
              <details className="record-info">
                <summary>{item.title}</summary>
                <pre>{item.content}</pre>
              </details>
            </div>
          )
        }
        if (item.kind === 'approval') {
          return (
            <div key={`a${index}`} className="record-timeline__node record-timeline__node--approval">
              <HelpCircle size={12} className="record-timeline__glyph" />
              <span>
                请求确认 <b>{item.toolName}</b>
                {item.approved === undefined ? '' : item.approved ? ' · 已批准' : ' · 已拒绝'}
              </span>
            </div>
          )
        }
        if (item.kind === 'alert') {
          return (
            <div key={`w${index}`} className="record-timeline__node record-timeline__node--alert">
              <AlertTriangle size={12} className="record-timeline__glyph" />
              <span>{item.text}</span>
            </div>
          )
        }
        // divider
        const tone = item.tone
        const Icon = tone === 'ok' ? CheckCircle2 : tone === 'fail' ? AlertTriangle : CircleDashed
        return (
          <div key={`d${index}`} className={`record-divider record-divider--${tone}`}>
            <Icon size={12} />
            <span>{item.label}</span>
          </div>
        )
      })}
      {items.length > limit && (
        <button type="button" className="record-timeline__more" onClick={() => setLimit(v => v + PAGE_SIZE)}>
          加载更多（剩余 {items.length - limit} 条）
        </button>
      )}
    </div>
  )
}
