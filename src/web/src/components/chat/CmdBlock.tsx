import React, { useState } from 'react'
import { Globe, Search, TerminalSquare } from 'lucide-react'
import type { CmdBlockData } from '../../types'
import { useStopBubbleRef } from '../../lib/stopBubble'

/** 执行块：与 WorkChain 的 tool-card 同构（图标+名称+命令+状态徽章+结果预览），
 * 样式复用同一套，保证对话里工具链视觉统一。 */
export function CmdBlock({ block }: { block: CmdBlockData }) {
  const [open, setOpen] = useState(false)
  const stopRef = useStopBubbleRef<HTMLDetailsElement>()
  const command = block.command.trim()
  const isCurl = /\b(curl|invoke-webrequest|invoke-restmethod|wget)\b/i.test(command)
  const isSearch = /\b(findstr|select-string|rg|grep)\b/i.test(command)
  const Icon = isCurl ? Globe : isSearch ? Search : TerminalSquare
  const label = isCurl ? '网络请求' : isSearch ? '搜索' : '执行命令'
  const hasOutput = block.lines.length > 0
  const preview = hasOutput
    ? (block.lines.find(line => line.trim()) || '').trim().slice(0, 64)
    : ''
  const stateClass = !block.done ? 'is-running' : block.success ? 'is-done' : 'is-failed'
  const stateText = !block.done ? '进行中' : block.success ? '完成' : '失败'

  return (
    <details
      className={`tool-card ${stateClass}`}
      ref={stopRef}
      open={open}
      onToggle={e => {
        // 只响应自身 toggle（React 合成 toggle 冒泡会让嵌套 details 互相干扰）
        if (e.target === e.currentTarget) setOpen((e.target as HTMLDetailsElement).open)
      }}
    >
      <summary title={block.risk === 'high' ? `高风险：${block.reason}` : command}>
        <Icon size={14} />
        <span className="tool-card__name">{label}{block.risk === 'high' ? ' ⚠' : ''}</span>
        <span className="tool-card__summary">{!block.done ? '进行中…' : preview || stateText}</span>
        <span className="tool-card__status">{stateText}</span>
      </summary>
      {hasOutput && (
        <div className="tool-card__body">
          <pre className="tool-card__output">{block.lines.join('\n')}</pre>
        </div>
      )}
    </details>
  )
}
