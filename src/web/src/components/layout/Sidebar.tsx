import React, { useEffect, useState } from 'react'
import {
  Activity, ChevronDown, Compass, FolderKanban, MessageSquare, PanelLeftClose, Plus, Settings, TerminalSquare, Trash2, Upload,
} from 'lucide-react'
import { api } from '../../lib/api'
import type { Chat, ProjectSummary, View } from '../../types'

interface Props {
  open: boolean
  chats: Chat[]
  projects: ProjectSummary[]
  activeChatId: number | null
  activeView: View
  onSelectChat: (id: number) => void
  onNewChat: () => void
  onNewProjectChat: (project: ProjectSummary) => void
  onOpenProjectView: () => void
  onDeleteChat: (id: number) => void
  onSetView: (v: View) => void
  onClose: () => void
  /** 侧边栏内成功添加项目后触发（用于刷新项目列表）。 */
  onProjectImported?: () => void
}

function projectName(project: ProjectSummary) {
  return project.workspace_root.split(/[\\/]/).filter(Boolean).pop() || '项目'
}

function SectionTitle({
  label, collapsed, onToggle, trailing, count,
}: {
  label: string
  collapsed: boolean
  onToggle: () => void
  trailing?: React.ReactNode
  count?: number
}) {
  return (
    <div className="sidebar-section-title">
      <button type="button" className={`sidebar-section-title__btn ${collapsed ? 'is-collapsed' : ''}`} onClick={onToggle} aria-expanded={!collapsed}>
        <ChevronDown size={12} />
        <span>{label}</span>
        {typeof count === 'number' && count > 0 && (
          <em className="sidebar-section-count">{count}</em>
        )}
      </button>
      {trailing}
    </div>
  )
}

export function Sidebar({
  open,
  chats,
  projects,
  activeChatId,
  activeView,
  onSelectChat,
  onNewChat,
  onNewProjectChat,
  onOpenProjectView,
  onDeleteChat,
  onSetView,
  onClose,
  onProjectImported,
}: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    const chat = chats.find(item => item.id === activeChatId)
    return chat?.projectId ? new Set([chat.projectId]) : new Set()
  })
  const [collapsed, setCollapsed] = useState({ projects: false, tasks: false })
  const [addingProject, setAddingProject] = useState(false)
  const [importPath, setImportPath] = useState('')
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState('')
  const [imported, setImported] = useState('')

  // 切换到某个项目对话时，自动展开它所属的项目
  useEffect(() => {
    const chat = chats.find(item => item.id === activeChatId)
    if (chat?.projectId) {
      setExpanded(prev => (prev.has(chat.projectId!) ? prev : new Set([...prev, chat.projectId!])))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeChatId])

  const selectView = (view: View) => {
    onSetView(view)
    onClose()
  }

  const toggleProject = (projectId: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(projectId)) next.delete(projectId)
      else next.add(projectId)
      return next
    })
  }

  const openChat = (chatId: number) => {
    onSelectChat(chatId)
    selectView('chat')
  }

  const doImport = async () => {
    const path = importPath.trim()
    if (!path || importing) return
    setImporting(true); setImportError(''); setImported('')
    try {
      const result = await api.importProject(path)
      setImportPath('')
      setAddingProject(false)
      setImported(`${projectName({ project_id: result.project_id, workspace_root: result.workspace, latest_task_id: result.task_id, task_count: 1 })} 已添加，体检已启动`)
      setExpanded(prev => (prev.has(result.project_id) ? prev : new Set([...prev, result.project_id])))
      onProjectImported?.()
    } catch (err) {
      setImportError(err instanceof Error ? err.message : '添加项目失败')
    } finally {
      setImporting(false)
    }
  }

  const looseChats = chats.filter(chat => !chat.projectId)

  return (
    <>
      {open && <button className="sidebar-backdrop" onClick={onClose} aria-label="关闭导航" />}
      <aside className={`app-sidebar ${open ? 'is-open' : ''}`}>
        <header className="sidebar-brand">
          <div className="sidebar-brand__mark"><TerminalSquare size={16} /></div>
          <div>
            <strong>Explorer</strong>
            <span>Local Agent</span>
          </div>
          <button type="button" className="mobile-close-button" onClick={onClose} title="关闭导航" aria-label="关闭导航">
            <PanelLeftClose size={17} />
          </button>
        </header>

        <div className="sidebar-new">
          <button onClick={() => { onOpenProjectView(); onClose() }}>
            <FolderKanban size={15} />
            <span>打开项目</span>
          </button>
        </div>

        <SectionTitle
          label="项目栏"
          collapsed={collapsed.projects}
          onToggle={() => setCollapsed(prev => ({ ...prev, projects: !prev.projects }))}
          count={projects.length}
          trailing={!collapsed.projects && (
            <button
              type="button"
              className={`sidebar-section-add ${addingProject ? 'is-active' : ''}`}
              onClick={() => { setAddingProject(v => !v); setImportError(''); setImported('') }}
              title="添加项目"
              aria-label="添加项目"
            >
              <Plus size={13} />
            </button>
          )}
        />
        {!collapsed.projects && (
          <>
            {addingProject && (
              <div className="sidebar-project-add">
                <input
                  value={importPath}
                  onChange={event => setImportPath(event.target.value)}
                  onKeyDown={event => { if (event.key === 'Enter') void doImport() }}
                  placeholder="项目目录绝对路径，如 C:\projects\demo"
                  spellCheck={false}
                  aria-label="项目目录绝对路径"
                />
                <button type="button" disabled={!importPath.trim() || importing} onClick={() => void doImport()}>
                  <Upload size={12} />{importing ? '正在导入并体检…' : '导入并体检'}
                </button>
                {importError && <span className="is-error">{importError}</span>}
                {imported && <span>{imported}</span>}
              </div>
            )}
            <nav className="sidebar-projects" aria-label="项目栏">
              {projects.length === 0 && (
                <button type="button" className="sidebar-projects__empty" onClick={() => { onOpenProjectView(); onClose() }}>
                  还没有项目，去工作台导入
                </button>
              )}
              {projects.map(project => {
                const id = project.project_id
                const conversations = chats.filter(chat => chat.projectId === id)
                const isOpen = expanded.has(id)
                const current = activeView === 'chat' && conversations.some(chat => chat.id === activeChatId)
                return (
                  <div key={id} className={`sidebar-project ${isOpen ? 'is-open' : ''}`}>
                    <button
                      type="button"
                      className={`sidebar-project__head ${current ? 'is-active' : ''}`}
                      onClick={() => toggleProject(id)}
                      title={project.workspace_root}
                    >
                      <ChevronDown size={13} className="sidebar-project__chevron" />
                      <FolderKanban size={14} />
                      <span>{projectName(project)}</span>
                      {conversations.length > 0 && <small>{conversations.length}</small>}
                    </button>
                    {isOpen && (
                      <div className="sidebar-project__body">
                        {conversations.map(chat => (
                          <div
                            key={chat.id}
                            className={`sidebar-chat sidebar-chat--conv ${chat.id === activeChatId && activeView === 'chat' ? 'is-active' : ''}`}
                            onClick={() => openChat(chat.id)}
                          >
                            <MessageSquare size={13} />
                            <span>{chat.title}</span>
                            <button
                              type="button"
                              title="删除对话"
                              aria-label="删除对话"
                              onClick={event => { event.stopPropagation(); onDeleteChat(chat.id) }}
                            >
                              <Trash2 size={12} />
                            </button>
                          </div>
                        ))}
                        <button type="button" className="sidebar-project__new" onClick={() => { onNewProjectChat(project); onClose() }}>
                          <Plus size={13} />
                          <span>新建对话</span>
                        </button>
                      </div>
                    )}
                  </div>
                )
              })}
            </nav>
          </>
        )}

        <SectionTitle
          label="任务栏"
          collapsed={collapsed.tasks}
          onToggle={() => setCollapsed(prev => ({ ...prev, tasks: !prev.tasks }))}
          count={looseChats.length}
        />
        {!collapsed.tasks && (
          <>
            <div className="sidebar-sub-new">
              <button onClick={() => { onNewChat(); onClose() }}>
                <Plus size={15} />
                <span>新建任务</span>
              </button>
            </div>
            <nav className="sidebar-chats" aria-label="任务栏">
              {looseChats.length === 0 && <p>暂无任务</p>}
              {looseChats.map(chat => (
                <div
                  key={chat.id}
                  className={`sidebar-chat ${chat.id === activeChatId && activeView === 'chat' ? 'is-active' : ''}`}
                  onClick={() => openChat(chat.id)}
                >
                  <MessageSquare size={14} />
                  <span>{chat.title}</span>
                  <button
                    type="button"
                    title="删除任务"
                    aria-label="删除任务"
                    onClick={event => { event.stopPropagation(); onDeleteChat(chat.id) }}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
            </nav>
          </>
        )}

        <nav className="sidebar-nav" aria-label="主导航">
          <button className={activeView === 'project' ? 'is-active' : ''} onClick={() => selectView('project')}>
            <FolderKanban size={15} />
            <span>项目工作台</span>
          </button>
          <button className={activeView === 'explore' ? 'is-active' : ''} onClick={() => selectView('explore')}>
            <Compass size={15} />
            <span>探索</span>
          </button>
          <button className={activeView === 'activity' ? 'is-active' : ''} onClick={() => selectView('activity')}>
            <Activity size={15} />
            <span>运行记录</span>
          </button>
          <button className={activeView === 'settings' ? 'is-active' : ''} onClick={() => selectView('settings')}>
            <Settings size={15} />
            <span>设置</span>
          </button>
        </nav>
      </aside>
    </>
  )
}
