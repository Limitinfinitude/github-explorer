import React from 'react'

/** 事实摘要条（模型轮次 / tokens / 延迟 / 终态原因），复用 activity-stage-summary 样式。 */
export function FactChip({ label, detail, tone }: { label: string; detail: string; tone?: 'failed' }) {
  return (
    <div className={`activity-stage-summary__item activity-stage-summary__item--facts ${tone === 'failed' ? 'is-failed' : ''}`}>
      <strong>{label}</strong>
      <span>{detail}</span>
    </div>
  )
}

/** 阶段摘要条（体检/实施/测试/运行 + 次数·耗时·失败）。 */
export function StageChip({ label, detail, failed }: { label: string; detail: string; failed?: boolean }) {
  return (
    <div className={`activity-stage-summary__item ${failed ? 'is-failed' : ''}`}>
      <strong>{label}</strong>
      <span>{detail}</span>
    </div>
  )
}

/**
 * 阶段耗时比例条：每段宽度=该阶段占任务总耗时的比例，绿=无失败、橙=有失败。
 * 全部阶段都亚秒时均分宽度；段宽不足 14% 时不显示文字（tooltip 承担详情）。
 */
export function StageBar({ segments }: { segments: Array<{ label: string; durationMs: number; failed: boolean; title: string }> }) {
  if (segments.length === 0) return null
  const total = segments.reduce((sum, seg) => sum + seg.durationMs, 0)
  const proportional = total > 0
  return (
    <div className="stage-bar" role="img" aria-label="阶段耗时占比">
      {segments.map(seg => {
        const pct = proportional ? (seg.durationMs / total) * 100 : 100 / segments.length
        return (
          <div
            key={seg.label}
            className={`stage-bar__seg ${seg.failed ? 'stage-bar__seg--warn' : 'stage-bar__seg--ok'}`}
            style={{ width: `${pct}%` }}
            title={seg.title}
          >
            {pct >= 14 && <span>{seg.label}</span>}
          </div>
        )
      })}
    </div>
  )
}
