import React, { useEffect, useRef, useState, useCallback } from 'react'
import { AlertTriangle, LoaderCircle } from 'lucide-react'

export interface ConfirmOptions {
  title: string
  message: string
  /** 确认按钮文案，默认「确定」 */
  confirmText?: string
  /** 取消按钮文案，默认「取消」 */
  cancelText?: string
  /** 危险操作（删除/放开权限）时确认按钮为红色 */
  danger?: boolean
}

interface PendingConfirm extends ConfirmOptions {
  resolve: (ok: boolean) => void
}

/** 应用内确认弹层——替代 window.confirm（浏览器原生弹窗不可定制且观感差）。
 *
 * 用法：
 *   const confirm = useConfirm()
 *   if (!(await confirm({ title: '删除模型', message: '…', danger: true }))) return
 */
export function useConfirm() {
  const [pending, setPending] = useState<PendingConfirm | null>(null)
  const pendingRef = useRef<PendingConfirm | null>(null)

  const confirm = useCallback((options: ConfirmOptions): Promise<boolean> => {
    return new Promise(resolve => {
      const item = { ...options, resolve }
      pendingRef.current = item
      setPending(item)
    })
  }, [])

  const settle = useCallback((ok: boolean) => {
    const item = pendingRef.current
    pendingRef.current = null
    setPending(null)
    item?.resolve(ok)
  }, [])

  const dialog = pending ? (
    <ConfirmDialog
      options={pending}
      onSettle={settle}
    />
  ) : null

  return { confirm, dialog }
}

function ConfirmDialog({ options, onSettle }: { options: PendingConfirm; onSettle: (ok: boolean) => void }) {
  const [busy, setBusy] = useState(false)
  const confirmRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    // 打开即聚焦确认按钮：Enter 直接确认、Esc 取消
    confirmRef.current?.focus()
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') { e.preventDefault(); onSettle(false) }
      if (e.key === 'Enter') { e.preventDefault(); onSettle(true) }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onSettle])

  return (
    <div className="confirm-overlay" onMouseDown={e => { if (e.target === e.currentTarget && !busy) onSettle(false) }}>
      <div className="confirm-box" role="alertdialog" aria-modal="true" aria-label={options.title}>
        <div className="confirm-box__icon">
          <AlertTriangle size={18} />
        </div>
        <h3 className="confirm-box__title">{options.title}</h3>
        <p className="confirm-box__message">{options.message}</p>
        <div className="confirm-box__actions">
          <button type="button" className="confirm-box__cancel" onClick={() => onSettle(false)} disabled={busy}>
            {options.cancelText ?? '取消'}
          </button>
          <button
            type="button"
            ref={confirmRef}
            className={`confirm-box__confirm ${options.danger ? 'is-danger' : ''}`}
            onClick={() => { setBusy(true); onSettle(true) }}
            disabled={busy}
          >
            {busy ? <LoaderCircle size={13} className="is-spinning" /> : null}
            {options.confirmText ?? '确定'}
          </button>
        </div>
      </div>
    </div>
  )
}
