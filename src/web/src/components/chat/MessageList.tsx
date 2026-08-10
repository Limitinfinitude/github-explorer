import React, { useEffect, useRef, useState } from 'react'
import type { Message, Step, CmdBlockData } from '../../types'
import { MessageItem } from './MessageItem'
import { WorkChain } from './WorkChain'
import { CmdBlock } from './CmdBlock'
import { displayResponseContent } from '../../lib/responseDisplay'

interface Props {
  messages: Message[]
  isGenerating: boolean
  streamSteps: Step[]
  streamCmdBlocks: CmdBlockData[]
  streamContent: string
  startTime: number
}

export function MessageList({ messages, isGenerating, streamSteps, streamCmdBlocks, streamContent, startTime }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const [renderedStreamContent, setRenderedStreamContent] = useState('')
  const elapsed = Math.round((Date.now() - startTime) / 1000)
  const displayStreamContent = displayResponseContent(streamContent, false)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, streamContent, streamCmdBlocks.length])

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
    <div className="message-list">
      {messages.map(msg => (
        <MessageItem key={msg.id} msg={msg} />
      ))}

      {isGenerating && (
        <div className="stream-message">
          <div className="flex items-center gap-2 mb-1.5">
            <div className="w-[22px] h-[22px] rounded-md bg-success flex items-center justify-center text-[10px] font-bold text-white">E</div>
            <span className="text-[13px] font-semibold text-zinc-200">Explorer</span>
            <span className="text-[11px] text-zinc-500">刚刚</span>
          </div>
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
              <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
          )}
          {streamSteps.length > 0 && <WorkChain steps={streamSteps} elapsed={elapsed} />}
          {streamCmdBlocks.map(b => <CmdBlock key={b.id} block={b} />)}
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}
