import React, { useEffect, useRef, useState } from 'react'
import type { Message, Step, CmdBlockData, ThinkingSegment } from '../../types'
import { MessageItem } from './MessageItem'
import { WorkChain } from './WorkChain'
import { CmdBlock } from './CmdBlock'
import { ReasoningRow } from './ReasoningRow'
import { displayResponseContent } from '../../lib/responseDisplay'

interface Props {
  messages: Message[]
  isGenerating: boolean
  streamSteps: Step[]
  streamCmdBlocks: CmdBlockData[]
  streamNarration: string[]
  streamThinking: ThinkingSegment[]
  streamContent: string
  startTime: number
}

export function MessageList({ messages, isGenerating, streamSteps, streamCmdBlocks, streamNarration, streamThinking, streamContent, startTime }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const [renderedStreamContent, setRenderedStreamContent] = useState('')
  const [stickToBottom, setStickToBottom] = useState(true)
  const elapsed = Math.round((Date.now() - startTime) / 1000)
  const displayStreamContent = displayResponseContent(streamContent, false)

  // 用户向上翻阅历史时暂停自动滚动，回到底部附近恢复跟随
  const handleScroll = () => {
    const el = listRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    setStickToBottom(distance < 120)
  }

  useEffect(() => {
    if (stickToBottom) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages.length, streamContent, streamCmdBlocks.length, streamNarration.length, streamThinking, stickToBottom])

  useEffect(() => {
    if (!displayStreamContent) {
      setRenderedStreamContent('')
      return
    }
    let active = true
    Promise.all([import('marked'), import('dompurify')]).then(([markedModule, purifyModule]) => {
      markedModule.marked.setOptions({ breaks: true, gfm: true })
      const parsed = markedModule.marked.parse(displayStreamContent)
      if (active) {
        setRenderedStreamContent(typeof parsed === 'string' ? purifyModule.default.sanitize(parsed) : '')
      }
    })
    return () => { active = false }
  }, [displayStreamContent])

  return (
    <div className="message-list" ref={listRef} onScroll={handleScroll}>
      {messages.map(msg => (
        <MessageItem key={msg.id} msg={msg} />
      ))}

      {isGenerating && (
        <div className="stream-message">
          <div className="w-full max-w-[860px]">
          <div className="flex items-center gap-2 mb-1.5">
            <div className="w-[22px] h-[22px] rounded-md bg-success flex items-center justify-center text-[10px] font-bold text-white">E</div>
            <span className="text-[13px] font-semibold text-fg">Explorer</span>
            <span className="text-[11px] text-muted">刚刚</span>
          </div>
          {streamThinking.length > 0 && (
            <div className="message-process">
              <ReasoningRow segments={streamThinking} running />
            </div>
          )}
          {streamContent ? (
            <div className="assistant-response">
              <div
                className="assistant-markdown"
                {...(renderedStreamContent
                  ? { dangerouslySetInnerHTML: { __html: renderedStreamContent } }
                  : { children: <p className="whitespace-pre-wrap">{displayStreamContent}</p> })}
              />
              <span className="streaming-cursor" />
            </div>
          ) : streamSteps.length === 0 && (
            <div className="assistant-response flex gap-1 py-2">
              <span className="w-1.5 h-1.5 bg-dot rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-1.5 h-1.5 bg-dot rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-1.5 h-1.5 bg-dot rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
          )}
          {streamSteps.length > 0 && <WorkChain steps={streamSteps} elapsed={elapsed} showSummary />}
          {streamNarration.length > 0 && (
            <div className="stream-narration">
              {streamNarration.map((line, index) => (
                <div key={index} className="stream-narration__line">{line}</div>
              ))}
            </div>
          )}
          {streamCmdBlocks.map(b => <CmdBlock key={b.id} block={b} />)}
          </div>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}
