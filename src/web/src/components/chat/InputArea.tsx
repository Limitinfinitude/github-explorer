import React, { useRef, useEffect, useState } from 'react'
import { Brain, Check, ChevronUp, Send, Square } from 'lucide-react'
import type { Model } from '../../types'

interface Props {
  isGenerating: boolean
  onSlashCommand?: (cmd: string, arg: string) => void
  planMode?: boolean
  currentModel: string
  models: Model[]
  agentMode: boolean
  thinkingEffort: 'off' | 'high' | 'max'
  onThinkingEffort: (value: 'off' | 'high' | 'max') => void
  onSend: (msg: string, planMode?: boolean) => void
  onStop: () => void
  onSelectModel: (id: string) => void
}

const THINK_OPTIONS: { value: 'off' | 'high' | 'max'; label: string; title: string }[] = [
  { value: 'off', label: '关闭', title: '不开启思考模式' },
  { value: 'high', label: '高', title: '高强度思考' },
  { value: 'max', label: '最大', title: '最大强度思考（预算最高）' },
]

export function InputArea({ isGenerating, currentModel, models, agentMode, thinkingEffort, onThinkingEffort, onSend, onStop, onSelectModel, onSlashCommand, planMode }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const thinkPopupRef = useRef<HTMLDivElement>(null)
  const [showModelPopup, setShowModelPopup] = useState(false)
  const [showThinkPopup, setShowThinkPopup] = useState(false)
  const [menuSelected, setMenuSelected] = useState(0)
  const [inputText, setInputText] = useState('')
  const model = models.find(m => m.id === currentModel)
  const thinkLabel = THINK_OPTIONS.find(o => o.value === thinkingEffort)?.label ?? '关闭'

  const COMMANDS: { cmd: string; desc: string }[] = [
    { cmd: 'plan', desc: '切换到计划模式，可附带任务描述直接以计划模式执行' },
    { cmd: 'compact', desc: '压缩当前对话上下文（下一轮生效）' },
    { cmd: 'cost', desc: '查看近 7 天模型用量' },
    { cmd: 'init', desc: '为当前工作区生成 AGENTS.md 项目说明' },
    { cmd: 'help', desc: '显示可用命令' },
  ]

  // "/" 前缀时弹出命令面板，输入内容过滤搜索
  const menuQuery = inputText.trim()
  const menuOpen = menuQuery.startsWith('/') && !isGenerating
  const menuWord = menuQuery.slice(1).split(/\s+/)[0]?.toLowerCase() ?? ''
  const menuArg = menuQuery.slice(1).split(/\s+/).slice(1).join(' ')
  const filtered = menuOpen
    ? COMMANDS.filter(c => !menuWord || c.cmd.startsWith(menuWord) || c.desc.includes(menuWord))
    : []
  const selected = filtered.length ? filtered[Math.min(menuSelected, filtered.length - 1)] : null

  useEffect(() => {
    if (!showModelPopup && !showThinkPopup) return
    function handleClick(e: MouseEvent) {
      if (popupRef.current && !popupRef.current.contains(e.target as Node)
        && thinkPopupRef.current && !thinkPopupRef.current.contains(e.target as Node)) {
        setShowModelPopup(false)
        setShowThinkPopup(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [showModelPopup, showThinkPopup])

  function autoResize() {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }

  const SLASH_COMMANDS = new Set(COMMANDS.map(c => c.cmd))

  function executeCommand(cmd: string, arg: string) {
    const el = textareaRef.current
    if (el) { el.value = ''; el.style.height = 'auto' }
    setInputText('')
    onSlashCommand?.(cmd, arg)
  }

  function submit() {
    const el = textareaRef.current
    if (!el) return
    const msg = inputText.trim()
    if (!msg || isGenerating) return
    setInputText('')
    el.style.height = 'auto'
    if (msg.startsWith('/') && !msg.startsWith('/ ')) {
      const [cmd, ...rest] = msg.slice(1).split(/\s+/)
      if (SLASH_COMMANDS.has(cmd)) {
        onSlashCommand?.(cmd, rest.join(' '))
        return
      }
    }
    onSend(msg, planMode)
  }

  return (
    <div className="composer-shell">
      {menuOpen && filtered.length > 0 && (
        <div className="cmd-menu">
          <div className="cmd-menu__heading">命令</div>
          {filtered.map((c, i) => (
            <button
              key={c.cmd}
              type="button"
              className={`cmd-menu__item ${i === Math.min(menuSelected, filtered.length - 1) ? 'is-selected' : ''}`}
              onMouseDown={e => { e.preventDefault(); executeCommand(c.cmd, menuArg) }}
              onMouseEnter={() => setMenuSelected(i)}
            >
              <strong>/{c.cmd}</strong>
              <span>{c.desc}</span>
            </button>
          ))}
          <div className="cmd-menu__foot">输入内容以过滤命令，↑↓ 选择，Enter 执行</div>
        </div>
      )}
      <div className="composer">
        <textarea
          ref={textareaRef}
          placeholder="输入消息，输入 / 唤起命令…"
          rows={1}
          className="composer__textarea"
          value={inputText}
          onChange={e => { setInputText(e.target.value); autoResize(); setMenuSelected(0) }}
          onKeyDown={e => {
            if (menuOpen && filtered.length) {
              if (e.key === 'ArrowDown') { e.preventDefault(); setMenuSelected(s => (s + 1) % filtered.length); return }
              if (e.key === 'ArrowUp') { e.preventDefault(); setMenuSelected(s => (s - 1 + filtered.length) % filtered.length); return }
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                if (selected) executeCommand(selected.cmd, menuArg)
                return
              }
            }
            if (e.key === 'Escape' && menuOpen) { e.preventDefault(); setInputText(''); return }
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
          }}
        />
        <div className="composer__toolbar">
          <div className="composer__controls">

            {/* 模型选择 */}
            <div className="relative" ref={popupRef}>
              <button onClick={() => setShowModelPopup(p => !p)}
                className="model-selector">
                <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: model?.color ?? '#666' }} />
                {model?.name ?? currentModel}
                <ChevronUp size={12} />
              </button>
              {showModelPopup && (
                <div className="absolute bottom-full left-0 mb-2 bg-surface-2 border border-theme rounded-xl p-1.5 min-w-[200px] shadow-xl z-50">
                  {models.map(m => (
                    <button key={m.id} onClick={() => { onSelectModel(m.id); setShowModelPopup(false) }}
                      className={`flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-[12px] text-left transition-colors ${m.id === currentModel ? 'bg-accent/10 text-accent' : 'text-fg-2 hover:bg-[var(--hover)]'}`}>
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
            {/* 思考强度选择 */}
            <div className="relative" ref={thinkPopupRef}>
              <button onClick={() => setShowThinkPopup(p => !p)}
                className={`think-selector ${thinkingEffort !== 'off' ? 'is-active' : ''}`}
                title="思考强度（仅本次任务生效）">
                <Brain size={13} />
                <span>{thinkLabel}</span>
                <ChevronUp size={12} />
              </button>
              {showThinkPopup && (
                <div className="absolute bottom-full left-0 mb-2 bg-surface-2 border border-theme rounded-xl p-1.5 min-w-[140px] shadow-xl z-50">
                  {THINK_OPTIONS.map(option => (
                    <button key={option.value} onClick={() => { onThinkingEffort(option.value); setShowThinkPopup(false) }}
                      title={option.title}
                      className={`flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-[12px] text-left transition-colors ${option.value === thinkingEffort ? 'bg-accent/10 text-accent' : 'text-fg-2 hover:bg-[var(--hover)]'}`}>
                      <span className="flex-1">{option.label}</span>
                      {option.value === thinkingEffort && (
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
