import React, { useEffect, useRef, useState } from 'react'
import type { Message } from '../../types'
import WorkProcess from './WorkProcess'
import { ReasoningRow } from './ReasoningRow'
import { AgentStatusPanel } from './AgentStatusPanel'
import { displayResponseContent } from '../../lib/responseDisplay'
import { preprocessMarkdown } from '../../lib/markdownFix'
import { relativeMessageTime } from '../../lib/time'

export function MessageItem({ msg }: { msg: Message }) {
  const contentRef = useRef<HTMLDivElement>(null)
  const [renderedContent, setRenderedContent] = useState('')
  const isUser = msg.role === 'user'
  const displayContent = displayResponseContent(msg.content, Boolean(msg.agentRun))
  const hasWork = Boolean(msg.agentRun || msg.steps?.length || msg.timeline?.length)
  const hasMaterialAgentRun = Boolean(
    msg.agentRun && (
      (msg.agentRun.fileChanges?.length ?? 0) > 0
      || msg.agentRun.verification
      || (msg.agentRun.acceptance?.length ?? 0) > 0
      || (msg.agentRun.processes?.length ?? 0) > 0
    ),
  )

  useEffect(() => {
    if (isUser) return
    let active = true
    Promise.all([import('marked'), import('dompurify')]).then(([markedModule, purifyModule]) => {
      markedModule.marked.setOptions({ breaks: true, gfm: true })
      const parsed = markedModule.marked.parse(preprocessMarkdown(displayContent))
      const html = typeof parsed === 'string' ? purifyModule.default.sanitize(parsed) : ''
      if (active) setRenderedContent(html)
    })
    return () => { active = false }
  }, [displayContent, isUser])

  useEffect(() => {
    if (!contentRef.current || !renderedContent || isUser) return
    import('../../lib/highlighter').then(module => {
      contentRef.current?.querySelectorAll('pre code:not(.hljs)').forEach(element => {
        module.hljs.highlightElement(element as HTMLElement)
      })
    })
  }, [renderedContent, isUser])

  return (
    <div className={`msg-enter mb-5 px-7 ${isUser ? 'flex flex-col items-end' : ''}`}>
      <div className={`w-full max-w-[860px] ${isUser ? 'flex flex-col items-end' : ''}`}>
      <div className={`flex items-center gap-2 mb-1.5 ${isUser ? 'flex-row-reverse' : ''}`}>
        <div
          className={`msg-avatar w-[22px] h-[22px] rounded-md flex items-center justify-center text-[10px] font-bold text-white flex-shrink-0 ${
            isUser ? 'msg-avatar--user' : 'msg-avatar--agent'
          }`}
        >
          {isUser ? 'U' : 'E'}
        </div>
        <span className="text-[13px] font-semibold text-fg">{isUser ? '你' : 'Explorer'}</span>
        <span className="text-[11px] text-muted">{relativeMessageTime(msg.time)}</span>
      </div>

      {isUser ? (
        <div className="max-w-[75%] bg-accent/10 border border-accent/20 rounded-[8px_8px_3px_8px] px-3.5 py-2.5 text-sm leading-6">
          <p className="whitespace-pre-wrap break-words text-fg">{msg.content}</p>
        </div>
      ) : (
        <>
          {hasWork && (
            <WorkProcess
              running={false}
              elapsedSec={msg.workElapsed}
              steps={msg.steps ?? []}
              cmdBlocks={msg.cmdBlocks ?? []}
              narrations={msg.narrations ?? []}
              thinking={msg.thinking ?? []}
              timeline={msg.timeline}
            />
          )}
          <div className="assistant-response">
            <div
              ref={contentRef}
              className="assistant-markdown"
              {...(renderedContent
                ? { dangerouslySetInnerHTML: { __html: renderedContent } }
                : { children: <p className="whitespace-pre-wrap">{displayContent}</p> })}
            />
          </div>
          {msg.agentRun && hasMaterialAgentRun && (
            <AgentStatusPanel
              compact
              status={msg.agentRun.status}
              plan={msg.agentRun.plan}
              repoMap={msg.agentRun.repoMap}
              fileChanges={msg.agentRun.fileChanges}
              verification={msg.agentRun.verification}
              acceptance={msg.agentRun.acceptance}
              processes={msg.agentRun.processes}
            />
          )}
        </>
      )}
      </div>
    </div>
  )
}
