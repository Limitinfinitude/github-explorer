import type { AgentAcceptanceItem } from '../types'

export function summarizeAcceptanceLedger(items: AgentAcceptanceItem[] | undefined) {
  const ledger = items ?? []
  return ledger.reduce((summary, item) => {
    summary[item.status] += 1
    summary.validEvidence += item.evidence.filter(evidence => evidence.valid).length
    return summary
  }, {
    passed: 0,
    failed: 0,
    unverified: 0,
    validEvidence: 0,
    total: ledger.length,
  })
}
