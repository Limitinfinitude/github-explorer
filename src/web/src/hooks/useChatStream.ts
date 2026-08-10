import { useRef, useState, useCallback, useEffect } from 'react'
import { api } from '../lib/api'
import type {
  Step, CmdBlockData, SSEEvent, AgentFileChange, AgentRunSummary,
  AgentVerification, AgentProcess, AgentApproval,
} from '../types'

export type StreamState = {
  isGenerating: boolean
  steps: Step[]
  cmdBlocks: CmdBlockData[]
  partialContent: string
  workspace: string
  taskId: string | null
  plan: string[]
  repoMap: string
  fileChanges: AgentFileChange[]
  verification: AgentVerification | null
  processes: AgentProcess[]
  approval: AgentApproval | null
}

type DoneHandler = (
  content: string,
  steps: Step[],
  cmdBlocks: CmdBlockData[],
  agentRun: AgentRunSummary,
) => void

function initialState(workspace: string): StreamState {
  return {
    isGenerating: false,
    steps: [],
    cmdBlocks: [],
    partialContent: '',
    workspace,
    taskId: null,
    plan: [],
    repoMap: '',
    fileChanges: [],
    verification: null,
    processes: [],
    approval: null,
  }
}

function summaryOf(state: StreamState): AgentRunSummary {
  return {
    taskId: state.taskId,
    plan: state.plan,
    repoMap: state.repoMap,
    fileChanges: state.fileChanges,
    verification: state.verification,
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
      if (!active || !task || task.status !== 'waiting_approval' || !task.active_batch) return
      const tool = task.active_batch.tool_uses[task.active_batch.next_index]
      if (!tool) return
      const verification = task.summary?.verification ?? []
      const processes = (task.summary?.processes ?? []).map(process => ({
        processId: String(process.process_id),
        status: String(process.status ?? 'unknown'),
        pid: typeof process.pid === 'number' ? process.pid : undefined,
        cwd: typeof process.cwd === 'string' ? process.cwd : undefined,
        url: typeof process.url === 'string' ? process.url : undefined,
      }))
      commit(current => ({
        ...current,
        taskId: task.task_id,
        plan: task.plan ?? [],
        repoMap: task.repo_map ?? '',
        fileChanges: activity.changesets.map(change => ({ files: change.files, diff: change.diff })),
        verification: verification.length
          ? { success: verification.every(check => check.success), checks: verification }
          : null,
        processes,
        approval: {
          taskId: task.task_id,
          toolName: tool.name,
          args: tool.input,
          reason: '该任务在服务重启或页面刷新前等待确认',
        },
      }))
    }).catch(() => {})
    return () => { active = false }
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
          processes: result.processes.map(process => {
            const previous = current.processes.find(item => item.processId === process.processId)
            return { ...previous, ...process }
          }),
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
  ) => {
    const steps = reset ? [] : [...stateRef.current.steps]
    const cmdBlocks = reset ? [] : [...stateRef.current.cmdBlocks]
    let activeCmdId: string | null = null
    let fullContent = ''

    if (reset) {
      commit(current => ({ ...initialState(workspace), workspace: current.workspace || workspace, isGenerating: true }))
    } else {
      commit(current => ({ ...current, isGenerating: true, approval: null, partialContent: '' }))
    }

    try {
      for await (const event of stream) {
        if (ctrl.signal.aborted) return
        const e = event as SSEEvent
        if (e.type === 'workspace') {
          commit(current => ({ ...current, workspace: e.path }))
        } else if (e.type === 'plan') {
          commit(current => ({ ...current, taskId: e.task_id, plan: e.steps }))
        } else if (e.type === 'repo_map') {
          commit(current => ({ ...current, taskId: e.task_id, repoMap: e.content }))
        } else if (e.type === 'step') {
          steps.push({ icon: e.icon || 'activity', text: e.step, done: true })
          commit(current => ({ ...current, steps: [...steps] }))
        } else if (e.type === 'tool_call') {
          steps.push({ icon: 'tool', text: `${e.name}(...)`, done: false })
          commit(current => ({ ...current, steps: [...steps] }))
        } else if (e.type === 'tool_result') {
          const offset = [...steps].reverse().findIndex(item => !item.done && item.text.includes(e.name))
          if (offset !== -1) {
            const index = steps.length - 1 - offset
            steps[index] = { ...steps[index], done: true, text: `${steps[index].text} ${e.success ? '完成' : '失败'}` }
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
            fileChanges: [...current.fileChanges, { files: e.files, diff: e.diff }],
          }))
        } else if (e.type === 'verification') {
          commit(current => ({
            ...current,
            taskId: e.task_id,
            verification: { success: e.success, checks: e.checks },
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
          if (e.status === 'waiting_approval') {
            commit(current => ({ ...current, isGenerating: false, partialContent: '' }))
            return
          }
          fullContent = e.content || fullContent
          const completed = commit(current => ({
            ...current,
            isGenerating: false,
            steps: [...steps],
            cmdBlocks: [...cmdBlocks],
            partialContent: '',
          }))
          onDone(fullContent, steps, cmdBlocks, summaryOf(completed))
          return
        } else if (e.type === 'error') {
          commit(current => ({ ...current, isGenerating: false }))
          onError(e.content)
          return
        }
      }
    } catch (err: unknown) {
      commit(current => ({ ...current, isGenerating: false }))
      if (err instanceof Error && err.name !== 'AbortError') onError(err.message)
    }
  }, [commit, onDone, onError, onToken, workspace])

  const send = useCallback((message: string, repo?: string) => {
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    void consume(api.chatStream(message, repo, ctrl.signal, sessionId, agentMode, workspace || undefined), ctrl, true)
  }, [sessionId, agentMode, workspace, consume])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    commit(current => ({ ...current, isGenerating: false }))
  }, [commit])

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
