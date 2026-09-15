import React, { useEffect, useRef, useState } from 'react'
import { Check, Copy, Link2, X } from 'lucide-react'

interface Props {
  url?: string
  title: string
  /** 生成失败时的错误信息（与 url 互斥） */
  error?: string
  onClose: () => void
}

/** 分享结果弹窗：展示公开链接并一键复制（复用确认弹窗的遮罩/盒子样式）。 */
export function ShareDialog({ url, title, error, onClose }: Props) {
  const [copied, setCopied] = useState(false)
  const linkRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (error) return
    linkRef.current?.focus()
    linkRef.current?.select()
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') { e.preventDefault(); onClose() }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, error])

  async function copy() {
    if (!url) return
    try {
      await navigator.clipboard.writeText(url)
    } catch {
      // 剪贴板 API 不可用（非 https/权限）时退回选中 + execCommand
      linkRef.current?.select()
      document.execCommand('copy')
    }
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1800)
  }

  return (
    <div className="confirm-overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="confirm-box share-box" role="dialog" aria-modal="true" aria-label="分享会话">
        <button type="button" className="share-box__close" onClick={onClose} aria-label="关闭"><X size={14} /></button>
        <div className={`confirm-box__icon share-box__icon ${error ? 'is-error' : ''}`}><Link2 size={17} /></div>
        <h3 className="confirm-box__title">{error ? '分享失败' : '分享链接已生成'}</h3>
        <p className="confirm-box__message">
          {error || `「${title}」已转成静态网页，任何拿到链接的人都能直接打开查看（含思考过程、工具调用与文件改动）。`}
        </p>
        {!error && (
          <div className="share-box__row">
            <input ref={linkRef} className="share-box__link" value={url} readOnly onFocus={e => e.currentTarget.select()} />
            <button type="button" className={`share-box__copy ${copied ? 'is-copied' : ''}`} onClick={copy}>
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? '已复制' : '复制'}
            </button>
          </div>
        )}
        <div className="confirm-box__actions">
          <button type="button" className="confirm-box__cancel" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}
