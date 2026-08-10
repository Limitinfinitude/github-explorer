import React from 'react'
import {
  Activity, Compass, MessageSquare, PanelLeftClose, Plus, Settings, TerminalSquare, Trash2,
} from 'lucide-react'
import type { Chat } from '../../types'

interface Props {
  open: boolean
  chats: Chat[]
  activeChatId: number | null
  activeView: 'chat' | 'explore' | 'activity' | 'settings'
  onSelectChat: (id: number) => void
  onNewChat: () => void
  onDeleteChat: (id: number) => void
  onSetView: (v: 'chat' | 'explore' | 'activity' | 'settings') => void
  onClose: () => void
}

export function Sidebar({
  open,
  chats,
  activeChatId,
  activeView,
  onSelectChat,
  onNewChat,
  onDeleteChat,
  onSetView,
  onClose,
}: Props) {
  const selectView = (view: 'chat' | 'explore' | 'activity' | 'settings') => {
    onSetView(view)
    onClose()
  }

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
          <button onClick={() => { onNewChat(); onClose() }}>
            <Plus size={15} />
            <span>新建任务</span>
          </button>
        </div>

        <div className="sidebar-section-title">任务记录</div>
        <nav className="sidebar-chats" aria-label="任务记录">
          {chats.length === 0 && <p>暂无任务</p>}
          {chats.map(chat => (
            <div
              key={chat.id}
              className={`sidebar-chat ${chat.id === activeChatId && activeView === 'chat' ? 'is-active' : ''}`}
              onClick={() => { onSelectChat(chat.id); selectView('chat') }}
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

        <nav className="sidebar-nav" aria-label="主导航">
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
