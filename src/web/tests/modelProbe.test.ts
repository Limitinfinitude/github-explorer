import test from 'node:test'
import assert from 'node:assert/strict'

import { modelProbeReadiness, probeStatusText } from '../src/lib/modelProbe.ts'


test('URL latency is available without an API key', () => {
  const readiness = modelProbeReadiness({
    base_url: 'https://gateway.example/v1',
    api_key: '',
    model: '',
  })

  assert.equal(readiness.canMeasure, true)
  assert.equal(readiness.canDiscover, true)
  assert.equal(readiness.canTest, false)
})


test('connection test requires URL and model but permits keyless local services', () => {
  const readiness = modelProbeReadiness({
    base_url: 'http://127.0.0.1:11434/v1',
    api_key: '',
    model: 'qwen3',
  })

  assert.equal(readiness.canTest, true)
})


test('connection success text reports latency without model counts', () => {
  const text = probeStatusText('连接成功', {
    ok: true,
    latency_ms: 42,
    status_code: 200,
  })

  assert.equal(text, '连接成功 · 42 ms · HTTP 200')
  assert.doesNotMatch(text, /模型|个/)
})
