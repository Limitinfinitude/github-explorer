import React from 'react'
import type { CmdBlockData } from '../../types'

export function CmdBlock({ block }: { block: CmdBlockData }) {
  const borderColor = !block.done
    ? 'border-theme'
    : block.success
    ? 'border-ok'
    : 'border-err'

  return (
    <div className={`my-2 rounded-lg border overflow-hidden text-xs font-mono ${borderColor}`}>
      <div className="flex items-center gap-2 px-3 py-1.5 bg-surface-2">
        {block.risk === 'high' ? (
          <span className="text-err font-semibold text-[10px]">⚠ 高风险: {block.reason}</span>
        ) : (
          <span className="text-ok text-[10px]">✓ 安全</span>
        )}
        <code className="flex-1 truncate text-fg">{block.command}</code>
        {block.done && (
          <span className={block.success ? 'text-ok' : 'text-err'}>
            {block.success ? '✅' : '❌'}
          </span>
        )}
      </div>
      {block.lines.length > 0 && (
        <pre className="p-2 max-h-48 overflow-y-auto bg-code text-code text-[11px] leading-5 whitespace-pre-wrap break-all">
          {block.lines.join('\n')}
        </pre>
      )}
    </div>
  )
}
