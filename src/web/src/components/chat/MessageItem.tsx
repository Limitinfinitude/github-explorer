import React, { useEffect, useRef, useState } from 'react'
import type { Message } from '../../types'
import { WorkChain } from './WorkChain'
import { CmdBlock } from './CmdBlock'
import { ReasoningRow } from './ReasoningRow'
import { AgentStatusPanel } from './AgentStatusPanel'
import { displayResponseContent } from '../../lib/responseDisplay'
import { relativeMessageTime } from '../../lib/time'

export function MessageItem({ msg }: { msg: Message }) {
  const contentRef = useRef<HTMLDivElement>(null)
  const [renderedContent, setRenderedContent] = useState('')
  const isUser = msg.role === 'user'
  const elapsed = msg.steps?.length ? msg.steps.length * 2 : 0
  const displayContent = displayResponseContent(msg.content, Boolean(msg.agentRun))
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
      const parsed = markedModule.marked.parse(displayContent)
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
    <div className={`mb-5 px-7 ${isUser ? 'flex flex-col items-end' : ''}`}>
      <div className={`flex items-center gap-2 mb-1.5 ${isUser ? 'flex-row-reverse' : ''}`}>
        <div
          className={`w-[22px] h-[22px] rounded-md flex items-center justify-center text-[10px] font-bold text-white flex-shrink-0 ${
            isUser ? 'bg-accent' : 'bg-success'
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
          {Array.isArray(msg.thinking) && msg.thinking.length > 0 && (
            <div className="message-process">
              {msg.thinking.map((block, index) => (
                <ReasoningRow key={index} text={block} running={false} />
              ))}
            </div>
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
          {Array.isArray(msg.narrations) && msg.narrations.length > 0 && (
            <div className="stream-narration">
              {msg.narrations.map((line, index) => (
                <div key={index} className="stream-narration__line">{line}</div>
              ))}
            </div>
          )}
          {msg.steps && msg.steps.length > 0 && <WorkChain steps={msg.steps} elapsed={elapsed} showSummary />}
          {msg.cmdBlocks?.map(b => <CmdBlock key={b.id} block={b} />)}
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
  )
}
