import React, { useEffect, useState } from 'react'
import { ChevronRight, FileText, Folder, FolderPlus, Plus } from 'lucide-react'
import { api } from '../../lib/api'

interface FsEntry {
  name: string
  path: string
  type: 'directory' | 'file'
  size?: number | null
}

interface Props {
  sessionId: string
  /** 当前选中的位置（绝对路径） */
  value: string
  onSelect: (path: string) => void
}

/** 单层目录节点：可展开/收起。 */
function DirNode({
  sessionId, entry, depth, selected, onSelect,
}: {
  sessionId: string
  entry: FsEntry
  depth: number
  selected: string
  onSelect: (path: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [children, setChildren] = useState<FsEntry[] | null>(null)
  const [loading, setLoading] = useState(false)

  async function toggle() {
    if (entry.type !== 'directory') { onSelect(entry.path); return }
    setOpen(v => !v)
    if (!children && !loading) {
      setLoading(true)
      try {
        const result = await api.listFs(sessionId, entry.path)
        setChildren(result.entries)
      } catch {
        setChildren([])
      } finally { setLoading(false) }
    }
  }

  return (
    <div>
      <button
        type="button"
        className={`dirtree-node ${selected === entry.path ? 'is-selected' : ''}`}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        onClick={() => void toggle()}
        title={entry.path}
      >
        {entry.type === 'directory'
          ? <ChevronRight size={12} className={`dirtree-node__chevron ${open ? 'is-open' : ''}`} />
          : <span className="dirtree-node__spacer" />}
        {entry.type === 'directory' ? <Folder size={13} /> : <FileText size={13} />}
        <span>{entry.name}</span>
      </button>
      {open && (
        <div>
          {loading && <div className="dirtree-node__loading" style={{ paddingLeft: `${22 + depth * 14}px` }}>加载中…</div>}
          {children?.length === 0 && <div className="dirtree-node__empty" style={{ paddingLeft: `${22 + depth * 14}px` }}>空目录</div>}
          {children?.map(child => (
            <DirNode
              key={child.path}
              sessionId={sessionId}
              entry={child}
              depth={depth + 1}
              selected={selected}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  )
}

/** 目录树浏览器：可展开选择位置、新建文件夹。 */
export function DirTree({ sessionId, value, onSelect }: Props) {
  const [entries, setEntries] = useState<FsEntry[]>([])
  const [root, setRoot] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [newFolder, setNewFolder] = useState(false)
  const [folderName, setFolderName] = useState('')
  const [creating, setCreating] = useState(false)
  const [createHint, setCreateHint] = useState('')

  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    api.getWorkspace(sessionId)
      .then(async () => {
        const result = await api.listFs(sessionId, '.')
        if (active) { setRoot(result.root); setEntries(result.entries) }
      })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : '读取目录失败') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  async function createFolder() {
    const name = folderName.trim()
    if (!name || creating) return
    setCreating(true); setCreateHint('')
    const parent = value && value !== root ? value : root
    try {
      const fullPath = parent.endsWith('/') || parent.endsWith('\\')
        ? `${parent}${name}` : `${parent}/${name}`
      await api.createFolder(sessionId, fullPath)
      setFolderName(''); setNewFolder(false)
      onSelect(fullPath)
      setCreateHint(`${name} 已创建`)
      // 重新加载当前目录
      const result = await api.listFs(sessionId, '.')
      setEntries(result.entries)
    } catch (err) {
      setCreateHint(err instanceof Error ? err.message : '创建失败')
    } finally { setCreating(false) }
  }

  return (
    <div className="dirtree">
      <div className="dirtree__toolbar">
        <span className="dirtree__root" title={root}>{root || '工作区'}</span>
        <button type="button" className="dirtree__add" onClick={() => { setNewFolder(v => !v); setCreateHint('') }} title="新建文件夹" aria-label="新建文件夹">
          <FolderPlus size={13} />
        </button>
      </div>
      {newFolder && (
        <div className="dirtree__new">
          <input
            value={folderName}
            onChange={e => setFolderName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') void createFolder() }}
            placeholder="新文件夹名"
            autoFocus
            aria-label="新文件夹名"
          />
          <button type="button" onClick={() => void createFolder()} disabled={!folderName.trim() || creating}>
            <Plus size={12} />{creating ? '创建中…' : '创建'}
          </button>
        </div>
      )}
      {createHint && <div className={`dirtree__hint ${createHint.includes('失败') || createHint.includes('无法') ? 'is-error' : ''}`}>{createHint}</div>}
      {loading ? <div className="dirtree__empty">加载中…</div> : error ? <div className="dirtree__empty is-error">{error}</div> : (
        <div className="dirtree__list">
          {entries.length === 0 && <div className="dirtree__empty">工作区无子目录</div>}
          {entries.map(entry => (
            <DirNode key={entry.path} sessionId={sessionId} entry={entry} depth={0} selected={value} onSelect={onSelect} />
          ))}
        </div>
      )}
    </div>
  )
}
