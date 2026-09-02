import React from 'react'
import { FileCode2 } from 'lucide-react'
import { diffStats, type DiffFile, type DiffHunk } from '../../lib/diffView'

/** 单个 hunk：左列旧行号 + 右列新行号 + 符号 + 代码；绿=新增，红=删除，灰=上下文。 */
function HunkLines({ hunk }: { hunk: DiffHunk }) {
  let oldNo = hunk.oldStart
  let newNo = hunk.newStart
  return (
    <div className="diff-lines">
      {hunk.lines.map((line, index) => {
        let oldLabel = ''
        let newLabel = ''
        if (oldNo !== null && line.type !== 'add') { oldLabel = String(oldNo); oldNo += 1 }
        if (newNo !== null && line.type !== 'del') { newLabel = String(newNo); newNo += 1 }
        return (
          <div className={`diff-line is-${line.type}`} key={index}>
            <span className="diff-line__old">{oldLabel}</span>
            <span className="diff-line__new">{newLabel}</span>
            <span className="diff-line__mark">{line.type === 'add' ? '+' : line.type === 'del' ? '−' : ' '}</span>
            <span className="diff-line__text">{line.text || '\u00A0'}</span>
          </div>
        )
      })}
    </div>
  )
}

/** 文件级 diff 块：文件头（路径 + 增删统计）+ hunks。 */
export function DiffFileView({ file }: { file: DiffFile }) {
  const stats = diffStats([file])
  return (
    <div className="diff-file">
      <div className="diff-file__head">
        <FileCode2 size={11} />
        <span className="diff-file__path">{file.path}</span>
        <span className="diff-file__stat">
          <b className="is-add">+{stats.add}</b>
          <b className="is-del">−{stats.del}</b>
        </span>
      </div>
      {file.hunks.map((hunk, index) => (
        <div className="diff-hunk" key={index}>
          {hunk.oldStart !== null && (
            <div className="diff-hunk__head">
              @@ -{hunk.oldStart} +{hunk.newStart} @@
            </div>
          )}
          <HunkLines hunk={hunk} />
        </div>
      ))}
    </div>
  )
}
