import React, { useEffect, useRef, useState } from 'react'
import { ArrowUp, Folder, HardDrive, Home, LoaderCircle, X } from 'lucide-react'
import { api } from '../../lib/api'

interface Props {
  open: boolean
  initialPath: string
  onSelect: (path: string) => void
  onClose: () => void
}

type Entry = { name: string; path: string; type: 'directory' }

/** 设置页路径选择弹层：从盘符/主目录起逐层浏览，选中目录回填。 */
export function PathBrowser({ open, initialPath, onSelect, onClose }: Props) {
  const [current, setCurrent] = useState('') // '' = 起点（盘符+主目录）
  const [entries, setEntries] = useState<Entry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    // 打开时：有初值目录则直接列它，否则列起点
    void load(initialPath && initialPath.includes('\\') ? initialPath : '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  async function load(path: string) {
    setLoading(true); setError('')
    try {
      const result = await api.browseSettingsDirectory(path)
      setCurrent(result.path)
      setEntries(result.entries)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取目录失败')
      // 失败退回起点
      if (path) {
        try {
          const result = await api.browseSettingsDirectory('')
          setCurrent('')
          setEntries(result.entries)
        } catch { /* 起点也失败则保持空 */ }
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="path-browser-overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="path-browser" ref={panelRef} role="dialog" aria-label="选择目录">
        <div className="path-browser__head">
          <Folder size={15} />
          <strong>选择目录</strong>
          <button type="button" className="path-browser__close" onClick={onClose} aria-label="关闭">
            <X size={14} />
          </button>
        </div>

        <div className="path-browser__crumbs">
          <button type="button" className="path-browser__crumb" onClick={() => void load('')} title="回到起点（盘符/主目录）">
            <HardDrive size={12} />起点
          </button>
          {current
            ? current.split(/[\\/]+/).filter(Boolean).map((part, i, arr) => {
                // 逐级累积路径（Windows 盘符 "E:" 需补反斜杠）
                const isDrive = i === 0 && part.endsWith(':')
                const fullPath = isDrive
                  ? part + '\\'
                  : arr.slice(0, i + 1).join('\\')
                return (
                  <React.Fragment key={fullPath + i}>
                    <span className="path-browser__sep">›</span>
                    <button type="button" className={`path-browser__crumb ${i === arr.length - 1 ? 'is-current' : ''}`} onClick={() => void load(fullPath)}>
                      {part}
                    </button>
                  </React.Fragment>
                )
              })
            : null}
          {loading && <LoaderCircle size={12} className="is-spinning path-browser__loading" />}
        </div>

        <div className="path-browser__list">
          {error && <div className="path-browser__error">{error}</div>}
          {!loading && !error && current && (
            <button type="button" className="path-browser__item path-browser__item--parent" onClick={() => void load(entries.find(e => e.name === '..')?.path ?? '')}>
              <ArrowUp size={13} />..
            </button>
          )}
          {!loading && entries
            .filter(e => e.name !== '..')
            .map(entry => (
              <button
                type="button"
                key={entry.path}
                className={`path-browser__item ${entry.name.startsWith('主目录') ? 'path-browser__item--home' : ''}`}
                onClick={() => void load(entry.path)}
                onDoubleClick={() => { onSelect(entry.path); onClose() }}
                title={entry.path}
              >
                {entry.name.startsWith('主目录')
                  ? <Home size={13} />
                  : <Folder size={13} />}
                <span>{entry.name}</span>
              </button>
            ))}
          {!loading && !error && entries.filter(e => e.name !== '..').length === 0 && (
            <div className="path-browser__empty">空目录</div>
          )}
        </div>

        <div className="path-browser__foot">
          <code className="path-browser__current" title={current || '未选择'}>{current || '浏览选择一个目录…'}</code>
          <button type="button" className="path-browser__confirm" disabled={!current} onClick={() => { onSelect(current); onClose() }}>
            选这个目录
          </button>
        </div>
      </div>
    </div>
  )
}
