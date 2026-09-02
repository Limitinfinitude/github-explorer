/** Markdown 渲染前的文本预处理。 */

/**
 * GFM autolink 会把 URL 后紧跟的全角字符（（）、。等）吞进链接，
 * 造成坏链。在 URL 与全角字符之间补一个空格，让 autolink 正确截断。
 */
const URL_FULLWIDTH_RE = /(https?:\/\/[A-Za-z0-9._~:/?#[\]@!$&'()*+,;=%-]+)(?=[\u3001\u3002\uff08\uff09\uff0c\uff1a\uff1b\uff01\uff1f\u201c\u201d\u2018\u2019\u300a\u300b\u2014\u2026])/g

export function preprocessMarkdown(text: string): string {
  return text.replace(URL_FULLWIDTH_RE, '$1 ')
}
