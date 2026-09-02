import React, { useEffect, useState } from 'react'
import { AlertCircle, CircleCheck, FolderGit2, FolderOpen, LoaderCircle, PlugZap, RefreshCw, Save, Timer, Zap } from 'lucide-react'
import { api } from '../../lib/api'
import { PathBrowser } from './PathBrowser'
import type { HookConfig } from '../../types'

interface Props {
  onModelsChanged?: (selectModelId?: string) => Promise<void> | void
}

export function SettingsView({ onModelsChanged }: Props) {
  // 默认工作目录
  const [workspaceDraft, setWorkspaceDraft] = useState('')
  const [workspaceSource, setWorkspaceSource] = useState<'configured' | 'fallback'>('fallback')
  const [workspaceSaving, setWorkspaceSaving] = useState(false)
  const [workspaceStatus, setWorkspaceStatus] = useState<{ ok: boolean; text: string } | null>(null)
  const [pathBrowserOpen, setPathBrowserOpen] = useState(false)

  // 运行偏好
  const [retentionDays, setRetentionDays] = useState(30)
  const [compactRatio, setCompactRatio] = useState(0.75)
  const [mcpPrewarm, setMcpPrewarm] = useState(true)
  const [prefsSaving, setPrefsSaving] = useState(false)
  const [prefsStatus, setPrefsStatus] = useState<{ ok: boolean; text: string } | null>(null)

  // 扩展机制（钩子 + MCP）
  const [hooks, setHooks] = useState<HookConfig[]>([])
  const [hookEvents, setHookEvents] = useState<string[]>([])
  const [hookDraft, setHookDraft] = useState({ event: 'session_end', command: '', matcher: '' })
  const [hooksSaving, setHooksSaving] = useState(false)
  const [hooksStatus, setHooksStatus] = useState<{ ok: boolean; text: string } | null>(null)
  const [mcpStatus, setMcpStatus] = useState<{ connected: boolean; servers: string[]; tools: Array<{ name: string; server?: string }> } | null>(null)
  const [mcpLoading, setMcpLoading] = useState(false)

  useEffect(() => {
    api.getDefaultWorkspace().then(result => {
      setWorkspaceDraft(result.path)
      setWorkspaceSource(result.source)
    }).catch(reason => {
      setWorkspaceStatus({ ok: false, text: reason instanceof Error ? reason.message : '读取失败' })
    })
    api.getRuntimePrefs().then(prefs => {
      setRetentionDays(prefs.event_retention_days)
      setCompactRatio(prefs.compact_ratio)
      setMcpPrewarm(prefs.mcp_prewarm)
    }).catch(() => {})
    api.getHooks().then(result => {
      setHookEvents(result.events)
      setHooks(result.hooks)
    }).catch(() => {})
    void refreshMcp()
  }, [])

  const refreshMcp = async () => {
    setMcpLoading(true)
    try {
      setMcpStatus(await api.getMcpStatus())
    } catch {
      setMcpStatus(null)
    } finally {
      setMcpLoading(false)
    }
  }

  const saveDefaultWorkspace = async () => {
    if (!workspaceDraft.trim()) return
    setWorkspaceSaving(true)
    setWorkspaceStatus(null)
    try {
      const result = await api.setDefaultWorkspace(workspaceDraft.trim())
      setWorkspaceDraft(result.path)
      setWorkspaceSource(result.source)
      setWorkspaceStatus({ ok: true, text: '默认工作目录已保存' })
    } catch (reason) {
      setWorkspaceStatus({ ok: false, text: reason instanceof Error ? reason.message : '保存失败' })
    } finally {
      setWorkspaceSaving(false)
    }
  }

  const savePrefs = async () => {
    setPrefsSaving(true)
    setPrefsStatus(null)
    try {
      await api.setRuntimePrefs({
        event_retention_days: retentionDays,
        compact_ratio: compactRatio,
        mcp_prewarm: mcpPrewarm,
      })
      setPrefsStatus({ ok: true, text: '已保存并即时生效' })
      // 模型列表可能受偏好影响（如 MCP），顺手刷新
      onModelsChanged?.()
    } catch (reason) {
      setPrefsStatus({ ok: false, text: reason instanceof Error ? reason.message : '保存失败' })
    } finally {
      setPrefsSaving(false)
    }
  }

  const saveHooksConfig = async () => {
    setHooksSaving(true)
    setHooksStatus(null)
    try {
      const cleaned = hooks.filter(hook => hook.command.trim())
      await api.saveHooks(cleaned)
      setHooks(cleaned)
      setHooksStatus({ ok: true, text: '已保存' })
    } catch (reason) {
      setHooksStatus({ ok: false, text: reason instanceof Error ? reason.message : '保存失败' })
    } finally {
      setHooksSaving(false)
    }
  }

  const addHook = () => {
    if (!hookDraft.command.trim()) return
    setHooks(previous => [...previous, {
      event: hookDraft.event,
      command: hookDraft.command.trim(),
      matcher: hookDraft.matcher.trim(),
      enabled: true,
      timeout: 20,
    }])
    setHookDraft(draft => ({ ...draft, command: '', matcher: '' }))
  }

  const removeHook = (index: number) => {
    setHooks(previous => previous.filter((_, i) => i !== index))
  }

  const toggleHook = (index: number, enabled: boolean) => {
    setHooks(previous => previous.map((hook, i) => i === index ? { ...hook, enabled } : hook))
  }

  return (
    <div className="settings-view">
      <header className="settings-header">
        <div>
          <span>GENERAL</span>
          <h1>通用设置</h1>
          <p>工作目录、运行偏好与扩展机制——模型与权限在对话输入框中管理</p>
        </div>
      </header>

      <section className="workspace-settings" aria-labelledby="workspace-settings-title">
        <div className="workspace-settings__heading">
          <span className="workspace-settings__icon"><FolderGit2 size={16} /></span>
          <div>
            <h2 id="workspace-settings-title">默认工作目录</h2>
            <p>新会话从这里开始；已有会话继续使用各自绑定的目录。</p>
          </div>
          <span className={`workspace-settings__source ${workspaceSource === 'configured' ? 'is-configured' : ''}`}>
            {workspaceSource === 'configured' ? '已配置' : '系统回退'}
          </span>
        </div>
        <div className="workspace-settings__control">
          <input
            value={workspaceDraft}
            onChange={event => { setWorkspaceDraft(event.target.value); setWorkspaceStatus(null) }}
            onKeyDown={event => { if (event.key === 'Enter') void saveDefaultWorkspace() }}
            placeholder="E:\\projects"
            aria-label="默认工作目录"
          />
          <button type="button" className="workspace-settings__browse" onClick={() => setPathBrowserOpen(true)} title="浏览选择目录">
            <FolderOpen size={14} />
            <span>浏览</span>
          </button>
          <button type="button" onClick={() => void saveDefaultWorkspace()} disabled={workspaceSaving || !workspaceDraft.trim()}>
            {workspaceSaving ? <LoaderCircle className="is-spinning" size={14} /> : <Save size={14} />}
            <span>{workspaceSaving ? '保存中' : '保存'}</span>
          </button>
        </div>
        {workspaceStatus && (
          <div className={`workspace-settings__status ${workspaceStatus.ok ? 'is-success' : 'is-error'}`}>
            {workspaceStatus.ok ? <CircleCheck size={12} /> : <AlertCircle size={12} />}
            <span>{workspaceStatus.text}</span>
          </div>
        )}
        <PathBrowser
          open={pathBrowserOpen}
          initialPath={workspaceDraft}
          onSelect={path => { setWorkspaceDraft(path); setWorkspaceStatus(null) }}
          onClose={() => setPathBrowserOpen(false)}
        />
      </section>

      <section className="workspace-settings" aria-labelledby="runtime-prefs-title">
        <div className="workspace-settings__heading">
          <span className="workspace-settings__icon"><Timer size={16} /></span>
          <div>
            <h2 id="runtime-prefs-title">运行偏好</h2>
            <p>Agent 运行时的行为参数，保存后即时生效。</p>
          </div>
          {prefsStatus && (
            <span className={`workspace-settings__source ${prefsStatus.ok ? 'is-configured' : ''}`}>{prefsStatus.text}</span>
          )}
        </div>
        <div className="prefs-grid">
          <div className="prefs-item">
            <label htmlFor="pref-retention">事件保留天数</label>
            <input
              id="pref-retention"
              type="number"
              min={1}
              max={365}
              value={retentionDays}
              onChange={e => { setRetentionDays(Number(e.target.value) || 30); setPrefsStatus(null) }}
            />
            <small>运行记录的流水事件保留天数，超期自动清理（任务状态与回放不受影响）</small>
          </div>
          <div className="prefs-item">
            <label htmlFor="pref-compact">上下文压缩阈值</label>
            <div className="prefs-item__slider">
              <input
                id="pref-compact"
                type="range"
                min={0.3}
                max={0.95}
                step={0.05}
                value={compactRatio}
                onChange={e => { setCompactRatio(Number(e.target.value)); setPrefsStatus(null) }}
              />
              <b>{Math.round(compactRatio * 100)}%</b>
            </div>
            <small>上下文占用超过窗口该比例时主动压缩历史，调低更省额度、调高保留更多上下文</small>
          </div>
          <div className="prefs-item prefs-item--wide">
            <label className="prefs-toggle">
              <input
                type="checkbox"
                checked={mcpPrewarm}
                onChange={e => { setMcpPrewarm(e.target.checked); setPrefsStatus(null) }}
              />
              <span className="prefs-toggle__body">
                <strong>MCP 预热连接</strong>
                <small>任务启动前预连 MCP 服务器（首次可能拉取 npx 包）。只用本地工具时可关闭以加快启动</small>
              </span>
            </label>
          </div>
        </div>
        <div className="prefs-actions">
          <button type="button" className="prefs-save" onClick={() => void savePrefs()} disabled={prefsSaving}>
            {prefsSaving ? <LoaderCircle className="is-spinning" size={14} /> : <Save size={14} />}
            <span>{prefsSaving ? '保存中' : '保存偏好'}</span>
          </button>
        </div>
      </section>

      <section className="workspace-settings" aria-labelledby="hooks-title">
        <div className="workspace-settings__heading">
          <span className="workspace-settings__icon"><PlugZap size={16} /></span>
          <div>
            <h2 id="hooks-title">扩展机制</h2>
            <p>钩子在 Agent 生命周期事件点执行自定义命令，事件载荷经 stdin 以 JSON 传入；pre_tool 钩子退出码 2 会阻断该工具调用（stderr 作为失败原因）。</p>
          </div>
          {hooksStatus && (
            <span className={`workspace-settings__source ${hooksStatus.ok ? 'is-configured' : ''}`}>{hooksStatus.text}</span>
          )}
        </div>
        <div className="hooks-editor">
          <div className="hooks-editor__add">
            <select value={hookDraft.event} onChange={event => setHookDraft(draft => ({ ...draft, event: event.target.value }))} aria-label="钩子事件">
              {hookEvents.map(event => <option key={event} value={event}>{event}</option>)}
            </select>
            <input
              value={hookDraft.command}
              onChange={event => setHookDraft(draft => ({ ...draft, command: event.target.value }))}
              placeholder="要执行的命令，例如 python notify.py"
              spellCheck={false}
              aria-label="钩子命令"
            />
            <input
              value={hookDraft.matcher}
              onChange={event => setHookDraft(draft => ({ ...draft, matcher: event.target.value }))}
              placeholder="工具名匹配（可选，如 run_command）"
              spellCheck={false}
              aria-label="钩子匹配器"
            />
            <button type="button" onClick={addHook} disabled={!hookDraft.command.trim()}>添加</button>
          </div>
          {hooks.length > 0 && (
            <ul className="hooks-list">
              {hooks.map((hook, index) => (
                <li key={index} className="hooks-list__item">
                  <label className="hooks-list__check">
                    <input type="checkbox" checked={hook.enabled} onChange={e => toggleHook(index, e.target.checked)} />
                  </label>
                  <code className="hooks-list__event">{hook.event}</code>
                  <code className="hooks-list__command">{hook.command}</code>
                  {hook.matcher && <code className="hooks-list__matcher">{hook.matcher}</code>}
                  <button type="button" className="hooks-list__remove" onClick={() => removeHook(index)} aria-label="删除钩子">×</button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="mcp-status">
          <div className="mcp-status__heading">
            <strong>MCP 工具服务器</strong>
            <button type="button" className="text-btn" onClick={() => void refreshMcp()} disabled={mcpLoading}>
              <RefreshCw size={13} className={mcpLoading ? 'is-spinning' : ''} />刷新
            </button>
          </div>
          {mcpStatus ? (
            mcpStatus.connected && mcpStatus.servers.length > 0 ? (
              <ul className="mcp-status__servers">
                {mcpStatus.servers.map(server => (
                  <li key={server}>
                    <span className="mcp-status__server">
                      <i className="mcp-status__pulse" />
                      <code>{server}</code>
                    </span>
                    <span className="mcp-status__count">{mcpStatus.tools.filter(tool => tool.server === server).length} 个工具</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mcp-status__empty">未连接 MCP 服务器。请在项目根目录的 .mcp.json 中配置 mcpServers，重启后自动连接。</p>
            )
          ) : (
            <p className="mcp-status__empty">无法读取 MCP 状态（未安装 mcp 包或连接失败）。</p>
          )}
        </div>
        <div className="section-save-row">
          <button type="button" className="text-btn text-btn--primary" onClick={() => void saveHooksConfig()} disabled={hooksSaving}>
            <Save size={14} />{hooksSaving ? '保存中…' : '保存钩子配置'}
          </button>
        </div>
      </section>
    </div>
  )
}
