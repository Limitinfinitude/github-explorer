const SECTION_RE = /^#{1,3}\s*(完成结果|文件变更|验证|运行状态)\s*$/gm
const REPEATED_SUMMARY_RE = /^\*\*(文件变更|验证|运行状态)：?\*\*\s*$/m
const INDENTED_FENCE_RE = /^[ \t]{1,3}(```|~~~)/gm

export function displayResponseContent(content: string, hasStructuredRun: boolean): string {
  const text = content.trim().replace(INDENTED_FENCE_RE, '$1')
  const matches = [...text.matchAll(SECTION_RE)]
  if (!hasStructuredRun && matches.length === 0) return text

  const completionIndex = matches.findIndex(match => match[1] === '完成结果')
  if (completionIndex === -1) return text

  const completion = matches[completionIndex]
  const start = (completion.index ?? 0) + completion[0].length
  const end = matches[completionIndex + 1]?.index ?? text.length
  const completionText = text.slice(start, end).trim()
  const repeatedSummaryIndex = completionText.search(REPEATED_SUMMARY_RE)
  const displayText = repeatedSummaryIndex === -1
    ? completionText
    : completionText.slice(0, repeatedSummaryIndex).trim()
  return displayText || text
}
