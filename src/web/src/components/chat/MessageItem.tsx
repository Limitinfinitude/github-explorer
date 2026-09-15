import React, { useEffect, useRef, useState } from 'react'
import type { Message } from '../../types'
import WorkProcess from './WorkProcess'
import { ReasoningRow } from './ReasoningRow'
import { AgentStatusPanel } from './AgentStatusPanel'
import { displayResponseContent } from '../../lib/responseDisplay'
import { preprocessMarkdown } from '../../lib/markdownFix'
import { relativeMessageTime } from '../../lib/time'
import { speak, speechSupported, stopSpeaking, toSpeakableText } from '../../lib/speech'
import { Check, Copy, Pencil, RefreshCw, Trash2, Volume2, VolumeX } from 'lucide-react'

export interface MessageActions {
  /** 编辑用户消息并重新发送（截断其后内容） */
  onEditResend: (messageId: string, content: string) => void
  /** 重新生成该条 AI 回复（截断到其之前的用户消息重发） */
  onRegenerate: (messageId: string) => void
  /** 删除单条消息 */
  onDelete: (messageId: string) => void
  /** 生成中时禁用编辑/重生成（避免与流冲突） */
  busy?: boolean
}

export function MessageItem({ msg, actions }: { msg: Message; actions?: MessageActions }) {
  const contentRef = useRef<HTMLDivElement>(null)
  const [renderedContent, setRenderedContent] = useState('')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(msg.content)
  const [copied, setCopied] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const editRef = useRef<HTMLTextAreaElement>(null)
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

  // 组件卸载/切换会话时停止朗读，避免串台
  useEffect(() => () => { stopSpeaking() }, [])

  useEffect(() => {
    if (editing) {
      const el = editRef.current
      if (el) {
        el.focus()
        el.selectionStart = el.selectionEnd = el.value.length
        el.style.height = 'auto'
        el.style.height = `${Math.min(el.scrollHeight, 320)}px`
      }
    }
  }, [editing])

  async function copyContent() {
    try {
      await navigator.clipboard.writeText(msg.content)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = msg.content
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }

  function toggleSpeak() {
    if (speaking) {
      stopSpeaking()
      setSpeaking(false)
      return
    }
    const started = speak(toSpeakableText(displayContent), () => setSpeaking(false))
    setSpeaking(started)
  }

  function startEdit() {
    setDraft(msg.content)
    setEditing(true)
  }

  function submitEdit() {
    const next = draft.trim()
    if (!next || !actions) { setEditing(false); return }
    setEditing(false)
    if (next !== msg.content) actions.onEditResend(msg.id, next)
  }

  const showActions = Boolean(actions)

  return (
    <div className={`msg-enter mb-5 px-7 group ${isUser ? 'flex flex-col items-end' : ''}`}>
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
        {showActions && !editing && (
          <div className={`msg-actions ${isUser ? 'msg-actions--user' : ''}`}>
            {isUser ? (
              <button type="button" title="编辑并重新发送" aria-label="编辑并重新发送"
                disabled={actions?.busy} onClick={startEdit}>
                <Pencil size={12} />
              </button>
            ) : (
              <>
                <button type="button" title={copied ? '已复制' : '复制'} aria-label="复制"
                  className={copied ? 'is-ok' : ''} onClick={() => { void copyContent() }}>
                  {copied ? <Check size={12} /> : <Copy size={12} />}
                </button>
                {speechSupported() && (
                  <button type="button" title={speaking ? '停止朗读' : '朗读'} aria-label="朗读"
                    className={speaking ? 'is-active' : ''} onClick={toggleSpeak}>
                    {speaking ? <VolumeX size={12} /> : <Volume2 size={12} />}
                  </button>
                )}
                <button type="button" title="重新生成" aria-label="重新生成"
                  disabled={actions?.busy} onClick={() => actions?.onRegenerate(msg.id)}>
                  <RefreshCw size={12} />
                </button>
              </>
            )}
            <button
              type="button"
              title={isUser ? '删除这轮对话（提问与回复一起删除）' : '删除这轮对话（连同用户提问一起删除）'}
              aria-label={isUser ? '删除这轮对话' : '删除这轮对话（连同用户提问）'}
              className="is-danger"
              onClick={() => actions?.onDelete(msg.id)}
            >
              <Trash2 size={12} />
            </button>
          </div>
        )}
      </div>

      {isUser ? (
        editing ? (
          <div className="msg-edit">
            <textarea
              ref={editRef}
              className="msg-edit__input"
              value={draft}
              onChange={e => {
                setDraft(e.target.value)
                const el = e.currentTarget
                el.style.height = 'auto'
                el.style.height = `${Math.min(el.scrollHeight, 320)}px`
              }}
              onKeyDown={e => {
                if (e.key === 'Escape') { e.preventDefault(); setEditing(false) }
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  submitEdit()
                }
              }}
            />
            <div className="msg-edit__bar">
              <span className="msg-edit__hint">Enter 重新发送 · Shift+Enter 换行 · Esc 取消</span>
              <button type="button" className="msg-edit__cancel" onClick={() => setEditing(false)}>取消</button>
              <button type="button" className="msg-edit__send" disabled={!draft.trim()} onClick={submitEdit}>重新发送</button>
            </div>
          </div>
        ) : (
          <div className="max-w-[75%] bg-accent/10 border border-accent/20 rounded-[8px_8px_3px_8px] px-3.5 py-2.5 text-sm leading-6">
            <p className="whitespace-pre-wrap break-words text-fg">{msg.content}</p>
          </div>
        )
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
