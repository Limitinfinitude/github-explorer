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

/** 给探索/项目等展示用的「相对时间」：天/周/月，中文单位，跨年时精确到日。 */
export function formatRelativeTime(value: string, now = Date.now()): string {
  if (!value) return ''
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value)
  const timestamp = Date.parse(hasZone ? value : `${value.replace(' ', 'T')}Z`)
  if (Number.isNaN(timestamp)) return ''
  const days = Math.floor(Math.max(0, now - timestamp) / 86_400_000)
  if (days === 0) return '今天'
  if (days === 1) return '昨天'
  if (days < 7) return `${days} 天前`
  if (days < 30) return `${Math.floor(days / 7)} 周前`
  if (days < 365) return `${Math.floor(days / 30)} 个月前`
  return new Date(timestamp).toLocaleDateString('zh-CN')
}
