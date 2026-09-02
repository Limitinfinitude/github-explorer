/** 统一 diff 展示的数据模型与解析：
 * - 优先解析后端 edit_files 结果里的 data.diff（difflib unified diff，带真实行号）；
 * - 结果缺失/被截断时用 args.edits 回退构建（replace = 红块 search + 绿块 content）。 */

export interface DiffLine {
  type: 'add' | 'del' | 'ctx'
  text: string
}

export interface DiffHunk {
  /** 旧/新文件起始行号；回退构建时为 null（没有真实行号） */
  oldStart: number | null
  newStart: number | null
  lines: DiffLine[]
}

export interface DiffFile {
  path: string
  hunks: DiffHunk[]
}

export interface DiffStats {
  add: number
  del: number
}

const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/

/** 解析 difflib.unified_diff 输出：按 `--- a/xxx` 切分文件段，再逐 hunk 归类行。 */
export function parseUnifiedDiff(diff: string): DiffFile[] {
  const files: DiffFile[] = []
  let current: DiffFile | null = null
  let hunk: DiffHunk | null = null
  let oldNo = 0
  let newNo = 0

  const closeHunk = () => {
    if (hunk) {
      current?.hunks.push(hunk)
      hunk = null
    }
  }

  for (const raw of diff.split('\n')) {
    const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw
    if (line.startsWith('--- a/')) {
      closeHunk()
      current = { path: line.slice(6), hunks: [] }
      files.push(current)
      continue
    }
    if (!current) continue
    if (line.startsWith('+++ b/')) continue
    const m = HUNK_RE.exec(line)
    if (m) {
      closeHunk()
      hunk = { oldStart: Number(m[1]), newStart: Number(m[2]), lines: [] }
      oldNo = Number(m[1])
      newNo = Number(m[2])
      continue
    }
    if (line.startsWith('\\')) continue // "\ No newline at end of file"
    if (!hunk) continue
    if (line.startsWith('+')) {
      hunk.lines.push({ type: 'add', text: line.slice(1) })
      newNo += 1
    } else if (line.startsWith('-')) {
      hunk.lines.push({ type: 'del', text: line.slice(1) })
      oldNo += 1
    } else {
      hunk.lines.push({ type: 'ctx', text: line.startsWith(' ') ? line.slice(1) : line })
      oldNo += 1
      newNo += 1
    }
  }
  closeHunk()
  return files.filter(f => f.hunks.length > 0)
}

interface EditSpec {
  path?: string
  operation?: string
  search?: string
  content?: string
}

/** args.edits → diff 文件列表（无真实行号）：replace 显示被替换块(红)+新内容块(绿)，write 全绿。
 * 单 hunk 超过上限时只展示前 150 行（写入大文件时避免整页绿屏）。 */
const MAX_FALLBACK_LINES = 150

export function editsToDiff(edits: EditSpec[]): DiffFile[] {
  const byPath = new Map<string, DiffHunk[]>()
  for (const edit of edits) {
    const path = String(edit.path || '').trim()
    if (!path) continue
    const hunks = byPath.get(path) ?? []
    const lines: DiffLine[] = []
    if (edit.operation === 'replace' && typeof edit.search === 'string' && edit.search) {
      for (const l of edit.search.split('\n')) lines.push({ type: 'del', text: l })
    }
    if (typeof edit.content === 'string') {
      for (const l of edit.content.split('\n')) lines.push({ type: 'add', text: l })
    }
    if (lines.length) {
      const total = lines.length
      if (total > MAX_FALLBACK_LINES) {
        lines.length = MAX_FALLBACK_LINES
        lines.push({ type: 'ctx', text: `⋯ 共 ${total} 行，仅展示前 ${MAX_FALLBACK_LINES} 行` })
      }
      hunks.push({ oldStart: null, newStart: null, lines })
    }
    byPath.set(path, hunks)
  }
  return [...byPath.entries()].map(([path, h]) => ({ path, hunks: h }))
}

export function diffStats(files: DiffFile[]): DiffStats {
  let add = 0
  let del = 0
  for (const file of files) {
    for (const hunk of file.hunks) {
      for (const line of hunk.lines) {
        if (line.type === 'add') add += 1
        else if (line.type === 'del') del += 1
      }
    }
  }
  return { add, del }
}
