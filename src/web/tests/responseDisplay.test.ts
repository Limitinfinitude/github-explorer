import assert from 'node:assert/strict'
import test from 'node:test'

import { displayResponseContent } from '../src/lib/responseDisplay.ts'

test('structured agent messages show only completion prose', () => {
  const content = [
    '## 完成结果',
    'README.md 第一行是 `# Explorer`。',
    '',
    '## 文件变更',
    '- 无文件变更',
    '',
    '## 验证',
    '- 未运行验证',
    '',
    '## 运行状态',
    '- 无后台进程',
  ].join('\n')

  assert.equal(displayResponseContent(content, true), 'README.md 第一行是 `# Explorer`。')
})

test('plain chat markdown remains unchanged', () => {
  const content = '你好！\n\n- 可以读取文件\n- 可以运行测试'

  assert.equal(displayResponseContent(content, false), content)
})

test('legacy no-op agent envelope shows only the answer without agent metadata', () => {
  const content = [
    '## 完成结果',
    '你好！有什么我可以帮助你的吗？',
    '',
    '## 文件变更',
    '- 无文件变更',
    '',
    '## 验证',
    '- 未运行验证',
    '',
    '## 运行状态',
    '- 无后台进程',
  ].join('\n')

  assert.equal(displayResponseContent(content, false), '你好！有什么我可以帮助你的吗？')
})

test('legacy knowledge answer keeps its own markdown structure', () => {
  const content = [
    '## 完成结果',
    '# 支持 Claude Code 的节省 Token 方案',
    '',
    '## 推荐项目',
    '',
    '1. Anthropic Python SDK',
    '2. LiteLLM',
    '',
    '## 文件变更',
    '- 无文件变更',
    '',
    '## 验证',
    '- 未运行验证',
    '',
    '## 运行状态',
    '- 无后台进程',
  ].join('\n')

  assert.equal(
    displayResponseContent(content, false),
    [
      '# 支持 Claude Code 的节省 Token 方案',
      '',
      '## 推荐项目',
      '',
      '1. Anthropic Python SDK',
      '2. LiteLLM',
    ].join('\n'),
  )
})

test('malformed indented fences do not swallow the rest of a knowledge answer', async () => {
  const content = [
    '## 完成结果',
    '### 2. LiteLLM',
    '- 支持功能：',
    '  ```python',
    '  from litellm import completion',
    '',
    '# 自动 token 计数',
    '  response = completion(model="claude")',
    '  ```',
    '',
    '### 3. Claude Context Manager',
    '- 官方上下文管理实践',
    '',
    '## 文件变更',
    '- 无文件变更',
    '',
    '## 验证',
    '- 未运行验证',
    '',
    '## 运行状态',
    '- 无后台进程',
  ].join('\n')

  const displayed = displayResponseContent(content, false)
  const { marked } = await import('marked')
  const html = marked.parse(displayed) as string
  const codeBlocks = [...html.matchAll(/<code[^>]*>([\s\S]*?)<\/code>/g)]

  assert.match(html, /<h3>3\. Claude Context Manager<\/h3>/)
  assert.equal(codeBlocks.some(match => match[1].includes('Claude Context Manager')), false)
})

test('structured completion drops repeated bold summary fields', () => {
  const content = [
    '## 完成结果',
    '已创建 `cute-cat.html`，包含完整页面和交互。',
    '',
    '**操作详情：**',
    '- 使用 CSS 绘制小猫',
    '',
    '**文件变更：**',
    '- 新增 `cute-cat.html`',
    '',
    '**验证：**',
    '- 浏览器打开成功',
  ].join('\n')

  assert.equal(
    displayResponseContent(content, true),
    [
      '已创建 `cute-cat.html`，包含完整页面和交互。',
      '',
      '**操作详情：**',
      '- 使用 CSS 绘制小猫',
    ].join('\n'),
  )
})
