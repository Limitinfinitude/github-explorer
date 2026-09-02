import React, { useRef, useEffect, useState } from 'react'
import { Brain, Check, ChevronUp, Send, Settings2, ShieldCheck, Square } from 'lucide-react'
import type { Model } from '../../types'
import { api } from '../../lib/api'
import { ModelManager } from '../settings/ModelManager'
import { useConfirm } from '../common/useConfirm'

export type ApprovalMode = 'confirm' | 'auto' | 'full'

const APPROVAL_MODES: { value: ApprovalMode; label: string; description: string }[] = [
  { value: 'confirm', label: '需要审批', description: '高风险操作等待人工确认' },
  { value: 'auto', label: '自动放行', description: '高风险操作自动放行并记录' },
  { value: 'full', label: '完全访问', description: '放开工作区外文件与全局写入（含判分脚本等受限路径）' },
]

const AUTO_MODE_WARNING =
  '自动放行模式将不再询问，高风险操作（含删除、系统级操作）直接执行并记录。受限路径（判分脚本等）仍受保护。确定切换？'

const FULL_ACCESS_WARNING =
  '完全访问模式将放开工作区外文件访问（包括判分脚本、评测结果等受限路径）与全局工具链写入（setx、npm install -g、go install 等），且跳过边界拦截。仅在你完全信任当前任务且需要这些能力时使用。'

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
  onModelsChanged?: () => Promise<void> | void
}

const THINK_OPTIONS: { value: 'off' | 'high' | 'max'; label: string; title: string }[] = [
  { value: 'off', label: '关闭', title: '不开启思考模式' },
  { value: 'high', label: '高', title: '高强度思考' },
  { value: 'max', label: '最大', title: '最大强度思考（预算最高）' },
]

export function InputArea({ isGenerating, currentModel, models, agentMode, thinkingEffort, onThinkingEffort, onSend, onStop, onSelectModel, onSlashCommand, planMode, onModelsChanged }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const thinkPopupRef = useRef<HTMLDivElement>(null)
  const shieldPopupRef = useRef<HTMLDivElement>(null)
  const [showModelPopup, setShowModelPopup] = useState(false)
  const [showThinkPopup, setShowThinkPopup] = useState(false)
  const [showShieldPopup, setShowShieldPopup] = useState(false)
  const [showModelManager, setShowModelManager] = useState(false)
  const [menuSelected, setMenuSelected] = useState(0)
  const [inputText, setInputText] = useState('')
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>('confirm')
  const [approvalSaving, setApprovalSaving] = useState(false)
  const { confirm, dialog: confirmDialog } = useConfirm()
  const model = models.find(m => m.id === currentModel)
  const thinkLabel = THINK_OPTIONS.find(o => o.value === thinkingEffort)?.label ?? '关闭'

  useEffect(() => {
    // 兼容历史值：已废弃的 open 档归并到 auto（语义最接近）
    api.getApprovalMode().then(mode => setApprovalMode(mode === 'open' ? 'auto' : mode)).catch(() => {})
  }, [])

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
    if (!showModelPopup && !showThinkPopup && !showShieldPopup) return
    function handleClick(e: MouseEvent) {
      if (popupRef.current && !popupRef.current.contains(e.target as Node)
        && thinkPopupRef.current && !thinkPopupRef.current.contains(e.target as Node)
        && shieldPopupRef.current && !shieldPopupRef.current.contains(e.target as Node)) {
        setShowModelPopup(false)
        setShowThinkPopup(false)
        setShowShieldPopup(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [showModelPopup, showThinkPopup, showShieldPopup])

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

  async function selectApprovalMode(mode: ApprovalMode) {
    if (mode === approvalMode || approvalSaving) return
    // 放开档位都先确认：自动放行跳过询问，完全访问还放开受限路径
    if (mode === 'auto' && !(await confirm({ title: '切换到自动放行？', message: AUTO_MODE_WARNING, confirmText: '切换', danger: true }))) return
    if (mode === 'full' && !(await confirm({ title: '切换到完全访问？', message: FULL_ACCESS_WARNING, confirmText: '切换', danger: true }))) return
    setApprovalSaving(true)
    try {
      await api.setApprovalMode(mode)
      setApprovalMode(mode)
    } catch {
      // 保存失败保持原档位；下次打开仍显示真实值
    } finally {
      setApprovalSaving(false)
    }
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

            {/* 权限档位 */}
            <div className="relative" ref={shieldPopupRef}>
              <button
                onClick={() => { setShowShieldPopup(p => !p); setShowModelPopup(false); setShowThinkPopup(false) }}
                className={`think-selector approval-selector ${approvalMode === 'confirm' ? 'is-guarded' : 'is-open'}`}
                title="权限档位：高风险操作的执行策略"
              >
                <ShieldCheck size={13} />
                <span>{APPROVAL_MODES.find(m => m.value === approvalMode)?.label}</span>
                <ChevronUp size={12} />
              </button>
              {showShieldPopup && (
                <div className="absolute bottom-full left-0 mb-2 bg-surface-2 border border-theme rounded-xl p-1.5 min-w-[280px] shadow-xl z-50">
                  <div className="px-3 pt-2 pb-1.5 text-[10px] font-semibold text-muted-2 uppercase tracking-wider">权限档位</div>
                  {APPROVAL_MODES.map(mode => (
                    <button
                      key={mode.value}
                      onClick={() => { void selectApprovalMode(mode.value); if (mode.value !== 'full' || approvalMode === 'full') setShowShieldPopup(false) }}
                      disabled={approvalSaving}
                      title={mode.description}
                      className={`flex items-start gap-2.5 w-full px-3 py-2 rounded-lg text-left transition-colors ${mode.value === approvalMode ? 'bg-accent/10 text-accent' : 'text-fg-2 hover:bg-[var(--hover)]'}`}
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 text-[12px]">
                          <span>{mode.label}</span>
                          {mode.value === approvalMode && <Check size={13} />}
                        </div>
                        <div className="text-[10.5px] text-muted mt-0.5 leading-snug">{mode.description}</div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="composer__controls composer__controls--right">
            {/* 模型选择 */}
            <div className="relative" ref={popupRef}>
              <button onClick={() => { setShowModelPopup(p => !p); setShowShieldPopup(false); setShowThinkPopup(false) }}
                className="model-selector">
                <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: model?.color ?? '#666' }} />
                {model?.name ?? currentModel}
                <ChevronUp size={12} />
              </button>
              {showModelPopup && (
                <div className="absolute bottom-full right-0 mb-2 bg-surface-2 border border-theme rounded-xl p-1.5 min-w-[200px] shadow-xl z-50">
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
                  <div className="my-1 mx-2 h-px bg-[var(--border)]" />
                  <button onClick={() => { setShowModelPopup(false); setShowModelManager(true) }}
                    className="flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-[12px] text-left text-fg-2 hover:bg-[var(--hover)] transition-colors">
                    <Settings2 size={13} className="opacity-70" />
                    <span className="flex-1">管理模型…</span>
                  </button>
                </div>
              )}
            </div>
            {/* 思考强度选择 */}
            <div className="relative" ref={thinkPopupRef}>
              <button onClick={() => { setShowThinkPopup(p => !p); setShowShieldPopup(false); setShowModelPopup(false) }}
                className={`think-selector ${thinkingEffort !== 'off' ? 'is-active' : ''}`}
                title="思考强度（仅本次任务生效）">
                <Brain size={13} />
                <span>{thinkLabel}</span>
                <ChevronUp size={12} />
              </button>
              {showThinkPopup && (
                <div className="absolute bottom-full right-0 mb-2 bg-surface-2 border border-theme rounded-xl p-1.5 min-w-[140px] shadow-xl z-50">
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
      <ModelManager
        open={showModelManager}
        models={models}
        currentModel={currentModel}
        onClose={() => setShowModelManager(false)}
        onModelsChanged={onModelsChanged ?? (async () => {})}
      />
      {confirmDialog}
    </div>
  )
}
