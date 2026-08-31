import React, { useMemo, useState } from 'react'
import { Check, ChevronDown, Copy } from 'lucide-react'

// 超过该字符数的 JSON 默认折叠，点「展开全部」看完整内容
const COLLAPSE_CHARS = 800

function normalize(value: unknown): string {
  if (value === undefined) return ''
  if (value === null) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'object' && Object.keys(value as object).length === 0) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

/** 折叠式 pretty JSON：键着色、长内容截断可展开、一键复制。替代裸 <pre>{JSON.stringify}。 */
export function JsonView({ value, label }: { value: unknown; label?: string }) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  const text = useMemo(() => normalize(value), [value])

  if (!text) return null
  const longText = text.length > COLLAPSE_CHARS
  const shown = longText && !expanded ? `${text.slice(0, COLLAPSE_CHARS)}\n…（剩余 ${(text.length - COLLAPSE_CHARS).toLocaleString()} 字符）` : text

  async function copy() {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="jsonview">
      <div className="jsonview__bar">
        {label && <span className="jsonview__label">{label}</span>}
        <span className="jsonview__size">{text.length.toLocaleString()} 字符</span>
        <button type="button" className="jsonview__btn" onClick={() => void copy()} title="复制" aria-label="复制内容">
          {copied ? <Check size={11} /> : <Copy size={11} />}
        </button>
        {longText && (
          <button type="button" className="jsonview__btn" onClick={() => setExpanded(v => !v)} aria-expanded={expanded}>
            <ChevronDown size={11} className={expanded ? 'is-open' : ''} />
            <span>{expanded ? '收起' : '展开全部'}</span>
          </button>
        )}
      </div>
      <pre className="jsonview__pre">
        {shown.split('\n').map((line, index) => {
          const match = line.match(/^(\s*)"([^"]+)":\s?(.*)$/)
          return match ? (
            <div key={index} className="jsonview__line">
              <span>{match[1]}</span>
              <span className="jsonview__key">"{match[2]}"</span>
              <span className="jsonview__punct">: </span>
              <span className="jsonview__val">{match[3]}</span>
            </div>
          ) : (
            <div key={index} className="jsonview__line">{line || ' '}</div>
          )
        })}
      </pre>
    </div>
  )
}
