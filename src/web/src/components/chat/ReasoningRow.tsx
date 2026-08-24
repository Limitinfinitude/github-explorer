import React, { useState } from 'react'
import { Brain, ChevronDown } from 'lucide-react'
import type { ThinkingSegment } from '../../types'

function firstLine(text: string): string {
  const newline = text.indexOf('\n')
  return newline === -1 ? text : text.slice(0, newline)
}

function latestLine(text: string): string {
  const visible = text.trimEnd()
  const newline = visible.lastIndexOf('\n')
  return newline === -1 ? visible : visible.slice(newline + 1)
}

/** 连续时间线形式的思维链：按 round 分组连缀展示，点击段可展开。 */
export function ReasoningRow({ segments, running }: { segments: ThinkingSegment[]; running: boolean }) {
  const [expanded, setExpanded] = useState(false)

  if (segments.length === 0) return null

  return (
    <div className={`think-row ${running ? 'is-running' : ''}`}>
      <button
        type="button"
        className="think-row__toggle"
        onClick={() => setExpanded(v => !v)}
        aria-expanded={expanded}
      >
        <Brain size={13} />
        <span className="think-row__title">Think</span>
        <span className="think-row__summary">
          {running ? latestLine(segments[segments.length - 1].content) : firstLine(segments[0].content)}
        </span>
        <ChevronDown size={12} className={expanded ? 'is-open' : ''} />
      </button>
      {expanded && (
        <div className="think-row__body think-row__body--timeline">
          {segments.map((segment, index) => (
            <div key={index} className="think-segment">
              <span className="think-segment__marker">第 {segment.round + 1} 轮</span>
              <div className="think-segment__content">{segment.content}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
