import React, { useEffect, useState } from 'react'
import { AlertCircle, Check, CircleCheck, Eye, EyeOff, FolderGit2, Gauge, KeyRound, ListFilter, LoaderCircle, Link2, Pencil, PlugZap, Plus, Save, X } from 'lucide-react'
import { api } from '../../lib/api'
import { modelProbeReadiness, probeStatusText } from '../../lib/modelProbe'
import type { ModelDiscoveryResult, ProbeResult } from '../../lib/modelProbe'
import type { CustomModelInput, Model } from '../../types'

interface Props {
  models: Model[]
  currentModel: string
  onSelectModel: (id: string) => void
  onModelCreated: (id: string) => Promise<void>
}

const EMPTY_FORM: CustomModelInput = {
  name: '',
  model: '',
  protocol: 'anthropic',
  base_url: '',
  api_key: '',
}

export function SettingsView({ models, currentModel, onSelectModel, onModelCreated }: Props) {
  const [adding, setAdding] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [showKey, setShowKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [form, setForm] = useState<CustomModelInput>(EMPTY_FORM)
  const [probeBusy, setProbeBusy] = useState<'latency' | 'discover' | 'connection' | null>(null)
  const [latencyResult, setLatencyResult] = useState<ProbeResult | null>(null)
  const [discoveryResult, setDiscoveryResult] = useState<ModelDiscoveryResult | null>(null)
  const [connectionResult, setConnectionResult] = useState<ProbeResult | null>(null)
  const [defaultWorkspace, setDefaultWorkspace] = useState('')
  const [workspaceDraft, setWorkspaceDraft] = useState('')
  const [workspaceSource, setWorkspaceSource] = useState<'configured' | 'fallback'>('fallback')
  const [workspaceSaving, setWorkspaceSaving] = useState(false)
  const [workspaceStatus, setWorkspaceStatus] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    api.getDefaultWorkspace().then(result => {
      setDefaultWorkspace(result.path)
      setWorkspaceDraft(result.path)
      setWorkspaceSource(result.source)
    }).catch(reason => {
      setWorkspaceStatus({ ok: false, text: reason instanceof Error ? reason.message : '读取失败' })
    })
  }, [])

  const saveDefaultWorkspace = async () => {
    if (!workspaceDraft.trim()) return
    setWorkspaceSaving(true)
    setWorkspaceStatus(null)
    try {
      const result = await api.setDefaultWorkspace(workspaceDraft.trim())
      setDefaultWorkspace(result.path)
      setWorkspaceDraft(result.path)
      setWorkspaceSource(result.source)
      setWorkspaceStatus({ ok: true, text: '默认工作目录已保存' })
    } catch (reason) {
      setWorkspaceStatus({ ok: false, text: reason instanceof Error ? reason.message : '保存失败' })
    } finally {
      setWorkspaceSaving(false)
    }
  }

  const update = <K extends keyof CustomModelInput>(key: K, value: CustomModelInput[K]) => {
    setForm(previous => ({ ...previous, [key]: value }))
    setLatencyResult(null)
    setDiscoveryResult(null)
    setConnectionResult(null)
  }

  const closeEditor = () => {
    setAdding(false)
    setEditingId(null)
    setShowKey(false)
    setError('')
    setForm(EMPTY_FORM)
    setProbeBusy(null)
    setLatencyResult(null)
    setDiscoveryResult(null)
    setConnectionResult(null)
  }

  const readiness = modelProbeReadiness(form)

  const editModel = (model: Model) => {
    setEditingId(model.id)
    setAdding(true)
    setShowKey(false)
    setError('')
    setForm({
      name: model.name,
      model: model.model ?? model.id,
      protocol: model.protocol ?? 'anthropic',
      base_url: model.base_url ?? '',
      api_key: '',
    })
    setLatencyResult(null)
    setDiscoveryResult(null)
    setConnectionResult(null)
  }

  const runProbe = async (
    kind: 'latency' | 'discover' | 'connection',
    action: () => Promise<void>,
  ) => {
    setProbeBusy(kind)
    try {
      await action()
    } catch (reason) {
      const result = { ok: false, error: reason instanceof Error ? reason.message : '请求失败' }
      if (kind === 'latency') setLatencyResult(result)
      if (kind === 'discover') setDiscoveryResult({ ...result, models: [] })
      if (kind === 'connection') setConnectionResult(result)
    } finally {
      setProbeBusy(null)
    }
  }

  const measureUrl = () => runProbe('latency', async () => {
    setLatencyResult(await api.measureModelUrl(form.base_url))
  })

  const getModels = () => runProbe('discover', async () => {
    setDiscoveryResult(await api.discoverModels({ ...form, model_config_id: editingId ?? undefined }))
  })

  const testConnection = () => runProbe('connection', async () => {
    setConnectionResult(await api.testModelConnection({ ...form, model_config_id: editingId ?? undefined }))
  })

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!form.name.trim() || !form.model.trim()) return
    setSaving(true)
    setError('')
    try {
      const result = editingId ? await api.saveModel(editingId, form) : await api.createModel(form)
      await onModelCreated(result.model.id)
      closeEditor()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '模型保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="settings-view">
      <header className="settings-header">
        <div>
          <span>MODEL PROVIDERS</span>
          <h1>模型设置</h1>
          <p>配置 Agent 使用的模型与服务端点</p>
        </div>
        {!adding && (
          <button type="button" className="settings-add" onClick={() => setAdding(true)}>
            <Plus size={14} />添加模型
          </button>
        )}
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
          <button type="button" onClick={() => void saveDefaultWorkspace()} disabled={workspaceSaving || !workspaceDraft.trim() || workspaceDraft.trim() === defaultWorkspace}>
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
      </section>

      {adding && (
        <form className="model-editor" onSubmit={submit}>
          <div className="model-editor__header">
            <div>
              <strong>{editingId ? '修改自定义模型' : '添加自定义模型'}</strong>
              <span>{editingId ? 'API Key 留空将保留原配置；保存后立即同步到对话模型列表' : '保存后将立即同步到对话模型列表'}</span>
            </div>
            <button type="button" className="model-editor__close" onClick={closeEditor} title="关闭" aria-label="关闭">
              <X size={15} />
            </button>
          </div>

          <div className="model-field model-field--wide">
            <label>协议</label>
            <div className="protocol-selector">
              <button type="button" className={form.protocol === 'anthropic' ? 'is-active' : ''} onClick={() => update('protocol', 'anthropic')}>
                Anthropic Messages
              </button>
              <button type="button" className={form.protocol === 'openai' ? 'is-active' : ''} onClick={() => update('protocol', 'openai')}>
                OpenAI Compatible
              </button>
            </div>
          </div>

          <div className="model-editor__grid">
            <div className="model-field">
              <label htmlFor="custom-model-name">显示名称</label>
              <input id="custom-model-name" value={form.name} onChange={event => update('name', event.target.value)} placeholder="例如：本地 Qwen" autoFocus />
            </div>
            <div className="model-field">
              <label htmlFor="custom-model-id">模型 ID</label>
              <div className="model-input-with-icon model-input-plain">
                <input id="custom-model-id" value={form.model} onChange={event => update('model', event.target.value)} placeholder="例如：qwen3-coder" />
                <button type="button" className="model-field-action" onClick={getModels} disabled={!readiness.canDiscover || probeBusy !== null} title="获取模型列表">
                  {probeBusy === 'discover' ? <LoaderCircle className="is-spinning" size={13} /> : <ListFilter size={13} />}
                  <span>获取</span>
                </button>
              </div>
              {discoveryResult && (
                <ProbeStatus result={discoveryResult} successLabel={`已获取 ${discoveryResult.models.length} 个模型`} />
              )}
              {discoveryResult?.ok && discoveryResult.models.length > 0 && (
                <div className="model-options" aria-label="可用模型">
                  {discoveryResult.models.map(modelId => (
                    <button type="button" key={modelId} className={modelId === form.model ? 'is-selected' : ''} onClick={() => update('model', modelId)}>
                      <code>{modelId}</code>{modelId === form.model && <Check size={12} />}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="model-field model-field--wide">
              <label htmlFor="custom-model-url">Base URL</label>
              <div className="model-input-with-icon">
                <Link2 size={14} />
                <input
                  id="custom-model-url"
                  value={form.base_url}
                  onChange={event => update('base_url', event.target.value)}
                  placeholder={form.protocol === 'openai' ? 'https://api.openai.com/v1' : 'https://api.anthropic.com'}
                />
                <button type="button" className="model-field-action" onClick={measureUrl} disabled={!readiness.canMeasure || probeBusy !== null} title="测试本机到该 URL 的延迟">
                  {probeBusy === 'latency' ? <LoaderCircle className="is-spinning" size={13} /> : <Gauge size={13} />}
                  <span>测速</span>
                </button>
              </div>
              {latencyResult && <ProbeStatus result={latencyResult} successLabel="URL 可达" />}
            </div>
            <div className="model-field model-field--wide">
              <label htmlFor="custom-model-key">API Key</label>
              <div className="model-input-with-icon">
                <KeyRound size={14} />
                <input
                  id="custom-model-key"
                  type={showKey ? 'text' : 'password'}
                  value={form.api_key}
                  onChange={event => update('api_key', event.target.value)}
                  placeholder={editingId ? '留空以保留当前密钥' : '可留空，用于无需鉴权的本地服务'}
                  autoComplete="new-password"
                />
                <button type="button" className="model-field-icon" onClick={() => setShowKey(value => !value)} title={showKey ? '隐藏密钥' : '显示密钥'} aria-label={showKey ? '隐藏密钥' : '显示密钥'}>
                  {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
                <button type="button" className="model-field-action" onClick={testConnection} disabled={!readiness.canTest || probeBusy !== null} title="使用当前模型发起最小请求">
                  {probeBusy === 'connection' ? <LoaderCircle className="is-spinning" size={13} /> : <PlugZap size={13} />}
                  <span>测试</span>
                </button>
              </div>
              {connectionResult && <ProbeStatus result={connectionResult} successLabel="连接成功" />}
            </div>
          </div>

          {error && <div className="model-editor__error">{error}</div>}
          <div className="model-editor__actions">
            <button type="button" onClick={closeEditor}>取消</button>
            <button type="submit" className="is-primary" disabled={saving || !form.name.trim() || !form.model.trim()}>
              <Save size={13} />{saving ? '保存中...' : editingId ? '保存修改' : '保存并使用'}
            </button>
          </div>
        </form>
      )}

      <div className="settings-section-heading">
        <h2>可用模型</h2>
        <span>{models.length} 个</span>
      </div>
      <div className="model-list">
        {models.map(model => (
          <div key={model.id} className={`model-row ${model.id === currentModel ? 'is-active' : ''}`}>
            <button type="button" className="model-row__select" onClick={() => onSelectModel(model.id)} aria-label={`使用 ${model.name}`}>
            <span className="model-row__icon" style={{ background: model.color }}>{model.icon}</span>
            <span className="model-row__body">
              <span className="model-row__title">
                <strong>{model.name}</strong>
                {model.id === currentModel && <em>使用中</em>}
              </span>
              <span className="model-row__meta">
                <code>{model.model ?? model.id}</code>
                {model.protocol && <span>{model.protocol === 'openai' ? 'OpenAI Compatible' : 'Anthropic'}</span>}
                {model.base_url && <span className="model-row__url" title={model.base_url}>{model.base_url}</span>}
              </span>
            </span>
            {model.id === currentModel && <Check className="model-row__check" size={16} />}
            </button>
            {model.tags?.includes('Environment') !== true && (
              <button type="button" className="model-row__edit" onClick={() => editModel(model)} title={`修改 ${model.name}`} aria-label={`修改 ${model.name}`}>
                <Pencil size={14} />
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function ProbeStatus({ result, successLabel }: { result: ProbeResult; successLabel: string }) {
  return (
    <div className={`model-probe-status ${result.ok ? 'is-success' : 'is-error'}`}>
      {result.ok ? <CircleCheck size={12} /> : <AlertCircle size={12} />}
      <span>{probeStatusText(successLabel, result)}</span>
    </div>
  )
}
