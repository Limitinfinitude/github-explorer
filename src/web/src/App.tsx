import React, { useState, useEffect, useCallback } from 'react'
import { Menu } from 'lucide-react'
import { Sidebar } from './components/layout/Sidebar'
import { ChatPanel } from './components/chat/ChatPanel'
import { ExploreView } from './components/explore/ExploreView'
import { ProjectWorkspaceView } from './components/project/ProjectWorkspaceView'
import { SettingsView } from './components/settings/SettingsView'
import { ActivityView } from './components/activity/ActivityView'
import { useChats } from './hooks/useChats'
import { api } from './lib/api'
import type { Message, Model, ProjectSummary, View } from './types'

const DEFAULT_MODELS: Model[] = [
  { id: 'claude-sonnet-5', name: 'Claude Sonnet 5', icon: 'C', color: '#cc785c', tags: ['最新', '推理强'] },
]

export default function App() {
  const { chats, activeChat, activeChatId, setActiveChatId, newChat, newProjectChat, openSession, deleteChat, pushMessage, hydrateChat } = useChats()
  const [models, setModels] = useState<Model[]>(DEFAULT_MODELS)
  const [currentModel, setCurrentModel] = useState('claude-sonnet-5')
  const [agentMode] = useState(true)
  const [activeView, setActiveView] = useState<View>('chat')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try { return localStorage.getItem('explorer_theme') === 'light' ? 'light' : 'dark' } catch { return 'dark' }
  })

  useEffect(() => {
    try {
      if (theme === 'light') document.documentElement.setAttribute('data-theme', 'light')
      else document.documentElement.removeAttribute('data-theme')
      localStorage.setItem('explorer_theme', theme)
    } catch { /* 主题持久化失败不影响使用 */ }
  }, [theme])

  const refreshModels = useCallback(async () => {
    const data = await api.getModels()
    if (data.models?.length) {
      setModels(data.models)
      setCurrentModel(data.current_model ?? data.active_model ?? data.models[0].id)
    }
    return data.models ?? []
  }, [])

  const refreshProjects = useCallback(async () => {
    try {
      setProjects(await api.getProjects())
    } catch { /* 项目栏读取失败时保留上次列表 */ }
  }, [])

  useEffect(() => {
    refreshModels().catch(() => {})
    void refreshProjects()
  }, [refreshModels, refreshProjects])

  // 进入工作台或切回对话视图时同步项目栏
  useEffect(() => {
    if (activeView === 'project' || activeView === 'chat') void refreshProjects()
  }, [activeView, refreshProjects])

  // Auto-create first chat or select existing
  useEffect(() => {
    if (chats.length === 0) {
      newChat()
    } else if (activeChatId === null) {
      setActiveChatId(chats[chats.length - 1].id)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (activeView !== 'chat' || !activeChat) return
    void hydrateChat(activeChat.id, activeChat.sessionId).catch(() => {
      // Keep the local cache when the canonical history endpoint is temporarily unavailable.
    })
  }, [activeView, activeChat?.id, activeChat?.sessionId, hydrateChat])

  const handleNewChat = useCallback(() => {
    newChat()
    setActiveView('chat')
  }, [newChat])

  const handleOpenProjectView = useCallback(() => {
    setActiveView('project')
  }, [])

  const handleNewProjectChat = useCallback((project: ProjectSummary) => {
    const name = project.workspace_root.split(/[\\/]/).filter(Boolean).pop() || '项目'
    newProjectChat(project.project_id, project.workspace_root, `${name} 项目`)
    setActiveView('chat')
  }, [newProjectChat])

  const handleOpenProjectConversation = useCallback((
    sessionId: string,
    title: string,
    userMessage?: string,
    project?: { projectId: string; workspace: string },
  ) => {
    openSession(sessionId, title, userMessage, project)
    setActiveView('chat')
  }, [openSession])

  const handleSelectModel = useCallback((id: string) => {
    setCurrentModel(id)
    api.selectModel(id).catch(() => {})
  }, [])

  const handleModelCreated = useCallback(async (id: string) => {
    await refreshModels()
    handleSelectModel(id)
  }, [handleSelectModel, refreshModels])

  const handlePushMessage = useCallback((msg: Message) => {
    if (activeChatId !== null) pushMessage(activeChatId, msg)
  }, [activeChatId, pushMessage])

  return (
    <div className="flex h-screen w-full bg-canvas text-fg">
      <Sidebar
        open={sidebarOpen}
        chats={chats}
        projects={projects}
        activeChatId={activeChatId}
        activeView={activeView}
        onSelectChat={setActiveChatId}
        onNewChat={handleNewChat}
        onNewProjectChat={handleNewProjectChat}
        onOpenProjectView={handleOpenProjectView}
        onDeleteChat={deleteChat}
        onProjectImported={() => void refreshProjects()}
        onSetView={setActiveView}
        onClose={() => setSidebarOpen(false)}
      />

      <main className="relative flex-1 flex flex-col min-w-0 bg-canvas">
        {activeView !== 'chat' && (
          <button type="button" className="mobile-menu-button view-mobile-menu" onClick={() => setSidebarOpen(true)} title="打开导航" aria-label="打开导航">
            <Menu size={17} />
          </button>
        )}
        {activeView === 'chat' && activeChat ? (
          <ChatPanel
            key={activeChat.id}
            chat={activeChat}
            models={models}
            currentModel={currentModel}
            agentMode={agentMode}
            onPushMessage={handlePushMessage}
            onSelectModel={handleSelectModel}
            onOpenMenu={() => setSidebarOpen(true)}
          />
        ) : activeView === 'project' ? (
          <ProjectWorkspaceView onOpenProjectConversation={handleOpenProjectConversation} />
        ) : activeView === 'explore' ? (
          <ExploreView />
        ) : activeView === 'activity' ? (
          <ActivityView />
        ) : activeView === 'settings' ? (
          <SettingsView
            models={models}
            currentModel={currentModel}
            theme={theme}
            onThemeChange={setTheme}
            onSelectModel={handleSelectModel}
            onModelCreated={handleModelCreated}
          />
        ) : (
          <div className="flex-1 flex items-center justify-center text-faint text-sm">
            请从左侧选择对话或新建
          </div>
        )}
      </main>
    </div>
  )
}
