import React, { useCallback, useEffect, useState } from 'react'
import { ChevronDown, FolderGit2, GitBranch, Link2, Menu, TerminalSquare } from 'lucide-react'
import { MessageList } from './MessageList'
import { InputArea } from './InputArea'
import { useChatStream } from '../../hooks/useChatStream'
import { AgentStatusPanel } from './AgentStatusPanel'
import { api } from '../../lib/api'
import type {
  AgentRunSummary, Chat, Model, Message, Step, CmdBlockData, WorkspaceProfile,
} from '../../types'

interface Props {
  chat: Chat
  models: Model[]
  currentModel: string
  agentMode: boolean
  onPushMessage: (msg: Message) => void
  onSelectModel: (id: string) => void
  onOpenMenu: () => void
}

const HINTS = ['扫描当前项目并给出改进建议', '运行测试并修复失败项', '启动本地开发服务']
const noop = () => {}

export function ChatPanel({ chat, models, currentModel, agentMode, onPushMessage, onSelectModel, onOpenMenu }: Props) {
  const [startTime, setStartTime] = useState(Date.now())
  const [workspace, setWorkspace] = useState('')
  const [workspaceDraft, setWorkspaceDraft] = useState('')
  const [workspaceError, setWorkspaceError] = useState('')
  const [workspaceProfile, setWorkspaceProfile] = useState<WorkspaceProfile | null>(null)
  const [recentWorkspaces, setRecentWorkspaces] = useState<string[]>([])
  const [workspaceLoading, setWorkspaceLoading] = useState(true)

  useEffect(() => {
    let active = true
    setWorkspace('')
    setWorkspaceDraft('')
    setWorkspaceProfile(null)
    setWorkspaceError('')
    setWorkspaceLoading(true)
    api.getWorkspace(chat.sessionId)
      .then(result => {
        if (!active) return
        const path = result.workspace ?? ''
        setWorkspace(path)
        setWorkspaceDraft(path)
        setWorkspaceProfile(result.profile)
        setRecentWorkspaces(result.recent)
      })
      .catch(reason => {
        if (active) setWorkspaceError(reason instanceof Error ? reason.message : '无法读取工作区')
      })
      .finally(() => { if (active) setWorkspaceLoading(false) })
    return () => { active = false }
  }, [chat.sessionId])

  const handleDone = useCallback((
    content: string,
    steps: Step[],
    cmdBlocks: CmdBlockData[],
    agentRun: AgentRunSummary,
  ) => {
    onPushMessage({
      id: `msg-${Date.now()}`,
      role: 'assistant',
      content,
      time: new Date().toISOString(),
      steps: steps.length ? steps : undefined,
      cmdBlocks: cmdBlocks.length ? cmdBlocks : undefined,
      agentRun,
    })
  }, [onPushMessage])

  const handleError = useCallback((msg: string) => {
    onPushMessage({ id: `msg-${Date.now()}`, role: 'assistant', content: `错误：${msg}`, time: new Date().toISOString() })
  }, [onPushMessage])

  const { state, send, stop, answerApproval, stopManagedProcess } = useChatStream(
    chat.sessionId,
    agentMode,
    workspace,
    noop,
    handleDone,
    handleError,
  )

  useEffect(() => {
    if (state.workspace && state.workspace !== workspace) {
      setWorkspace(state.workspace)
      setWorkspaceDraft(state.workspace)
    }
  }, [state.workspace, workspace])

  const bindWorkspace = useCallback(async (requestedPath?: string) => {
    const path = (requestedPath ?? workspaceDraft).trim()
    if (!path) return
    try {
      const result = await api.bindWorkspace(chat.sessionId, path)
      const boundPath = result.workspace ?? path
      setWorkspace(boundPath)
      setWorkspaceDraft(boundPath)
      setWorkspaceProfile(result.profile)
      setRecentWorkspaces(result.recent)
      setWorkspaceError('')
    } catch (err) {
      setWorkspaceError(err instanceof Error ? err.message : '无法绑定工作区')
    }
  }, [chat.sessionId, workspaceDraft])

  const handleSend = useCallback((msg: string) => {
    if (workspaceLoading) return
    setStartTime(Date.now())
    onPushMessage({ id: `msg-${Date.now()}`, role: 'user', content: msg, time: new Date().toISOString() })
    send(msg)
  }, [send, onPushMessage, workspaceLoading])

  const isEmpty = chat.messages.length === 0 && !state.isGenerating

  return (
    <div className="flex flex-col h-full min-w-0">
      <div className="workspace-bar">
        <button type="button" className="mobile-menu-button" onClick={onOpenMenu} title="打开导航" aria-label="打开导航">
          <Menu size={17} />
        </button>
        <details className="workspace-switcher">
          <summary className="workspace-switcher__trigger">
            <span className="workspace-switcher__icon"><FolderGit2 size={15} /></span>
            <span className="workspace-switcher__title">
              <strong>{workspaceProfile?.name || '选择工作区'}</strong>
              <small>{workspaceProfile?.branch || (workspace ? '本会话' : '正在加载')}</small>
            </span>
            <ChevronDown size={14} />
          </summary>
          <div className="workspace-menu">
            {workspaceProfile && (
              <div className="workspace-menu__current">
                <div>
                  <strong>{workspaceProfile.name}</strong>
                  <span title={workspaceProfile.path}>{workspaceProfile.path}</span>
                </div>
                <div className="workspace-tags">
                  {workspaceProfile.git && <span><GitBranch size={11} />{workspaceProfile.branch || 'Git'}</span>}
                  {workspaceProfile.python && <span>Python</span>}
                  {workspaceProfile.venv && <span>.venv</span>}
                  {workspaceProfile.node && <span>Node</span>}
                </div>
              </div>
            )}
            {recentWorkspaces.length > 0 && (
              <div className="workspace-menu__recent">
                <label>最近使用</label>
                {recentWorkspaces.map(path => (
                  <button key={path} type="button" onClick={() => void bindWorkspace(path)} title={path}>
                    <FolderGit2 size={13} />
                    <span>{path.split(/[\\/]/).filter(Boolean).pop() || path}</span>
                    <small>{path}</small>
                  </button>
                ))}
              </div>
            )}
            <div className="workspace-menu__bind">
              <label htmlFor={`workspace-${chat.sessionId}`}>本地绝对路径</label>
              <div>
                <input
                  id={`workspace-${chat.sessionId}`}
                  value={workspaceDraft}
                  onChange={event => setWorkspaceDraft(event.target.value)}
                  onKeyDown={event => { if (event.key === 'Enter') void bindWorkspace() }}
                  placeholder="E:\\projects\\my-app"
                  className="workspace-input"
                />
                <button type="button" onClick={() => void bindWorkspace()} className="workspace-bind" title="绑定工作区">
                  <Link2 size={14} />
                </button>
              </div>
              {workspaceError && <span className="workspace-error">{workspaceError}</span>}
            </div>
          </div>
        </details>
        <div className="agent-online"><span />本地 Agent</div>
      </div>
      {isEmpty ? (
        <div className="empty-workbench">
          <div className="empty-workbench__mark"><TerminalSquare size={22} /></div>
          <h2>准备执行本地任务</h2>
          <div className="empty-workbench__actions">
            {HINTS.map(h => (
              <button key={h} onClick={() => handleSend(h)}
                className="task-template">
                {h}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <MessageList
          messages={chat.messages}
          isGenerating={state.isGenerating}
          streamSteps={state.steps}
          streamCmdBlocks={state.cmdBlocks}
          streamContent={state.partialContent}
          startTime={startTime}
        />
      )}
      {state.approval && (
        <AgentStatusPanel
          plan={[]}
          fileChanges={[]}
          verification={null}
          processes={[]}
          approval={state.approval}
          onAnswerApproval={answerApproval}
          onStopProcess={stopManagedProcess}
        />
      )}
      <InputArea
        isGenerating={state.isGenerating || workspaceLoading}
        currentModel={currentModel}
        models={models}
        agentMode={agentMode}
        onSend={handleSend}
        onStop={stop}
        onSelectModel={onSelectModel}
      />
    </div>
  )
}
