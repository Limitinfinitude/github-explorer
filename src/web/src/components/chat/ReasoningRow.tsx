import React, { useState } from 'react'
import { Brain, ChevronDown } from 'lucide-react'

function firstLine(text: string): string {
  const newline = text.indexOf('\n')
  return newline === -1 ? text : text.slice(0, newline)
}

function latestLine(text: string): string {
  const visible = text.trimEnd()
  const newline = visible.lastIndexOf('\n')
  return newline === -1 ? visible : visible.slice(newline + 1)
}

/** 紧凑的 Think 折叠行：折叠时显示第一行/运行中最新行摘要，点击展开正文。 */
export function ReasoningRow({ text, running }: { text: string; running: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const summary = running ? latestLine(text) : firstLine(text)
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
        <span className="think-row__summary">{summary || (running ? '思考中…' : '')}</span>
        <ChevronDown size={12} className={expanded ? 'is-open' : ''} />
      </button>
      {expanded && <div className="think-row__body">{text}</div>}
    </div>
  )
}
