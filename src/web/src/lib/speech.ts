/** 朗读与文本清理：把 markdown 正文转成适合语音播报的纯文本。 */

/** 粗略剥离 markdown 标记（代码块用占位词代替，避免朗读符号）。 */
export function toSpeakableText(markdown: string): string {
  let text = String(markdown || '')
  text = text.replace(/```[\s\S]*?```/g, '，代码块，')
  text = text.replace(/`([^`]*)`/g, '$1')
  text = text.replace(/!\[[^\]]*\]\([^)]*\)/g, '')
  text = text.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
  text = text.replace(/^#{1,6}\s+/gm, '')
  text = text.replace(/^\s*[-*+]\s+/gm, '')
  text = text.replace(/\*\*([^*]+)\*\*/g, '$1')
  text = text.replace(/[*_>|]/g, '')
  text = text.replace(/https?:\/\/\S+/g, '，链接，')
  return text.replace(/\s+/g, ' ').trim().slice(0, 4000)
}

export function speechSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

/** 开始朗读；返回是否成功启动。onEnd 在自然结束或被取消时触发。 */
export function speak(text: string, onEnd: () => void): boolean {
  if (!speechSupported() || !text) return false
  window.speechSynthesis.cancel()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = 'zh-CN'
  utterance.rate = 1.05
  utterance.onend = onEnd
  utterance.onerror = onEnd
  window.speechSynthesis.speak(utterance)
  return true
}

export function stopSpeaking(): void {
  if (speechSupported()) window.speechSynthesis.cancel()
}
