import React, { useRef, useEffect, useState } from 'react'
import { Bot, Check, ChevronUp, Send, Square } from 'lucide-react'
import type { Model } from '../../types'

interface Props {
  isGenerating: boolean
  currentModel: string
  models: Model[]
  agentMode: boolean
  onSend: (msg: string) => void
  onStop: () => void
  onSelectModel: (id: string) => void
}

export function InputArea({ isGenerating, currentModel, models, agentMode, onSend, onStop, onSelectModel }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const [showModelPopup, setShowModelPopup] = useState(false)
  const model = models.find(m => m.id === currentModel)

  useEffect(() => {
    if (!showModelPopup) return
    function handleClick(e: MouseEvent) {
      if (popupRef.current && !popupRef.current.contains(e.target as Node)) {
        setShowModelPopup(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [showModelPopup])

  function autoResize() {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }

  function submit() {
    const el = textareaRef.current
    if (!el) return
    const msg = el.value.trim()
    if (!msg || isGenerating) return
    el.value = ''
    el.style.height = 'auto'
    onSend(msg)
  }

  return (
    <div className="composer-shell">
      <div className="composer">
        <textarea
          ref={textareaRef}
          placeholder="输入消息..."
          rows={1}
          className="composer__textarea"
          onInput={autoResize}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }}
        />
        <div className="composer__toolbar">
          <div className="composer__controls">
            <div
              className={`agent-mode-toggle ${agentMode ? 'is-active' : ''}`}
              title="本地 Agent 模式"
            >
              <Bot size={14} />
              <span>Agent</span>
            </div>
            {/* 模型选择 */}
            <div className="relative" ref={popupRef}>
              <button onClick={() => setShowModelPopup(p => !p)}
                className="model-selector">
                <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: model?.color ?? '#666' }} />
                {model?.name ?? currentModel}
                <ChevronUp size={12} />
              </button>
              {showModelPopup && (
                <div className="absolute bottom-full left-0 mb-2 bg-zinc-800 border border-zinc-700 rounded-xl p-1.5 min-w-[200px] shadow-xl z-50">
                  {models.map(m => (
                    <button key={m.id} onClick={() => { onSelectModel(m.id); setShowModelPopup(false) }}
                      className={`flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-[12px] text-left transition-colors ${m.id === currentModel ? 'bg-accent/10 text-accent' : 'text-zinc-300 hover:bg-zinc-700'}`}>
                      <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: m.color }} />
                      <span className="flex-1">{m.name}</span>
                      {m.id === currentModel && (
                        <Check size={13} />
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* 发送/停止按钮 */}
          {isGenerating ? (
            <button onClick={onStop}
              title="停止生成"
              aria-label="停止生成"
              className="composer__submit is-stop">
              <Square size={11} fill="currentColor" />
            </button>
          ) : (
            <button onClick={submit}
              title="发送任务"
              aria-label="发送任务"
              className="composer__submit">
              <Send size={14} />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
