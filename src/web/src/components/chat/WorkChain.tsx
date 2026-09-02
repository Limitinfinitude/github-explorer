import React, { useMemo } from 'react'
import { AlertCircle, Check, CircleDot, Loader2 } from 'lucide-react'
import type { Step } from '../../types'
import { summarizeWorkChain } from '../../lib/workChain'
import { ToolCard } from './ToolCard'

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
        {steps.map((step, index) => (
          <ToolCard key={step.callId ?? `tool-${index}`} step={step} />
        ))}
      </div>
    </div>
  )
}
