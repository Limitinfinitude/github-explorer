import { useRef, useState, useCallback, useEffect } from 'react'
import { api } from '../lib/api'
import type {
  Step, CmdBlockData, SSEEvent, AgentFileChange, AgentRunSummary,
  ContextUsage,
  AgentVerification, AgentProcess, AgentApproval,
  AgentAcceptanceItem, ThinkingSegment,
} from '../types'
import { normalizeProcessStatus, reconcileProcesses } from '../lib/processState'
import { isRecoverableTaskStatus } from '../lib/taskRecovery'

export type StreamState = {
  isGenerating: boolean
  steps: Step[]
  cmdBlocks: CmdBlockData[]
  narrations: string[]
  thinking: ThinkingSegment[]
  partialContent: string
  workspace: string
  taskId: string | null
  status: AgentRunSummary['status']
  plan: string[]
  repoMap: string
  fileChanges: AgentFileChange[]
  verification: AgentVerification | null
  acceptance: AgentAcceptanceItem[]
  processes: AgentProcess[]
  approval: AgentApproval | null
  contextUsage: ContextUsage | null
}

type DoneHandler = (
  content: string,
  steps: Step[],
  cmdBlocks: CmdBlockData[],
  agentRun: AgentRunSummary,
  narrations: string[],
  thinking: ThinkingSegment[],
) => void

type StreamConsumer = (
  stream: AsyncGenerator<SSEEvent>,
  ctrl: AbortController,
  reset: boolean,
  seedTaskId?: string | null,
) => Promise<void>

function initialState(workspace: string): StreamState {
  return {
    isGenerating: false,
    steps: [],
    cmdBlocks: [],
    narrations: [],
    thinking: [],
    partialContent: '',
    workspace,
    taskId: null,
    status: null,
    plan: [],
    repoMap: '',
    fileChanges: [],
    verification: null,
    acceptance: [],
    processes: [],
    approval: null,
    contextUsage: null,
  }
}

function summaryOf(state: StreamState): AgentRunSummary {
  return {
    taskId: state.taskId,
    status: state.status,
    plan: state.plan,
    repoMap: state.repoMap,
    fileChanges: state.fileChanges,
    verification: state.verification,
    acceptance: state.acceptance,
    processes: state.processes,
  }
}

export function useChatStream(
  sessionId: string,
  agentMode: boolean,
  workspace: string,
  onToken: (text: string) => void,
  onDone: DoneHandler,
  onError: (msg: string) => void,
) {
  const abortRef = useRef<AbortController | null>(null)
  const consumeRef = useRef<StreamConsumer | null>(null)
  const lastSequenceRef = useRef(0)
  const [state, setState] = useState<StreamState>(() => initialState(workspace))
  const stateRef = useRef(state)

  const commit = useCallback((update: (current: StreamState) => StreamState) => {
    const next = update(stateRef.current)
    stateRef.current = next
    setState(next)
    return next
  }, [])

  useEffect(() => {
    commit(current => ({ ...current, workspace }))
  }, [workspace, commit])

  useEffect(() => {
    if (!agentMode) return
    let active = true
    api.getActiveTask(sessionId).then(({ task, activity }) => {
      if (!active || !task || !isRecoverableTaskStatus(task.status)) return
      lastSequenceRef.current = activity.events.reduce(
        (highest, event) => Math.max(highest, event.sequence),
        0,
      )
      const verification = task.summary?.verification ?? []
      const processes = (task.summary?.processes ?? []).map(process => ({
        processId: String(process.process_id),
        status: normalizeProcessStatus(process.status),
        pid: typeof process.pid === 'number' ? process.pid : undefined,
        cwd: typeof process.cwd === 'string' ? process.cwd : undefined,
        url: typeof process.url === 'string' ? process.url : undefined,
      }))
      commit(current => ({
        ...current,
        taskId: task.task_id,
        status: task.status as AgentRunSummary['status'],
        isGenerating: task.status !== 'waiting_approval',
        plan: task.plan ?? [],
        repoMap: task.repo_map ?? '',
        fileChanges: activity.changesets.map(change => ({ files: change.files, diff: change.diff })),
        verification: verification.length
          ? { success: verification.every(check => check.success), checks: verification }
          : null,
        acceptance: task.summary?.acceptance ?? [],
        processes,
        approval: null,
      }))
      if (task.status === 'waiting_approval' && task.active_batch) {
        const tool = task.active_batch.tool_uses[task.active_batch.next_index]
        if (!tool) return
        commit(current => ({
          ...current,
          isGenerating: false,
          approval: {
            taskId: task.task_id,
            toolName: tool.name,
            args: tool.input,
            reason: '该任务正在等待操作确认',
          },
        }))
        return
      }
      const ctrl = new AbortController()
      abortRef.current = ctrl
      void consumeRef.current?.(
        api.taskEvents(task.task_id, ctrl.signal, 0),
        ctrl,
        true,
        task.task_id,
      )
    }).catch(() => {})
    return () => {
      active = false
      abortRef.current?.abort()
    }
  }, [agentMode, sessionId, commit])

  useEffect(() => {
    if (!agentMode) return
    let active = true
    const refresh = async () => {
      try {
        const result = await api.listProcesses(sessionId)
        if (!active) return
        commit(current => ({
          ...current,
          processes: reconcileProcesses(current.processes, result.processes),
        }))
      } catch { /* the stream remains usable when polling is unavailable */ }
    }
    refresh()
    const timer = window.setInterval(refresh, 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [agentMode, sessionId, commit])

  const consume = useCallback(async (
    stream: AsyncGenerator<SSEEvent>,
    ctrl: AbortController,
    reset: boolean,
    seedTaskId: string | null = null,
  ) => {
    const steps = reset ? [] : [...stateRef.current.steps]
    const cmdBlocks = reset ? [] : [...stateRef.current.cmdBlocks]
    let activeCmdId: string | null = null
    let fullContent = ''
    let terminalSeen = false

    if (reset) {
      commit(current => ({
        ...initialState(workspace),
        workspace: current.workspace || workspace,
        taskId: seedTaskId,
        isGenerating: true,
      }))
    } else {
      commit(current => ({ ...current, isGenerating: true, approval: null, partialContent: '', narrations: [], thinking: [] }))
    }

    try {
      for await (const event of stream) {
        if (ctrl.signal.aborted) return
        const e = event as SSEEvent
        if ('sequence' in e && typeof e.sequence === 'number') {
          lastSequenceRef.current = Math.max(lastSequenceRef.current, e.sequence)
        }
        if (e.type === 'workspace') {
          commit(current => ({ ...current, workspace: e.path, taskId: e.task_id ?? current.taskId }))
        } else if (e.type === 'plan') {
          commit(current => ({ ...current, taskId: e.task_id, plan: e.steps }))
        } else if (e.type === 'repo_map') {
          commit(current => ({ ...current, taskId: e.task_id, repoMap: e.content }))
        } else if (e.type === 'step') {
          steps.push({ icon: e.icon || 'activity', text: e.step, done: true })
          commit(current => ({ ...current, steps: [...steps] }))
        } else if (e.type === 'narration') {
          commit(current => ({ ...current, narrations: [...current.narrations, e.content] }))
        } else if (e.type === 'thinking') {
          commit(current => {
            const thinking = [...current.thinking]
            const round = e.round ?? 0
            const last = thinking[thinking.length - 1]
            // 同一轮次的连续增量追加到当前段，新轮次/新段则开一段
            if (last && last.round === round) {
              thinking[thinking.length - 1] = { content: last.content + e.content, round }
            } else {
              thinking.push({ content: e.content, round })
            }
            return { ...current, thinking }
          })
        } else if (e.type === 'tool_call') {
          steps.push({
            icon: 'tool', text: `${e.name}(...)`, done: false,
            callId: e.call_id, toolName: e.name, args: e.args, status: 'running',
          })
          commit(current => ({ ...current, steps: [...steps] }))
        } else if (e.type === 'tool_result') {
          let index = steps.findIndex(item => item.callId === e.call_id)
          if (index === -1) {
            const offset = [...steps].reverse().findIndex(item => !item.done && item.text.includes(e.name))
            if (offset !== -1) index = steps.length - 1 - offset
          }
          if (index !== -1) {
            steps[index] = {
              ...steps[index], done: true,
              status: e.success ? 'succeeded' : 'failed',
              text: `${e.name}(...) ${e.success ? '完成' : '失败'}`,
              output: e.success ? (e.output || '').slice(0, 4000) : undefined,
              error: e.error || (e.success ? undefined : '工具执行失败'),
            }
          }
          commit(current => ({ ...current, steps: [...steps] }))
        } else if (e.type === 'tool_recovered') {
          const index = steps.findIndex(item => item.callId === e.failed_call_id)
          if (index !== -1) {
            steps[index] = {
              ...steps[index],
              recoveredByCallId: e.recovered_by_call_id,
            }
          }
          commit(current => ({ ...current, steps: [...steps] }))
        } else if (e.type === 'cmd_preview') {
          const block: CmdBlockData = {
            id: `cmd-${Date.now()}`,
            command: e.command,
            risk: e.risk,
            reason: e.reason,
            lines: [],
            done: false,
          }
          cmdBlocks.push(block)
          activeCmdId = block.id
          commit(current => ({ ...current, cmdBlocks: [...cmdBlocks] }))
        } else if (e.type === 'cmd_line') {
          const block = cmdBlocks.find(item => item.id === activeCmdId)
          if (block) {
            block.lines.push(e.text)
            commit(current => ({ ...current, cmdBlocks: [...cmdBlocks] }))
          }
        } else if (e.type === 'cmd_done') {
          const block = cmdBlocks.find(item => item.id === activeCmdId)
          if (block) {
            block.done = true
            block.success = e.success
            commit(current => ({ ...current, cmdBlocks: [...cmdBlocks] }))
          }
          activeCmdId = null
        } else if (e.type === 'file_changed') {
          commit(current => ({
            ...current,
            taskId: e.task_id,
            fileChanges: [...current.fileChanges, { files: e.files, diff: e.diff, pathKinds: e.path_kinds }],
          }))
        } else if (e.type === 'verification') {
          commit(current => ({
            ...current,
            taskId: e.task_id,
            verification: { success: e.success, checks: e.checks },
          }))
        } else if (e.type === 'acceptance') {
          commit(current => ({
            ...current,
            taskId: e.task_id,
            acceptance: e.items,
          }))
        } else if (e.type === 'process_started') {
          commit(current => {
            const process: AgentProcess = {
              processId: e.process_id,
              status: e.data.status ?? 'running',
              pid: e.data.pid,
              cwd: e.data.cwd,
            }
            return {
              ...current,
              taskId: e.task_id,
              processes: [...current.processes.filter(item => item.processId !== process.processId), process],
            }
          })
        } else if (e.type === 'context_usage') {
          commit(cur => ({ ...cur, contextUsage: {
            window: Number(e.window || 0),
            used: Number(e.used || 0),
            breakdown: {
              history: Number(e.breakdown?.history || 0),
              tools_system: Number(e.breakdown?.tools_system || 0),
              tools_mcp: Number(e.breakdown?.tools_mcp || 0),
              system_prompt: Number(e.breakdown?.system_prompt || 0),
              other: Number(e.breakdown?.other || 0),
            },
            cache_hit_tokens: Number(e.cache_hit_tokens || 0),
            cache_hit_rate: e.cache_hit_rate == null ? null : Number(e.cache_hit_rate),
            compactions: Number(e.compactions || 0),
          }}))
        } else if (e.type === 'budget_warning') {
          steps.push({ icon: 'activity', text: e.message, done: true, status: 'succeeded' })
          commit(current => ({
            ...current,
            taskId: e.task_id,
            plan: e.plan ?? current.plan,
            steps: [...steps],
          }))
        } else if (e.type === 'approval_required') {
          commit(current => ({
            ...current,
            taskId: e.task_id,
            approval: { taskId: e.task_id, toolName: e.tool_name, args: e.args, reason: e.reason },
          }))
        } else if (e.type === 'token') {
          fullContent += e.content
          commit(current => ({ ...current, partialContent: fullContent }))
          onToken(e.content)
        } else if (e.type === 'done') {
          terminalSeen = true
          if (e.status === 'waiting_approval') {
            commit(current => ({
              ...current,
              status: 'waiting_approval',
              isGenerating: false,
              partialContent: '',
              narrations: [],
              thinking: [],
            }))
            return
          }
          const lastThinking = stateRef.current.thinking
          const lastNarrations = stateRef.current.narrations
          fullContent = e.content || fullContent
          const completed = commit(current => ({
            ...current,
            status: (e.status ?? null) as AgentRunSummary['status'],
            isGenerating: false,
            steps: [...steps],
            cmdBlocks: [...cmdBlocks],
            partialContent: '',
            narrations: [],
            thinking: [],
          }))
          onDone(fullContent, steps, cmdBlocks, summaryOf(completed), lastNarrations, lastThinking)
          return
        } else if (e.type === 'error') {
          steps.push({ icon: 'activity', text: e.content, done: true, status: 'failed' })
          commit(current => ({ ...current, steps: [...steps] }))
          onError(e.content)
        } else if (e.type === 'input_warning') {
          steps.push({ icon: 'activity', text: e.message, done: true, status: 'failed' })
          commit(current => ({ ...current, steps: [...steps], isGenerating: false }))
          onError(e.message)
        }
      }
      if (!terminalSeen && !ctrl.signal.aborted) {
        commit(current => ({ ...current, isGenerating: false }))
        onError('任务事件连接已结束；任务仍由后台管理，重新进入会话可恢复进度。')
      }
    } catch (err: unknown) {
      commit(current => ({ ...current, isGenerating: false }))
      if (err instanceof Error && err.name !== 'AbortError') onError(err.message)
    }
  }, [commit, onDone, onError, onToken, workspace])
  consumeRef.current = consume

  const send = useCallback((message: string, thinkingEffort?: string, planMode?: boolean) => {
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    lastSequenceRef.current = 0
    commit(current => ({
      ...initialState(workspace),
      workspace: current.workspace || workspace,
      isGenerating: true,
    }))
    void api.startAgentTask(message, sessionId, workspace || undefined, thinkingEffort, planMode)
      .then(started => {
        if (ctrl.signal.aborted) return
        commit(current => ({
          ...current,
          taskId: started.task_id,
          workspace: started.workspace || current.workspace,
          status: 'pending',
        }))
        return consume(
          api.taskEvents(started.task_id, ctrl.signal, 0),
          ctrl,
          false,
          started.task_id,
        )
      })
      .catch(err => {
        if (ctrl.signal.aborted) return
        commit(current => ({ ...current, isGenerating: false }))
        onError(err instanceof Error ? err.message : '无法启动任务')
      })
  }, [sessionId, workspace, consume, commit, onError])

  const stop = useCallback(() => {
    const taskId = stateRef.current.taskId
    const abort = () => {
      abortRef.current?.abort()
      commit(current => ({
        ...current,
        isGenerating: false,
        status: taskId ? 'cancelled' : current.status,
      }))
    }
    if (!taskId) {
      abort()
      return
    }
    void api.cancelTask(sessionId, taskId)
      .catch(err => onError(err instanceof Error ? err.message : '取消任务失败'))
      .finally(abort)
  }, [sessionId, onError, commit])

  const answerApproval = useCallback((approved: boolean) => {
    const approval = stateRef.current.approval
    if (!approval) return
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    void consume(api.approvalStream(sessionId, approval.taskId, approved, ctrl.signal), ctrl, false)
  }, [sessionId, consume])

  const stopManagedProcess = useCallback(async (processId: string) => {
    try {
      const result = await api.stopProcess(sessionId, processId)
      commit(current => ({
        ...current,
        processes: current.processes.map(process => process.processId === processId
          ? { ...process, status: result.success ? 'stopped' : process.status }
          : process),
      }))
    } catch (err) {
      onError(err instanceof Error ? err.message : '停止进程失败')
    }
  }, [sessionId, onError, commit])

  return { state, send, stop, answerApproval, stopManagedProcess }
}
