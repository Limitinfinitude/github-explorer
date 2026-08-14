import type { Chat, Message } from '../types'

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

export function ensureSessionChat(
  chats: Chat[],
  id: number,
  now: number,
  sessionId: string,
  title: string,
  message?: Message,
) {
  const existing = chats.find(chat => chat.sessionId === sessionId)
  if (existing) {
    if (!message) return { chats, activeChatId: existing.id, chat: existing }
    const updated = { ...existing, messages: [...existing.messages, message] }
    return {
      chats: chats.map(chat => chat.id === existing.id ? updated : chat),
      activeChatId: existing.id,
      chat: updated,
    }
  }
  const chat: Chat = { id, title, sessionId, messages: message ? [message] : [], created: now }
  return { chats: [...chats, chat], activeChatId: id, chat }
}
