import type { Message } from '../types'

export interface CanonicalHistoryRow {
  role: string
  content: string
  timestamp?: string | null
}

export function historyToMessages(sessionId: string, rows: CanonicalHistoryRow[]): Message[] {
  let messageIndex = 0
  return rows.flatMap(row => {
    if ((row.role !== 'user' && row.role !== 'assistant') || typeof row.content !== 'string') return []
    const index = messageIndex++
    return [{
      id: `${sessionId}:${index}`,
      role: row.role,
      content: row.content,
      time: row.timestamp || new Date().toISOString(),
    }]
  })
}
