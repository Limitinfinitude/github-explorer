import React, { useMemo, useState } from 'react'
import { Brain, ChevronDown } from 'lucide-react'
import type { ThinkingSegment } from '../../types'

function firstLine(text: string | undefined): string {
  if (typeof text !== 'string' || text.length === 0) return ''
  const newline = text.indexOf('\n')
  return newline === -1 ? text : text.slice(0, newline)
}

function latestLine(text: string | undefined): string {
  if (typeof text !== 'string' || text.length === 0) return ''
  const visible = text.trimEnd()
  const newline = visible.lastIndexOf('\n')
  return newline === -1 ? visible : visible.slice(newline + 1)
}

/** 段落标题：从思考内容提取这一段在干什么（首行非标记文本，截短）。
 * 思考文本大多是「用户要 X，我先 Y……」式的行动推理，首行就是最好的摘要；
 * 没有时回退到该段思考产生的动作数描述。 */
function segmentTitle(content: string, index: number, total: number): string {
  const lines = content.split('\n').map(line => line.trim()).filter(Boolean)
  const skipMarkers = /^[#>*\-•\d]/  // markdown 噪声行
  const lead = lines.find(line => !skipMarkers.test(line) && line.length > 6)
  if (lead) {
    const title = lead.length > 42 ? `${lead.slice(0, 42)}…` : lead
    return index === 0 ? title : `接着：${title}`
  }
  return index === 0 ? '开始思考' : index === total - 1 ? '最后思考' : '继续思考'
}

/** 思考流：折叠显示「正在想什么」（最新一段的末行），展开为叙事时间线。
 * 不用「第 N 轮」标记——轮数对读者没有语义，每段标题说明这轮在干什么。 */
export function ReasoningRow({ segments, running }: { segments: ThinkingSegment[]; running: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const [limit, setLimit] = useState(3)

  const validSegments = useMemo(
    () => segments.filter(seg => typeof seg?.content === 'string' && seg.content.length > 0),
    [segments],
  )
  if (validSegments.length === 0) return null

  const latest = validSegments[validSegments.length - 1]
  const visibleSegments = expanded ? validSegments.slice(0, limit) : []

  return (
    <div className={`think-row ${running ? 'is-running' : ''}`}>
      <button
        type="button"
        className="think-row__toggle"
        onClick={() => setExpanded(v => !v)}
        aria-expanded={expanded}
      >
        {running && <span className="think-row__pulse" />}
        <Brain size={13} />
        <span className="think-row__title">{running ? '思考中' : '思考过程'}</span>
        <span className="think-row__summary">
          {running ? latestLine(latest.content) : firstLine(validSegments[0].content)}
        </span>
        {validSegments.length > 1 && <span className="think-row__count">{validSegments.length}</span>}
        <ChevronDown size={12} className={expanded ? 'is-open' : ''} />
      </button>
      {expanded && (
        <div className="think-row__body think-row__body--timeline">
          {visibleSegments.map((segment, index) => (
            <div key={index} className="think-segment">
              <span className="think-segment__marker">{segmentTitle(segment.content, index, validSegments.length)}</span>
              <div className="think-segment__content">{segment.content}</div>
            </div>
          ))}
          {limit < validSegments.length && (
            <button type="button" className="think-segment__more" onClick={() => setLimit(v => v + 5)}>
              继续展开（还有 {validSegments.length - limit} 段）
            </button>
          )}
        </div>
      )}
    </div>
  )
}
