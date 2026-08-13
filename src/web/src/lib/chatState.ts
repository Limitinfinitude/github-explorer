import type { Chat } from '../types'

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
