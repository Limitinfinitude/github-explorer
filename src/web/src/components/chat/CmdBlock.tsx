import React, { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { CmdBlockData } from '../../types'

/** 执行块：命令一行摘要 + 状态；输出默认折叠（点击展开），避免长输出刷屏。 */
export function CmdBlock({ block }: { block: CmdBlockData }) {
  const [open, setOpen] = useState(false)
  const borderColor = !block.done
    ? 'border-theme'
    : block.success
    ? 'border-ok'
    : 'border-err'
  const hasOutput = block.lines.length > 0

  return (
    <div className={`my-2 rounded-lg border overflow-hidden text-xs font-mono ${borderColor}`}>
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-1.5 bg-surface-2 text-left"
        onClick={() => hasOutput && setOpen(v => !v)}
        aria-expanded={hasOutput ? open : undefined}
      >
        {block.risk === 'high' ? (
          <span className="text-err font-semibold text-[10px]">⚠ 高风险: {block.reason}</span>
        ) : (
          <span className="text-ok text-[10px]">✓ 安全</span>
        )}
        <code className="flex-1 truncate text-fg">{block.command}</code>
        {hasOutput && (
          <span className="text-muted text-[10px]">{open ? '收起输出' : `${block.lines.length} 行输出`}</span>
        )}
        {block.done && (
          <span className={block.success ? 'text-ok' : 'text-err'}>
            {block.success ? '✅' : '❌'}
          </span>
        )}
        {hasOutput && <ChevronDown size={12} className={`text-muted ${open ? 'rotate-180' : ''}`} />}
      </button>
      {hasOutput && open && (
        <pre className="p-2 max-h-48 overflow-y-auto bg-code text-code text-[11px] leading-5 whitespace-pre-wrap break-all">
          {block.lines.join('\n')}
        </pre>
      )}
    </div>
  )
}
