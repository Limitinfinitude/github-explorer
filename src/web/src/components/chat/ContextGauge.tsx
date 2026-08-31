import { useState } from 'react'
import type { ContextUsage } from '../../types'

interface Props {
  usage: ContextUsage | null
}

const fmt = (n: number) => (n >= 10000 ? `${(n / 10000).toFixed(1)}万` : String(n))

/** 上下文容量指示器：圆环进度 + 悬停分类明细（消息/工具/系统提示词 + 缓存命中率）。 */
export function ContextGauge({ usage }: Props) {
  const [open, setOpen] = useState(false)
  if (!usage || !usage.window) return null

  const pct = Math.min(100, (usage.used / usage.window) * 100)
  const deg = Math.min(360, (pct / 100) * 360)
  const near = pct >= 75 // 与后端软压缩阈值一致
  const rows: { label: string; tokens: number }[] = [
    { label: '消息', tokens: usage.breakdown.history },
    { label: 'MCP 工具', tokens: usage.breakdown.tools_mcp },
    { label: '系统工具', tokens: usage.breakdown.tools_system },
    { label: '系统提示词', tokens: usage.breakdown.system_prompt },
    { label: '其他', tokens: usage.breakdown.other || 0 },
  ]
  const totalBreakdown = rows.reduce((s, r) => s + r.tokens, 0) || 1

  return (
    <div
      className="ctx-gauge"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onClick={() => setOpen(v => !v)}
      title="上下文容量"
    >
      <div
        className={`ctx-gauge__ring ${near ? 'is-near' : ''}`}
        style={{ background: `conic-gradient(var(--accent) ${deg}deg, var(--border-soft, #333) ${deg}deg)` }}
      >
        <div className="ctx-gauge__hole">
          <span className="ctx-gauge__pct">{pct.toFixed(0)}%</span>
        </div>
      </div>
      {open && (
        <div className="ctx-gauge__panel">
          <div className="ctx-gauge__head">
            <strong>上下文容量</strong>
            <span>{fmt(usage.used)}/{fmt(usage.window)}（{pct.toFixed(1)}%）</span>
          </div>
          <div className="ctx-gauge__bar">
            <div className="ctx-gauge__bar-fill" style={{ width: `${pct}%` }} />
          </div>
          <ul className="ctx-gauge__rows">
            {rows.map(r => (
              <li key={r.label}>
                <span className="ctx-gauge__dot" />
                <span className="ctx-gauge__label">{r.label}</span>
                <span className="ctx-gauge__share">{((r.tokens / totalBreakdown) * 100).toFixed(1)}%</span>
              </li>
            ))}
          </ul>
          <div className="ctx-gauge__foot">
            <span>平均缓存命中率</span>
            <span>{usage.cache_hit_rate != null ? `${(usage.cache_hit_rate * 100).toFixed(1)}%` : '—'}</span>
          </div>
          {usage.compactions > 0 && (
            <div className="ctx-gauge__foot">
              <span>已压缩次数</span>
              <span>{usage.compactions}</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
