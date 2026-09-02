import React, { useEffect, useMemo, useState } from 'react'
import { Check, Eye, EyeOff, LoaderCircle, Pencil, Plus, Save, Settings2, Trash2, X } from 'lucide-react'
import { api } from '../../lib/api'
import { useConfirm } from '../common/useConfirm'
import type { CustomModelInput, Model } from '../../types'

interface Props {
  open: boolean
  models: Model[]
  currentModel: string
  onClose: () => void
  /** 模型增删改后通知父级刷新列表 */
  onModelsChanged: (selectModelId?: string) => Promise<void> | void
}

const EMPTY_FORM: CustomModelInput = {
  name: '',
  model: '',
  protocol: 'anthropic',
  base_url: '',
  api_key: '',
  thinking_effort: 'off',
  context_window: '',
  max_output_tokens: '',
}

/** 从 base_url 提取提供商显示名：https://api.xiaomimimo.com/anthropic → xiaomimimo */
function providerName(baseUrl: string): string {
  const value = (baseUrl || '').trim()
  if (!value) return '未设置 Base URL'
  try {
    const host = new URL(value).hostname
    // 去掉常见前缀，取主域名
    const parts = host.replace(/^www\./, '').split('.')
    const core = parts.length >= 2 ? parts[parts.length - 2] : parts[0]
    return core || host
  } catch {
    return value.replace(/^https?:\/\//, '').split('/')[0] || value
  }
}

/** 模型管理弹层：左侧提供商（按 base_url 分组），右侧该提供商下的模型列表 + 模型配置。 */
export function ModelManager({ open, models, currentModel, onClose, onModelsChanged }: Props) {
  const [selectedProvider, setSelectedProvider] = useState<string>('')
  const [editing, setEditing] = useState<Model | null>(null)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState<CustomModelInput>(EMPTY_FORM)
  const [showKey, setShowKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [discovered, setDiscovered] = useState<string[] | null>(null)
  const [discovering, setDiscovering] = useState(false)
  // 添加模型时复用已有模型的密钥（服务端已存，前端不可见）：discover/保存时传 model_config_id
  const [reuseKeyId, setReuseKeyId] = useState<string | null>(null)
  const { confirm, dialog: confirmDialog } = useConfirm()

  // 按提供商（base_url）分组
  const providers = useMemo(() => {
    const groups = new Map<string, { key: string; name: string; baseUrl: string; models: Model[] }>()
    for (const model of models) {
      const key = (model.base_url || '').trim() || '(none)'
      if (!groups.has(key)) {
        groups.set(key, { key, name: providerName(model.base_url || ''), baseUrl: model.base_url || '', models: [] })
      }
      groups.get(key)!.models.push(model)
    }
    return [...groups.values()].sort((a, b) => b.models.length - a.models.length)
  }, [models])

  // 打开时选中当前模型所在的提供商
  useEffect(() => {
    if (!open) return
    const current = models.find(m => m.id === currentModel)
    if (current) setSelectedProvider((current.base_url || '').trim() || '(none)')
    else if (providers.length) setSelectedProvider(providers[0].key)
    setEditing(null); setAdding(false); setError(''); setStatus('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const provider = providers.find(p => p.key === selectedProvider)

  function startAdd() {
    setAdding(true); setEditing(null); setError(''); setStatus('')
    setDiscovered(null)
    // 复用当前提供商的 Base URL；密钥复用该提供商下任一已存 key 的模型（reuseKeyId）
    const withKey = provider?.models.find(m => m.has_key)
    setForm({ ...EMPTY_FORM, base_url: provider?.baseUrl ?? '' })
    setReuseKeyId(withKey?.id ?? null)
  }

  function startEdit(model: Model) {
    setAdding(false); setEditing(model); setError(''); setStatus('')
    setShowKey(false)
    setDiscovered(null)
    setReuseKeyId(null)
    setForm({
      name: model.name,
      model: model.model ?? model.id,
      protocol: model.protocol ?? 'anthropic',
      base_url: model.base_url ?? '',
      api_key: '',
      thinking_effort: model.thinking_effort ?? 'off',
      context_window: model.context_window ?? '',
      max_output_tokens: model.max_output_tokens ? String(model.max_output_tokens) : '',
    })
  }

  const update = <K extends keyof CustomModelInput>(key: K, value: CustomModelInput[K]) => {
    setForm(previous => ({ ...previous, [key]: value }))
    setStatus('')
  }

  /** 拉取该 Base URL 下的可用模型列表（复用服务端已存 key 时传 reuseKeyId） */
  async function fetchModels() {
    if (!form.base_url.trim() || discovering) return
    setDiscovering(true); setError(''); setDiscovered(null)
    try {
      const result = await api.discoverModels({
        protocol: form.protocol,
        base_url: form.base_url,
        api_key: form.api_key,
        model_config_id: reuseKeyId ?? editing?.id,
      })
      if (result.ok) setDiscovered(result.models)
      else setError(result.error || '获取模型列表失败')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '获取模型列表失败')
    } finally {
      setDiscovering(false)
    }
  }

  async function submit() {
    if (!form.name.trim() || !form.model.trim()) {
      setError('显示名称和模型 ID 不能为空')
      return
    }
    setSaving(true); setError(''); setStatus('')
    try {
      if (editing) {
        const result = await api.saveModel(editing.id, form)
        await onModelsChanged()
        setStatus(`已保存「${result.model.name}」`)
        setEditing(null)
      } else {
        // 复用已有模型的密钥：交给后端从来源模型取真实 key
        const payload: CustomModelInput & { reuse_key_from?: string } = { ...form }
        if (!payload.api_key.trim() && reuseKeyId) payload.reuse_key_from = reuseKeyId
        const result = await api.createModel(payload)
        await onModelsChanged(result.model.id)
        setStatus(`已添加「${result.model.name}」`)
        setAdding(false)
      }
      setForm(EMPTY_FORM)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  async function remove(model: Model) {
    if (!(await confirm({
      title: `删除模型「${model.name}」？`,
      message: '此操作不可撤销。若该模型正在使用，请先切换到其他模型。',
      confirmText: '删除',
      danger: true,
    }))) return
    setError(''); setStatus('')
    try {
      await api.deleteModel(model.id)
      await onModelsChanged()
      setStatus(`已删除「${model.name}」`)
      if (editing?.id === model.id) setEditing(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '删除失败')
    }
  }

  if (!open) return null

  return (
    <div className="mm-overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="mm" role="dialog" aria-label="管理模型">
        <div className="mm__head">
          <Settings2 size={15} />
          <strong>管理模型</strong>
          <span className="mm__count">{models.length} 个模型 · {providers.length} 个提供商</span>
          <button type="button" className="mm__close" onClick={onClose} aria-label="关闭"><X size={14} /></button>
        </div>
        <div className="mm__body">
          {/* 左栏：提供商 */}
          <aside className="mm__providers">
            <div className="mm__col-title">提供商</div>
            {providers.map(p => (
              <button
                key={p.key}
                type="button"
                className={`mm__provider ${p.key === selectedProvider ? 'is-active' : ''}`}
                onClick={() => { setSelectedProvider(p.key); setEditing(null); setAdding(false); setError(''); setStatus('') }}
                title={p.baseUrl || '未设置 Base URL'}
              >
                <span className="mm__provider-dot" />
                <span className="mm__provider-name">{p.name}</span>
                <span className="mm__provider-count">{p.models.length}</span>
              </button>
            ))}
            <button type="button" className="mm__provider-add" onClick={startAdd}>
              <Plus size={13} />添加提供商
            </button>
          </aside>

          {/* 右栏：该提供商的模型列表 + 配置表单 */}
          <div className="mm__main">
            <div className="mm__col-title">
              模型
              <span className="mm__col-sub">{provider?.baseUrl || ''}</span>
            </div>
            <div className="mm__model-list">
              {provider?.models.map(model => (
                <div key={model.id} className={`mm__model ${editing?.id === model.id || (adding && provider?.key === selectedProvider) ? 'is-editing' : ''} ${model.id === currentModel ? 'is-current' : ''}`}>
                  <div className="mm__model-info">
                    <span className="mm__model-icon" style={{ background: model.color }}>{model.icon}</span>
                    <div className="mm__model-text">
                      <strong>{model.name} {model.id === currentModel && <em>使用中</em>}</strong>
                      <code>{model.model ?? model.id}</code>
                    </div>
                  </div>
                  <div className="mm__model-actions">
                    {model.id === currentModel && <Check size={13} className="mm__model-check" />}
                    <button type="button" onClick={() => startEdit(model)} title="编辑" aria-label={`编辑 ${model.name}`}>
                      <Pencil size={13} />
                    </button>
                    {!model.tags?.includes('Environment') && (
                      <button type="button" className="mm__model-del" onClick={() => void remove(model)} title="删除" aria-label={`删除 ${model.name}`}>
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                </div>
              ))}
              {!provider && <div className="mm__empty">左侧选择一个提供商</div>}
            </div>

            {(editing || adding) && (
              <form className="mm__form" onSubmit={e => { e.preventDefault(); void submit() }}>
                <div className="mm__form-head">
                  <strong>{editing ? `编辑：${editing.name}` : '添加模型'}</strong>
                  <button type="button" onClick={() => { setEditing(null); setAdding(false); setError('') }} aria-label="取消"><X size={13} /></button>
                </div>
                <div className="mm__form-grid">
                  <div className="mm__field">
                    <label>显示名称</label>
                    <input value={form.name} onChange={e => update('name', e.target.value)} placeholder="例如：DeepSeek V4" autoFocus />
                  </div>
                  <div className="mm__field">
                    <label>模型 ID</label>
                    <div className="mm__key-row">
                      <input value={form.model} onChange={e => update('model', e.target.value)} placeholder="例如：deepseek-chat" />
                      <button type="button" onClick={() => void fetchModels()} disabled={!form.base_url.trim() || discovering} title="从该 Base URL 拉取可用模型列表">
                        {discovering ? <LoaderCircle size={13} className="is-spinning" /> : <span className="mm__fetch-text">获取</span>}
                      </button>
                    </div>
                  </div>
                  {discovered && discovered.length > 0 && (
                    <div className="mm__field mm__field--wide">
                      <label>可用模型（点击选用）</label>
                      <div className="mm__discovered">
                        {discovered.map(id => (
                          <button type="button" key={id} className={id === form.model ? 'is-selected' : ''} onClick={() => update('model', id)}>
                            <code>{id}</code>{id === form.model && <Check size={11} />}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  <div className="mm__field mm__field--wide">
                    <label>协议</label>
                    <div className="mm__seg">
                      <button type="button" className={form.protocol === 'anthropic' ? 'is-active' : ''} onClick={() => update('protocol', 'anthropic')}>Anthropic</button>
                      <button type="button" className={form.protocol === 'openai' ? 'is-active' : ''} onClick={() => update('protocol', 'openai')}>OpenAI Compatible</button>
                    </div>
                  </div>
                  <div className="mm__field mm__field--wide">
                    <label>Base URL</label>
                    <input value={form.base_url} onChange={e => update('base_url', e.target.value)} placeholder="https://api.example.com" spellCheck={false} />
                  </div>
                  <div className="mm__field mm__field--wide">
                    <label>
                      API Key
                      {!adding && editing?.has_key && <em>（已配置，留空保留）</em>}
                      {adding && reuseKeyId && <em>（将复用此提供商已保存的密钥）</em>}
                    </label>
                    <div className="mm__key-row">
                      <input
                        type={showKey ? 'text' : 'password'}
                        value={form.api_key}
                        onChange={e => update('api_key', e.target.value)}
                        placeholder={reuseKeyId && !form.api_key ? '留空复用已保存的密钥' : adding ? '可留空（本地服务）' : editing?.has_key ? '留空以保留当前密钥' : '可留空（本地服务）'}
                        autoComplete="new-password"
                      />
                      <button type="button" onClick={() => setShowKey(v => !v)} aria-label={showKey ? '隐藏密钥' : '显示密钥'}>
                        {showKey ? <EyeOff size={13} /> : <Eye size={13} />}
                      </button>
                    </div>
                  </div>
                  <div className="mm__field">
                    <label>思考程度</label>
                    <div className="mm__seg">
                      <button type="button" className={form.thinking_effort === 'off' ? 'is-active' : ''} onClick={() => update('thinking_effort', 'off')}>关闭</button>
                      <button type="button" className={form.thinking_effort === 'high' ? 'is-active' : ''} onClick={() => update('thinking_effort', 'high')}>高</button>
                      <button type="button" className={form.thinking_effort === 'max' ? 'is-active' : ''} onClick={() => update('thinking_effort', 'max')}>最大</button>
                    </div>
                  </div>
                  <div className="mm__field">
                    <label>上下文窗口</label>
                    <input value={form.context_window ?? ''} onChange={e => update('context_window', e.target.value)} placeholder="128k（支持 256k / 1M）" spellCheck={false} />
                  </div>
                  <div className="mm__field">
                    <label>最大输出</label>
                    <input value={form.max_output_tokens ?? ''} onChange={e => update('max_output_tokens', e.target.value)} placeholder="12k（支持 8000 / 32k）" spellCheck={false} />
                  </div>
                </div>
                {error && <div className="mm__error">{error}</div>}
                <div className="mm__form-actions">
                  <button type="button" onClick={() => { setEditing(null); setAdding(false); setError('') }}>取消</button>
                  <button type="submit" className="is-primary" disabled={saving || !form.name.trim() || !form.model.trim()}>
                    <Save size={13} />{saving ? '保存中…' : editing ? '保存修改' : '添加模型'}
                  </button>
                </div>
              </form>
            )}

            {!editing && !adding && provider && (
              <button type="button" className="mm__add-model" onClick={startAdd}>
                <Plus size={13} />在此提供商下添加模型
              </button>
            )}
            {!editing && !adding && status && <div className="mm__status">{status}</div>}
          </div>
        </div>
        {confirmDialog}
      </div>
    </div>
  )
}
