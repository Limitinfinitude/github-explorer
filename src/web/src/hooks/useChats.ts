import { useState, useCallback } from 'react'
import type { Chat, Message } from '../types'
import { api } from '../lib/api'
import { historyToMessages } from '../lib/chatHistory'
import { appendNewChat, appendProjectChat, ensureSessionChat } from '../lib/chatState'
import type { ChatMeta } from '../lib/chatState'

const STORAGE_KEY = 'explorer_chats'

function load(): Chat[] {
  try {
    const s = localStorage.getItem(STORAGE_KEY)
    if (s) {
      const chats: Chat[] = JSON.parse(s)
      chats.forEach(c => {
        if (!c.created) c.created = Date.now()
        if (!c.sessionId || c.sessionId === 'default') {
          c.sessionId = `session-${c.id}-${Date.now()}`
        }
        // 旧版本数据字段缺失/类型不同，深度规范化避免渲染崩溃
        c.messages = Array.isArray(c.messages) ? c.messages : []
        c.messages.forEach(m => {
          if (typeof m.thinking === 'string') m.thinking = [m.thinking]
          if (!Array.isArray(m.thinking)) m.thinking = undefined
          if (!Array.isArray(m.narrations)) m.narrations = undefined
          if (!Array.isArray(m.steps)) m.steps = undefined
          if (!Array.isArray(m.cmdBlocks)) m.cmdBlocks = undefined
          if (m.agentRun) {
            if (!Array.isArray(m.agentRun.fileChanges)) m.agentRun.fileChanges = []
            if (!Array.isArray(m.agentRun.processes)) m.agentRun.processes = []
            if (!Array.isArray(m.agentRun.acceptance)) m.agentRun.acceptance = []
            if (!Array.isArray(m.agentRun.plan)) m.agentRun.plan = []
          }
        })
      })
      return chats
    }
  } catch { /* ignore */ }
  return []
}

function save(chats: Chat[]) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(chats)) } catch { /* ignore */ }
}

let nextId = 0

export function useChats() {
  const [chats, setChats] = useState<Chat[]>(() => {
    const loaded = load()
    nextId = loaded.length ? Math.max(...loaded.map(c => c.id)) + 1 : 1
    return loaded
  })
  const [activeChatId, setActiveChatId] = useState<number | null>(() => {
    const loaded = load()
    return loaded.length ? loaded[loaded.length - 1].id : null
  })

  const activeChat = chats.find(c => c.id === activeChatId) ?? null

  const newChat = useCallback(() => {
    const id = nextId++
    const now = Date.now()
    const result = appendNewChat(chats, id, now)
    setChats(prev => {
      const next = appendNewChat(prev, id, now).chats
      save(next)
      return next
    })
    setActiveChatId(result.activeChatId)
    return result.chat
  }, [chats])

  const openSession = useCallback((sessionId: string, title: string, userMessage?: string, meta?: ChatMeta) => {
    const id = nextId++
    const now = Date.now()
    const message: Message | undefined = userMessage ? {
      id: `msg-${now}`,
      role: 'user',
      content: userMessage,
      time: new Date(now).toISOString(),
    } : undefined
    const result = ensureSessionChat(chats, id, now, sessionId, title, message, meta)
    if (result.chat.id !== id) nextId -= 1
    setChats(prev => {
      const next = ensureSessionChat(prev, id, now, sessionId, title, message, meta).chats
      save(next)
      return next
    })
    setActiveChatId(result.activeChatId)
    return result.chat
  }, [chats])

  /** 在指定项目下新建一个对话（新会话，绑定项目工作区）。 */
  const newProjectChat = useCallback((projectId: string, workspace: string, title?: string) => {
    const id = nextId++
    const now = Date.now()
    const result = appendProjectChat(chats, id, now, title || '新对话', projectId, workspace)
    setChats(prev => {
      const next = appendProjectChat(prev, id, now, title || '新对话', projectId, workspace).chats
      save(next)
      return next
    })
    setActiveChatId(result.activeChatId)
    return result.chat
  }, [chats])

  /** 归属某项目的对话。 */
  const projectChats = useCallback((projectId: string) => chats.filter(chat => chat.projectId === projectId), [chats])

  /** 未归属项目的普通任务/对话。 */
  const looseChats = useCallback(() => chats.filter(chat => !chat.projectId), [chats])

  const deleteChat = useCallback((id: number) => {
    setChats(prev => {
      const next = prev.filter(c => c.id !== id)
      save(next)
      return next
    })
    setActiveChatId(prev => (prev === id ? null : prev))
  }, [])

  const pushMessage = useCallback((chatId: number, msg: Message) => {
    const target = chats.find(c => c.id === chatId)
    if (target?.sessionId) {
      void api.saveChatMessage(target.sessionId, msg).catch(() => {})
    }
    setChats(prev => {
      const next = prev.map(c =>
        c.id === chatId ? { ...c, messages: [...c.messages, msg], title: c.messages.length === 0 ? msg.content.slice(0, 20) : c.title } : c
      )
      save(next)
      return next
    })
  }, [chats])

  const updateLastMessage = useCallback((chatId: number, updater: (m: Message) => Message) => {
    setChats(prev => {
      const next = prev.map(c => {
        if (c.id !== chatId || !c.messages.length) return c
        const msgs = [...c.messages]
        msgs[msgs.length - 1] = updater(msgs[msgs.length - 1])
        return { ...c, messages: msgs }
      })
      save(next)
      return next
    })
  }, [])

  const hydrateChat = useCallback(async (chatId: number, sessionId: string) => {
    // 优先从后端聊天消息恢复（含思考/工具过程），本地缓存丢失时仍有完整副本
    let messages: Message[] = []
    try {
      const stored = await api.getChatMessages(sessionId)
      messages = stored.map((m, index) => ({
        ...m,
        id: m.id ?? `msg-hydrated-${Date.now()}-${index}`,
        time: m.time || new Date().toISOString(),
      }))
    } catch { /* fall through to agent history */ }
    if (messages.length === 0) {
      const history = await api.getHistory(sessionId)
      messages = historyToMessages(sessionId, history)
    }
    if (messages.length === 0) return
    setChats(prev => {
      const next = prev.map(chat => {
        if (chat.id !== chatId || chat.sessionId !== sessionId) return chat
        const title = chat.title === '新对话'
          ? (messages.find(message => message.role === 'user')?.content.slice(0, 20) || chat.title)
          : chat.title
        return { ...chat, messages, title }
      })
      save(next)
      return next
    })
  }, [])

  return { chats, activeChat, activeChatId, setActiveChatId, newChat, newProjectChat, projectChats, looseChats, openSession, deleteChat, pushMessage, updateLastMessage, hydrateChat }
}
