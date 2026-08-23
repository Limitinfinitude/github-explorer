import type { Chat, Message } from '../types'

/** 旧版本本地缓存可能缺少 sessionId；空值或 default 会让多个对话共享同一份历史。 */
export function ensureChatSessionId(chat: Chat): Chat {
  if (chat.sessionId && chat.sessionId !== 'default') return chat
  return { ...chat, sessionId: `session-${chat.id}-${Date.now()}` }
}

export function appendNewChat(chats: Chat[], id: number, now: number) {
  const chat: Chat = {
    id,
    title: '新对话',
    sessionId: `session-${id}-${now}`,
    messages: [],
    created: now,
  }
  return { chats: [...chats, chat], activeChatId: id, chat }
}

export interface ChatMeta {
  projectId?: string
  workspace?: string
}

export function appendProjectChat(
  chats: Chat[],
  id: number,
  now: number,
  title: string,
  projectId: string,
  workspace: string,
) {
  const chat: Chat = {
    id,
    title,
    sessionId: `project-conv-${id}-${now}`,
    messages: [],
    created: now,
    projectId,
    workspace,
  }
  return { chats: [...chats, chat], activeChatId: id, chat }
}

export function ensureSessionChat(
  chats: Chat[],
  id: number,
  now: number,
  sessionId: string,
  title: string,
  message?: Message,
  meta?: ChatMeta,
) {
  const existing = chats.find(chat => chat.sessionId === sessionId)
  if (existing) {
    const merged = { ...existing, ...(meta || {}) }
    if (!message) return { chats, activeChatId: existing.id, chat: merged }
    const updated = { ...merged, messages: [...merged.messages, message] }
    return {
      chats: chats.map(chat => chat.id === existing.id ? updated : chat),
      activeChatId: existing.id,
      chat: updated,
    }
  }
  const chat: Chat = {
    id,
    title,
    sessionId,
    messages: message ? [message] : [],
    created: now,
    ...(meta || {}),
  }
  return { chats: [...chats, chat], activeChatId: id, chat }
}
