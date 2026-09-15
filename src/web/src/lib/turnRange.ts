import type { Message } from '../types'

/** 一轮对话的范围：一条用户消息 + 紧随其后（到下一个用户消息之前）的所有回复。
 *  删除任意一条消息时按轮删除，避免留下"孤立的提问/回复"。 */
export interface TurnRange {
  /** 起始下标（含） */
  start: number
  /** 结束下标（不含） */
  end: number
}

export function turnRange(messages: Message[], messageId: string): TurnRange | null {
  const index = messages.findIndex(m => m.id === messageId)
  if (index === -1) return null
  // 向前找本轮的用户消息
  let start = index
  while (start > 0 && messages[start].role !== 'user') start -= 1
  if (messages[start].role !== 'user') {
    // 前面没有用户消息（如开场系统提示）→ 只删除这一条
    return { start: index, end: index + 1 }
  }
  // 向后到下一个用户消息之前
  let end = start + 1
  while (end < messages.length && messages[end].role !== 'user') end += 1
  return { start, end }
}

/** 在消息列表中删除一条消息所在的整轮，返回新数组（找不到时原样返回）。 */
export function removeTurn(messages: Message[], messageId: string): Message[] {
  const range = turnRange(messages, messageId)
  if (!range) return messages
  return [...messages.slice(0, range.start), ...messages.slice(range.end)]
}
