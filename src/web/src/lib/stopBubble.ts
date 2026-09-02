import { useEffect, useRef } from 'react'

/** 阻止内部 <details> 的点击冒泡到外层 <details>（嵌套折叠容器的原生 toggle 连动问题）。
 *
 * React 的 onClick 是合成事件（挂 root，冒泡阶段晚期），拦不住原生 <summary> 点击
 * 对外层 <details> 的 toggle——必须在原生捕获阶段 stopPropagation。
 * 用法：const ref = useStopBubbleRef(); <details ref={ref}>…
 */
export function useStopBubbleRef<T extends HTMLElement>() {
  const ref = useRef<T | null>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const stop = (e: Event) => e.stopPropagation()
    el.addEventListener('click', stop, true)
    return () => el.removeEventListener('click', stop, true)
  }, [])
  return ref
}
