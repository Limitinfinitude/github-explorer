export function formatLocalTimestamp(value: string, timeZone?: string) {
  if (!value) return ''
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value)
  const normalized = hasZone ? value : `${value.replace(' ', 'T')}Z`
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return value
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date)
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find(item => item.type === type)?.value ?? ''
  return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}:${part('second')}`
}

export function relativeMessageTime(value: string, now = Date.now()): string {
  if (!value) return ''
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value)
  const timestamp = Date.parse(hasZone ? value : `${value.replace(' ', 'T')}Z`)
  if (Number.isNaN(timestamp)) return ''
  const diff = Math.max(0, Math.round((now - timestamp) / 1000))
  if (diff < 10) return '刚刚'
  if (diff < 60) return `${diff}秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`
  return new Date(timestamp).toLocaleTimeString('zh-CN', {
    hour: '2-digit', minute: '2-digit',
  })
}
