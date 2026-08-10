import { useState, useCallback } from 'react'
import type { Chat, Message } from '../types'

const STORAGE_KEY = 'explorer_chats'

function load(): Chat[] {
  try {
    const s = localStorage.getItem(STORAGE_KEY)
    if (s) {
      const chats: Chat[] = JSON.parse(s)
      chats.forEach(c => { if (!c.created) c.created = Date.now() })
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
    const chat: Chat = {
      id,
      title: '新对话',
      sessionId: `session-${id}-${Date.now()}`,
      messages: [],
      created: Date.now(),
    }
    setChats(prev => {
      const next = [...prev, chat]
      save(next)
      return next
    })
    setActiveChatId(id)
    return chat
  }, [])

  const deleteChat = useCallback((id: number) => {
    setChats(prev => {
      const next = prev.filter(c => c.id !== id)
      save(next)
      return next
    })
    setActiveChatId(prev => (prev === id ? null : prev))
  }, [])

  const pushMessage = useCallback((chatId: number, msg: Message) => {
    setChats(prev => {
      const next = prev.map(c =>
        c.id === chatId ? { ...c, messages: [...c.messages, msg], title: c.messages.length === 0 ? msg.content.slice(0, 20) : c.title } : c
      )
      save(next)
      return next
    })
  }, [])

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

  return { chats, activeChat, activeChatId, setActiveChatId, newChat, deleteChat, pushMessage, updateLastMessage }
}
