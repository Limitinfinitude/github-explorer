import React from 'react'
import {
  Activity, CheckCircle2, ChevronDown, CircleDashed, ClipboardCheck, FileCode2,
  GitBranch, Map, ShieldAlert, Square, TerminalSquare, XCircle,
} from 'lucide-react'
import type {
  AgentAcceptanceItem, AgentApproval, AgentFileChange, AgentProcess, AgentRunSummary, AgentVerification,
} from '../../types'
import { summarizeAcceptanceLedger } from '../../lib/acceptanceLedger'

interface Props {
  status?: AgentRunSummary['status']
  plan: string[]
  repoMap?: string
  fileChanges: AgentFileChange[]
  verification: AgentVerification | null
  acceptance?: AgentAcceptanceItem[]
  processes: AgentProcess[]
  approval?: AgentApproval | null
  onAnswerApproval?: (approved: boolean) => void
  onStopProcess?: (processId: string) => void
  compact?: boolean
}

export function AgentStatusPanel({
  status = null,
  plan,
  repoMap = '',
  fileChanges,
  verification,
  acceptance = [],
  processes,
  approval = null,
  onAnswerApproval,
  onStopProcess,
  compact = false,
}: Props) {
  if (!plan.length && !repoMap && !fileChanges.length && !verification && !acceptance.length && !processes.length && !approval) return null

  const fileCount = new Set(fileChanges.flatMap(item => item.files.filter(
    path => item.pathKinds?.[path] !== 'directory',
  ))).size
  const acceptanceSummary = summarizeAcceptanceLedger(acceptance)
  const processStatusLabels: Record<AgentProcess['status'], string> = {
    running: '运行中',
    stopped: '已停止',
    exited: '已退出',
    orphaned: '已失联',
  }
  const body = (
    <>
      {!compact && <header className="agent-run__header">
        <div className="agent-run__title">
          <Activity size={14} aria-hidden="true" />
          <span>Agent 执行记录</span>
        </div>
        <span className="agent-run__count">{fileCount} 个文件</span>
      </header>}

      {plan.length > 0 && (
        <div className="agent-run__section">
          <div className="agent-run__label"><GitBranch size={13} />执行计划</div>
          <ol className="agent-plan">
            {plan.map((step, index) => (
              <li key={`${step}-${index}`}>
                <span>{index + 1}</span>
                <p>{step}</p>
              </li>
            ))}
          </ol>
        </div>
      )}

      {repoMap && (
        <details className="agent-run__section agent-details">
          <summary>
            <span className="agent-run__label"><Map size={13} />Repo Map</span>
            <ChevronDown size={14} />
          </summary>
          <pre>{repoMap}</pre>
        </details>
      )}

      {fileChanges.length > 0 && (
        <div className="agent-run__section">
          <div className="agent-run__label"><FileCode2 size={13} />文件变更</div>
          <div className="agent-file-list">
            {fileChanges.map((change, index) => (
              <details key={`${change.files.join('-')}-${index}`} className="agent-details">
                <summary>
                  <span>{change.files.join(', ')}</span>
                  <ChevronDown size={14} />
                </summary>
                {change.diff && <pre>{change.diff}</pre>}
              </details>
            ))}
          </div>
        </div>
      )}

      {verification && (
        <div className="agent-run__section agent-verification">
          <div className={`agent-result ${verification.success ? 'is-success' : 'is-error'}`}>
            {verification.success ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
            <span>{verification.success ? '验证通过' : '验证失败'}</span>
          </div>
          <div className="agent-checks">
            {verification.checks.map((check, index) => (
              <span key={`${check.command}-${index}`}>
                {check.kind ? `[${check.kind}] ` : ''}{check.command || '检查'}
                {check.cwd ? ` · cwd ${check.cwd}` : ''}
                {check.python_executable ? ` · Python ${check.python_executable}` : ''}
                {check.returncode !== undefined ? ` · 退出码 ${check.returncode}` : ''}
              </span>
            ))}
          </div>
        </div>
      )}

      {acceptance.length > 0 && (
        <div className="agent-run__section">
          <div className="agent-run__label">
            <ClipboardCheck size={13} />需求验收
            <span className="agent-acceptance__summary">
              {acceptanceSummary.passed}/{acceptanceSummary.total} 有证据通过
            </span>
          </div>
          <ol className="agent-acceptance">
            {acceptance.map(item => (
              <li key={item.id} className={`is-${item.status}`}>
                <span className="agent-acceptance__index">{item.id}</span>
                <div>
                  <strong>{item.text}</strong>
                  <small>{item.status === 'passed' ? '通过' : item.status === 'failed' ? '未完成' : '未验证'}</small>
                  {item.evidence.length > 0 && (
                    <p>{item.evidence.map(evidence => `${!evidence.valid ? '无效' : evidence.sufficient === false ? '有效但不足' : '有效'} ${evidence.type}:${evidence.ref}`).join(' · ')}</p>
                  )}
                  {item.reason && <p>{item.reason}</p>}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      {processes.length > 0 && (
        <div className="agent-run__section">
          <div className="agent-run__label"><TerminalSquare size={13} />后台进程</div>
          <div className="agent-process-list">
            {processes.map(process => (
              <details key={process.processId} className="agent-process agent-details">
                <summary>
                  {process.status === 'running'
                    ? <CircleDashed className="agent-process__pulse" size={14} />
                    : <CheckCircle2 size={14} />}
                  <span className="agent-process__command">{process.command || process.processId.slice(0, 12)}</span>
                  <span className={`agent-process__status is-${process.status}`}>{processStatusLabels[process.status]}</span>
                  {process.status === 'running' && onStopProcess && (
                    <button
                      type="button"
                      title="停止后台进程"
                      aria-label="停止后台进程"
                      onClick={event => { event.preventDefault(); onStopProcess(process.processId) }}
                      className="agent-icon-button is-danger"
                    >
                      <Square size={11} fill="currentColor" />
                    </button>
                  )}
                  <ChevronDown size={14} />
                </summary>
                <div className="agent-process__meta">
                  <span>PID {process.pid ?? '—'}</span>
                  <span>{process.cwd || '目录未知'}</span>
                  {process.returncode !== null && process.returncode !== undefined && <span>退出码 {process.returncode}</span>}
                </div>
                <pre>{process.logs || '暂无日志输出'}</pre>
              </details>
            ))}
          </div>
        </div>
      )}

      {approval && (
        <div className="agent-approval">
          <ShieldAlert size={18} />
          <div>
            <strong>需要确认：{approval.toolName}</strong>
            <p>{approval.reason}</p>
          </div>
          <div className="agent-approval__actions">
            <button type="button" onClick={() => onAnswerApproval?.(false)}>拒绝</button>
            <button type="button" className="is-primary" onClick={() => onAnswerApproval?.(true)}>允许并继续</button>
          </div>
        </div>
      )}
    </>
  )

  if (compact) {
    const failed = status === 'failed' || verification?.success === false
    const incomplete = status === 'incomplete' || status === 'blocked' || status === 'cancelled' || status === 'interrupted'
    const StatusIcon = failed ? XCircle : incomplete ? CircleDashed : CheckCircle2
    const statusText = failed
      ? '执行失败'
      : status === 'cancelled'
        ? '已取消'
        : status === 'interrupted'
          ? '执行已中断'
        : incomplete
          ? '执行未完成'
          : status === 'completed'
            ? '执行完成'
            : status === 'waiting_approval'
              ? '等待确认'
              : status === 'running'
                ? '执行中'
                : status === 'pending' || status === 'queued'
                  ? '等待执行'
                  : '执行状态未知'
    const meta = [
      fileCount ? `${fileCount} 个文件` : '无文件变更',
      verification ? (verification.success ? '验证通过' : '验证失败') : '未运行验证',
      acceptance.length ? `${acceptanceSummary.passed}/${acceptanceSummary.total} 项验收` : null,
      processes.length ? `${processes.length} 个进程` : null,
    ].filter(Boolean).join(' · ')

    return (
      <details className="agent-run agent-run--compact">
        <summary className="agent-run__compact-summary">
          <StatusIcon size={14} />
          <strong>{statusText}</strong>
          <span>{meta}</span>
          <ChevronDown size={14} />
        </summary>
        <div className="agent-run__compact-body">{body}</div>
      </details>
    )
  }

  return <section className="agent-run">{body}</section>
}
