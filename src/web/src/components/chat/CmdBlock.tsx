import React from 'react'
import type { CmdBlockData } from '../../types'

export function CmdBlock({ block }: { block: CmdBlockData }) {
  const borderColor = !block.done
    ? 'border-zinc-700'
    : block.success
    ? 'border-green-700'
    : 'border-red-700'

  return (
    <div className={`my-2 rounded-lg border overflow-hidden text-xs font-mono ${borderColor}`}>
      <div className="flex items-center gap-2 px-3 py-1.5 bg-zinc-800/60">
        {block.risk === 'high' ? (
          <span className="text-red-400 font-semibold text-[10px]">⚠ 高风险: {block.reason}</span>
        ) : (
          <span className="text-green-500 text-[10px]">✓ 安全</span>
        )}
        <code className="flex-1 truncate text-zinc-200">{block.command}</code>
        {block.done && (
          <span className={block.success ? 'text-green-400' : 'text-red-400'}>
            {block.success ? '✅' : '❌'}
          </span>
        )}
      </div>
      {block.lines.length > 0 && (
        <pre className="p-2 max-h-48 overflow-y-auto bg-zinc-950 text-zinc-300 text-[11px] leading-5 whitespace-pre-wrap break-all">
          {block.lines.join('\n')}
        </pre>
      )}
    </div>
  )
}
