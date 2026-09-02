import React, { useEffect, useState } from 'react'
import { Brain, ChevronDown } from 'lucide-react'
import type { CmdBlockData, Step, ThinkingSegment, WorkTimelineItem } from '../../types'
import { ToolCard } from './ToolCard'
import { CmdBlock } from './CmdBlock'
import { useStopBubbleRef } from '../../lib/stopBubble'

function fmtElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`
}

/** 思考卡：与工具卡同构的可折叠卡——头部（图标+「思考」+首行摘要），
 * 正文按原文换行（长 URL/英文长词强制断行），默认收起。 */
function ThinkCard({ content }: { content: string }) {
  const text = content.trim()
  const stopRef = useStopBubbleRef<HTMLDetailsElement>()
  if (!text) return null
  const lead = text.split('\n').map(line => line.trim()).filter(Boolean).find(line => line.length > 6) ?? text.slice(0, 40)
  const title = lead.length > 46 ? `${lead.slice(0, 46)}…` : lead
  return (
    <details className="think-card" ref={stopRef}>
      <summary><Brain size={13} /><span className="think-card__name">思考</span>
        <span className="think-card__summary">{title}</span>
      </summary>
      <div className="think-card__body">{text}</div>
    </details>
  )
}

/** 旁白卡：轻量可折叠行（"正在搜索 xxx"），点击展开看完整原文。 */
function NoteCard({ text }: { text: string }) {
  const stopRef = useStopBubbleRef<HTMLDetailsElement>()
  const t = text.trim()
  if (!t) return null
  const short = t.length > 52 ? `${t.slice(0, 52)}…` : t
  return (
    <details className="note-card" ref={stopRef}>
      <summary><span className="note-card__text">{short}</span></summary>
      <div className="note-card__body">{t}</div>
    </details>
  )
}

/** 统一「工作过程」容器（对齐 ZCode）：
 * - 运行中：header「工作中 N分N秒」自动展开，内部按事件顺序交错显示 思考/工具/命令/旁白；
 * - 完成：header「已工作 N分N秒」自动收起，点击展开全过程；输出结果（正文）在容器之外。
 */
export default function WorkProcess({
  running,
  elapsedSec,
  startedAt,
  steps,
  cmdBlocks,
  narrations,
  thinking,
  timeline,
}: {
  running: boolean
  elapsedSec?: number
  startedAt?: number | null
  steps: Step[]
  cmdBlocks: CmdBlockData[]
  narrations: string[]
  thinking: ThinkingSegment[]
  timeline?: WorkTimelineItem[]
}) {
  const [open, setOpen] = useState(running)
  const [now, setNow] = useState(Date.now())
  const [limit, setLimit] = useState(60)

  // 运行中每秒跳表；结束时自动收起（运行→完成的瞬间）
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  useEffect(() => {
    if (!running) setOpen(false)
  }, [running])

  const liveElapsed = startedAt
    ? Math.max(0, Math.round(((running ? now : Date.now()) - startedAt) / 1000))
    : (elapsedSec ?? 0)
  const toolCount = steps.length + cmdBlocks.length
  const thinkCount = thinking.length

  // 时间线渲染：有 timeline 按事件顺序交错；老消息没有 timeline 时退化为分块堆叠
  const hasTimeline = Boolean(timeline && timeline.length)
  const thinkByRound = new Map<number, ThinkingSegment[]>()
  for (const seg of thinking) {
    const list = thinkByRound.get(seg.round) ?? []
    list.push(seg)
    thinkByRound.set(seg.round, list)
  }
  const thinkCursor = new Map<number, number>()
  const nextThink = (round: number): ThinkingSegment | null => {
    const list = thinkByRound.get(round) ?? []
    const idx = thinkCursor.get(round) ?? 0
    if (idx >= list.length) return null
    thinkCursor.set(round, idx + 1)
    return list[idx]
  }
  const stepByCall = new Map(steps.filter(s => s.callId).map(s => [s.callId!, s]))
  const cmdById = new Map(cmdBlocks.map(b => [b.id, b]))

  const visibleTimeline = (timeline ?? []).slice(-limit)

  return (
    <details
      className={`work-process ${running ? 'is-running' : 'is-done'} ${open ? 'is-open' : ''}`}
      open={open}
      onToggle={e => {
        // React 合成 toggle 会冒泡：内层卡片的开合也会触发这里的 handler，
        // 只响应外层自身的 toggle（e.target 必须是本 details）
        if (e.target === e.currentTarget) setOpen((e.target as HTMLDetailsElement).open)
      }}
    >
      <summary className="work-process__head">
        {running && <span className="work-process__spinner" />}
        <span className="work-process__label">{running ? '工作中' : '已工作'}</span>
        <span className="work-process__time">{fmtElapsed(liveElapsed)}</span>
        {running && narrations.length > 0 && (
          <span className="work-process__current">{narrations[narrations.length - 1]}</span>
        )}
        {!running && toolCount > 0 && <span className="work-process__chip">{toolCount} 个操作</span>}
        {!running && thinkCount > 0 && <span className="work-process__chip">{thinkCount} 段思考</span>}
        <ChevronDown size={13} className="work-process__chevron" />
      </summary>
      <div className="work-process__body">
        {hasTimeline ? (
          visibleTimeline.map((item, index) => {
            if (item.kind === 'think') {
              const seg = nextThink(item.round)
              return seg ? (
                <ThinkCard key={`t${index}`} content={seg.content} />
              ) : null
            }
            if (item.kind === 'tool') {
              const step = stepByCall.get(item.callId)
              return step ? <ToolCard key={`k${index}`} step={step} /> : null
            }
            if (item.kind === 'cmd') {
              const block = cmdById.get(item.id)
              return block ? <CmdBlock key={`c${index}`} block={block} /> : null
            }
            // 旁白与工具卡信息重复（"正在搜索 x" vs 网页搜索卡）：有完整时间线时跳过，
            // 仅作为运行中折叠态的实时摘要（head 上）与旧消息兜底
            return null
          })
        ) : (
          <>
            {thinking.map((seg, index) => (
              <ThinkCard key={`t${index}`} content={seg.content} />
            ))}
            {steps.map((step, index) => <ToolCard key={`k${index}`} step={step} />)}
            {cmdBlocks.map((block, index) => <CmdBlock key={`c${index}`} block={block} />)}
            {steps.length === 0 && cmdBlocks.length === 0 && narrations.map((line, index) => (
              <NoteCard key={`n${index}`} text={line} />
            ))}
          </>
        )}
        {hasTimeline && (timeline?.length ?? 0) > limit && (
          <button type="button" className="work-process__more" onClick={() => setLimit(v => v + 60)}>
            继续展开（前面还有 {(timeline?.length ?? 0) - limit} 条）
          </button>
        )}
        {!running && toolCount === 0 && thinkCount === 0 && narrations.length === 0 && (
          <div className="work-process__note">本次没有记录工作过程</div>
        )}
      </div>
    </details>
  )
}
