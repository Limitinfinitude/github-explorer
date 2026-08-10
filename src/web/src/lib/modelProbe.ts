export interface ProbeResult {
  ok: boolean
  latency_ms?: number
  status_code?: number
  error?: string
}

export interface ModelDiscoveryResult extends ProbeResult {
  models: string[]
}

export function modelProbeReadiness(form: { base_url: string; api_key: string; model: string }) {
  const hasUrl = Boolean(form.base_url.trim())
  return {
    canMeasure: hasUrl,
    canDiscover: hasUrl,
    canTest: hasUrl && Boolean(form.model.trim()),
  }
}

export function probeStatusText(successLabel: string, result: ProbeResult) {
  if (!result.ok) return result.error || '请求失败'
  const details = [
    typeof result.latency_ms === 'number' ? `${result.latency_ms} ms` : '',
    typeof result.status_code === 'number' ? `HTTP ${result.status_code}` : '',
  ].filter(Boolean)
  return [successLabel, ...details].join(' · ')
}
