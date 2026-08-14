import type {
  AgentAcceptanceItem, AgentProcess, AgentTrace, AgentTraceDetail, CustomModelInput,
  DefaultWorkspaceResponse, Model, ObservabilityStatus, ProjectEvidence, ProjectOverview, ProjectSummary, Repo, SSEEvent, WorkspaceResponse,
} from '../types'
import type { CanonicalHistoryRow } from './chatHistory'
import type { ModelDiscoveryResult, ProbeResult } from './modelProbe'
import { normalizeProcessStatus } from './processState'

async function* readSSE(res: Response): AsyncGenerator<SSEEvent> {
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  if (!res.body) throw new Error('Response body is null')
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try { yield JSON.parse(line.slice(6)) } catch { /* ignore malformed event */ }
    }
  }
}

export const api = {
  async *chatStream(
    message: string,
    repo: string | undefined,
    signal: AbortSignal,
    sessionId: string,
    agentMode: boolean,
    workspace?: string,
  ): AsyncGenerator<SSEEvent> {
    const res = await fetch('/api/agent/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, repo, session_id: sessionId, agent_mode: agentMode, workspace }),
      signal,
    })
    yield* readSSE(res)
  },

  async bindWorkspace(sessionId: string, path: string) {
    const res = await fetch('/api/agent/workspace', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, path }),
    })
    if (!res.ok) throw new Error(`绑定工作区失败：HTTP ${res.status}`)
    return res.json() as Promise<WorkspaceResponse>
  },

  async getWorkspace(sessionId: string) {
    const res = await fetch(`/api/agent/workspace/${encodeURIComponent(sessionId)}`)
    if (!res.ok) throw new Error(`读取工作区失败：HTTP ${res.status}`)
    return res.json() as Promise<WorkspaceResponse>
  },

  async getHistory(sessionId: string): Promise<CanonicalHistoryRow[]> {
    const res = await fetch(`/api/agent/history/${encodeURIComponent(sessionId)}`)
    if (!res.ok) throw new Error(`读取会话历史失败：HTTP ${res.status}`)
    const data = await res.json() as { history?: CanonicalHistoryRow[] }
    return data.history ?? []
  },

  async getDefaultWorkspace() {
    const res = await fetch('/api/agent/workspace/default')
    if (!res.ok) throw new Error(`读取默认工作目录失败：HTTP ${res.status}`)
    return res.json() as Promise<DefaultWorkspaceResponse>
  },

  async setDefaultWorkspace(path: string) {
    const res = await fetch('/api/agent/workspace/default', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({})) as { detail?: string }
      throw new Error(data.detail || `保存默认工作目录失败：HTTP ${res.status}`)
    }
    return res.json() as Promise<DefaultWorkspaceResponse>
  },

  async getTraces(limit = 50): Promise<AgentTrace[]> {
    const res = await fetch(`/api/agent/traces?limit=${limit}`)
    if (!res.ok) throw new Error(`读取运行记录失败：HTTP ${res.status}`)
    const data = await res.json() as { traces: AgentTrace[] }
    return data.traces
  },

  async getObservability(): Promise<ObservabilityStatus> {
    const res = await fetch('/api/agent/observability')
    if (!res.ok) throw new Error(`读取监控状态失败：HTTP ${res.status}`)
    return res.json()
  },

  async getTraceDetail(taskId: string): Promise<AgentTraceDetail> {
    const res = await fetch(`/api/agent/tasks/${encodeURIComponent(taskId)}`)
    if (!res.ok) throw new Error(`读取任务详情失败：HTTP ${res.status}`)
    return res.json()
  },

  async getProjectOverview(projectId: string): Promise<ProjectOverview> {
    const res = await fetch(`/api/projects/${encodeURIComponent(projectId)}/overview`)
    if (!res.ok) throw new Error(`读取项目概览失败：HTTP ${res.status}`)
    return res.json()
  },

  async getProjects(): Promise<ProjectSummary[]> {
    const res = await fetch('/api/projects')
    if (!res.ok) throw new Error(`读取项目列表失败：HTTP ${res.status}`)
    const data = await res.json() as { projects?: ProjectSummary[] }
    return data.projects ?? []
  },

  async getProjectEvidence(projectId: string): Promise<ProjectEvidence> {
    const res = await fetch(`/api/projects/${encodeURIComponent(projectId)}/evidence`)
    if (!res.ok) throw new Error(`读取项目证据失败：HTTP ${res.status}`)
    return res.json()
  },

  async getActiveTask(sessionId: string) {
    const res = await fetch(`/api/agent/sessions/${encodeURIComponent(sessionId)}/active-task`)
    if (!res.ok) throw new Error(`读取活动任务失败：HTTP ${res.status}`)
    return res.json() as Promise<{
      task: null | {
        task_id: string
        status: string
        plan?: string[]
        repo_map?: string
        active_batch?: {
          next_index: number
          tool_uses: Array<{ name: string; input: Record<string, unknown> }>
        }
        summary?: {
          changed_files?: string[]
          verification?: Array<{ command: string; success: boolean; returncode?: number; output?: string }>
          acceptance?: AgentAcceptanceItem[]
          processes?: Array<Record<string, unknown>>
        }
      }
      activity: {
        events: Array<{ sequence: number; type: string; payload: Record<string, unknown>; created_at: string }>
        changesets: Array<{ files: string[]; diff: string }>
      }
    }>
  },

  async approveOperation(sessionId: string, taskId: string, approved: boolean) {
    const res = await fetch('/api/agent/approval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, task_id: taskId, approved }),
    })
    if (!res.ok) throw new Error(`确认操作失败：HTTP ${res.status}`)
    return res.json()
  },

  async cancelTask(sessionId: string, taskId: string) {
    const res = await fetch(`/api/agent/tasks/${encodeURIComponent(taskId)}/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    })
    const data = await res.json().catch(() => ({})) as { success?: boolean; error?: string }
    if (!res.ok || !data.success) {
      throw new Error(data.error || `取消任务失败：HTTP ${res.status}`)
    }
    return data
  },

  async *approvalStream(
    sessionId: string,
    taskId: string,
    approved: boolean,
    signal: AbortSignal,
  ): AsyncGenerator<SSEEvent> {
    const res = await fetch('/api/agent/approval/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, task_id: taskId, approved }),
      signal,
    })
    yield* readSSE(res)
  },

  async listProcesses(sessionId: string): Promise<{ processes: AgentProcess[] }> {
    const res = await fetch(`/api/agent/processes/${encodeURIComponent(sessionId)}`)
    if (!res.ok) throw new Error(`读取后台进程失败：HTTP ${res.status}`)
    const data = await res.json() as { processes: Array<Record<string, unknown>> }
    return {
      processes: data.processes.map(process => ({
        processId: String(process.process_id),
        status: normalizeProcessStatus(process.status),
        pid: typeof process.pid === 'number' ? process.pid : undefined,
        cwd: typeof process.cwd === 'string' ? process.cwd : undefined,
        command: typeof process.command === 'string' ? process.command : undefined,
        logs: typeof process.logs === 'string' ? process.logs : undefined,
        returncode: typeof process.returncode === 'number' || process.returncode === null
          ? process.returncode : undefined,
      })),
    }
  },

  async stopProcess(sessionId: string, processId: string) {
    const res = await fetch(`/api/agent/processes/${encodeURIComponent(sessionId)}/${encodeURIComponent(processId)}/stop`, {
      method: 'POST',
    })
    if (!res.ok) throw new Error(`停止进程失败：HTTP ${res.status}`)
    return res.json()
  },

  async searchRepos(q: string, lang: string): Promise<Repo[]> {
    const params = new URLSearchParams({ q, lang })
    const res = await fetch(`/api/search?${params}`)
    if (!res.ok) throw new Error(`搜索失败：HTTP ${res.status}`)
    const data = await res.json()
    if (data.error) throw new Error(data.error)
    return data.repos ?? []
  },

  async getTrending(period: number, lang: string): Promise<Repo[]> {
    const params = new URLSearchParams({ period: String(period), lang })
    const res = await fetch(`/api/trending?${params}`)
    if (!res.ok) throw new Error(`读取趋势失败：HTTP ${res.status}`)
    const data = await res.json()
    if (data.error) throw new Error(data.error)
    return data.repos ?? []
  },

  async getModels() {
    const res = await fetch('/api/settings')
    if (!res.ok) return { models: [] as Model[], current_model: null, active_model: null }
    return res.json() as Promise<{ models: Model[]; current_model: string | null; active_model: string | null }>
  },

  async selectModel(modelId: string) {
    await fetch('/api/settings/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model_id: modelId }),
    })
  },

  async saveModel(modelId: string, config: Record<string, string>) {
    await fetch(`/api/settings/models/${modelId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
  },

  async createModel(config: CustomModelInput) {
    const res = await fetch('/api/settings/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
    const data = await res.json()
    if (!res.ok || !data.ok) {
      throw new Error(data.detail ?? data.error ?? `保存失败：HTTP ${res.status}`)
    }
    return data as { ok: true; model: Model }
  },

  async measureModelUrl(baseUrl: string): Promise<ProbeResult> {
    const res = await fetch('/api/settings/models/latency', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: baseUrl }),
    })
    if (!res.ok) throw new Error(`测速失败：HTTP ${res.status}`)
    return res.json()
  },

  async discoverModels(config: Pick<CustomModelInput, 'protocol' | 'base_url' | 'api_key'>): Promise<ModelDiscoveryResult> {
    const res = await fetch('/api/settings/models/discover', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
    if (!res.ok) throw new Error(`获取模型失败：HTTP ${res.status}`)
    return res.json()
  },

  async testModelConnection(config: Pick<CustomModelInput, 'protocol' | 'base_url' | 'api_key' | 'model'>): Promise<ProbeResult> {
    const res = await fetch('/api/settings/models/test-connection', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
    if (!res.ok) throw new Error(`连接测试失败：HTTP ${res.status}`)
    return res.json()
  },
}
