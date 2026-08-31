export interface ThinkingSegment {
  content: string
  round: number
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  time: string
  toolCalls?: ToolCall[]
  steps?: Step[]
  cmdBlocks?: CmdBlockData[]
  thinking?: ThinkingSegment[]
  narrations?: string[]
  agentRun?: AgentRunSummary
}

export interface Chat {
  id: number
  title: string
  sessionId: string
  messages: Message[]
  created: number
  /** 归属项目（工作台项目栏）；未设置则为普通任务对话，显示在任务栏。 */
  projectId?: string
  /** 会话绑定的工作区根目录（项目对话 = 项目目录；未绑定前用于首次打开自动绑定）。 */
  workspace?: string
}

export interface Model {
  id: string
  name: string
  model?: string
  protocol?: 'anthropic' | 'openai'
  icon: string
  color: string
  tags: string[]
  api_key_masked?: string
  base_url?: string
  has_key?: boolean
  thinking_effort?: 'off' | 'high' | 'max'
  context_window?: string
  context_window_tokens?: number
}

export interface ContextUsage {
  window: number
  used: number
  breakdown: {
    history: number
    tools_system: number
    tools_mcp: number
    system_prompt: number
    other?: number
  }
  cache_hit_tokens?: number
  cache_hit_rate?: number | null
  compactions: number
}

export interface CustomModelInput {
  name: string
  model: string
  protocol: 'anthropic' | 'openai'
  base_url: string
  api_key: string
  thinking_effort: 'off' | 'high' | 'max'
}

export interface ToolCall {
  name: string
  args: Record<string, unknown>
  success?: boolean
  output?: string
}

export interface Step {
  icon: string
  text: string
  done: boolean
  callId?: string
  toolName?: string
  args?: Record<string, unknown>
  output?: string
  error?: string
  status?: 'running' | 'succeeded' | 'failed' | 'rejected' | 'interrupted'
  recoveredByCallId?: string
}

export interface CmdBlockData {
  id: string
  command: string
  risk: 'safe' | 'high'
  reason: string
  lines: string[]
  done: boolean
  success?: boolean
}

export interface AgentFileChange {
  files: string[]
  diff: string
  pathKinds?: Record<string, 'file' | 'directory'>
}

export interface AgentVerification {
  success: boolean
  checks: Array<{
    command: string
    success: boolean
    returncode?: number
    output?: string
    cwd?: string
    python_executable?: string
    kind?: 'write_readback' | 'static' | 'unit' | 'build' | 'http' | 'port' | 'browser' | 'command'
  }>
}

export interface AgentProcess {
  processId: string
  status: 'running' | 'stopped' | 'exited' | 'orphaned'
  pid?: number
  cwd?: string
  command?: string
  logs?: string
  returncode?: number | null
  url?: string
}

export interface AgentApproval {
  taskId: string
  toolName: string
  args: Record<string, unknown>
  reason: string
}

export interface AgentAcceptanceEvidence {
  type: 'file' | 'check' | 'process'
  ref: string
  valid: boolean
  sufficient?: boolean
}

export interface AgentAcceptanceItem {
  id: number
  text: string
  status: 'passed' | 'failed' | 'unverified'
  evidence: AgentAcceptanceEvidence[]
  reason: string
}

export interface AgentRunSummary {
  taskId: string | null
  status: 'pending' | 'queued' | 'running' | 'waiting_approval' | 'completed' | 'incomplete' | 'failed' | 'blocked' | 'cancelled' | 'interrupted' | null
  plan: string[]
  fileChanges: AgentFileChange[]
  verification: AgentVerification | null
  acceptance?: AgentAcceptanceItem[]
  processes: AgentProcess[]
  repoMap?: string
}

export interface Repo {
  full_name: string
  description: string
  stars: number
  forks: number
  language: string
  topics?: string[]
  pushed_at?: string
  html_url?: string
  url?: string
  stars_today?: number
  trending_period?: string
  /** trending enrichment 补全的详情字段 */
  owner_avatar?: string
  open_issues?: number
  license?: string
}

export interface WorkspaceProfile {
  name: string
  path: string
  git: boolean
  branch: string | null
  python: boolean
  node: boolean
  venv: boolean
}

export interface WorkspaceResponse {
  session_id: string
  workspace: string | null
  root?: string | null
  current_path?: string | null
  source?: 'session' | 'request' | 'default' | 'fallback'
  profile: WorkspaceProfile | null
  recent: string[]
}

export interface DefaultWorkspaceResponse {
  path: string
  source: 'configured' | 'fallback'
}

export interface AgentTrace {
  task_id: string
  session_id: string
  message: string
  message_encoding_status?: 'intact' | 'legacy_corrupted'
  status: string
  resume_available?: boolean
  workspace_root?: string
  tool_count: number
  failed_tool_count: number
  recovered_tool_count: number
  changed_file_count: number
  verification: 'passed' | 'failed' | 'not_run'
  terminal_reason?: string
  completion_evidence?: 'verified' | 'partial' | 'none'
  budget_exhausted_stages?: string[]
  approval_count?: number
  successful_tool_count?: number
  acceptance_passed?: boolean
  acceptance_total?: number
  model_rounds?: number
  model_error_count?: number
  provider_truncation_count?: number
  diagnostic_tool_count?: number
  diagnostic_unique_count?: number
  model_latency_ms?: number
  total_tokens?: number
  event_count?: number
  last_event_type?: string | null
  last_event_at?: string | null
  metrics_version?: string
  metrics_computed_at?: string | null
  created_at: string
  updated_at: string
}

export interface EvaluationReport {
  generated_at: string
  metrics_version: string | null
  summary: {
    task_count: number
    status_counts: Record<string, number>
    tool_count: number
    changed_file_count: number
    artifact_count: number
  }
  tasks: Array<{
    task_id: string
    session_id: string
    workspace_root?: string
    status: string
    terminal_reason?: string
    completion_evidence?: string
    tool_count: number
    tools: Array<Record<string, unknown>>
    changed_files: string[]
    artifacts: Array<Record<string, unknown>>
  }>
}

export interface AgentTraceDetail {
  task: Record<string, unknown> | null
  activity: {
    events: AgentEvent[]
    tool_runs: Array<{
      tool_name: string
      args: Record<string, unknown>
      result: Record<string, unknown>
      recovered_by_call_id?: string | null
      created_at: string
    }>
    changesets: Array<{ files: string[]; diff: string; created_at: string }>
    artifacts: AgentArtifact[]
  }
}

export interface ProjectOverview {
  project_id: string
  project_session_id: string
  workspace_root: string
  current_path: string
  stage: 'intake' | 'inspect' | 'run' | 'understand' | 'experiment' | 'verify'
  stage_status: string
  next_action: string
  summary: {
    task_id?: string
    message: string
    status: string
    changed_file_count: number
    verification_count: number
    process_count: number
    failed: boolean
  }
  evidence_counts: {
    events: number
    tool_runs: number
    changesets: number
    files: number
    artifacts: number
  }
  changed_files: string[]
  active_processes: Array<Record<string, unknown>>
  latest_verification: Record<string, unknown> | null
  stage_budgets: Record<string, { used: number; limit: number; status: string }>
  failure_patterns: ProjectFailurePattern[]
  quality_metrics: {
    false_completion: boolean
    false_incomplete: boolean
    evidence_coverage: number | null
    tool_recovery_rate: number | null
    unrecovered_tool_failures: number
    model_rounds: number
    model_latency_ms: number
    total_tokens: number
    event_count: number
    terminal_reason: string
    completion_evidence: 'verified' | 'partial' | 'none'
    budget_exhausted_stages: string[]
    approval_count: number
    failed_tool_count: number
    recovered_tool_count: number
    successful_tool_count: number
    last_event_type: string | null
    last_event_at: string | null
    acceptance_passed: boolean
    acceptance_total: number
    acceptance_passed_count: number
  }
  trace: AgentTrace | null
}

export interface ProjectSummary {
  project_id: string
  workspace_root: string
  latest_task_id: string
  task_count: number
  updated_at?: string
}

export interface HookConfig {
  event: string
  command: string
  matcher: string
  enabled: boolean
  timeout: number
}

/** 项目矩阵行：所有项目的轻量状态（工作台矩阵视图） */
export interface ProjectMatrixRow {
  project_id: string
  workspace_root: string
  stage: string
  stage_status: string
  next_action: string
  message: string
  status: string
  failed: boolean
  verification_count: number
  updated_at: string
  /** 工作区身份：正式项目（有 git/清单标记）享受项目仪式；临时工作区折叠降级。 */
  kind?: 'project' | 'scratch'
  /** 该工作区的项目会话 id（临时工作区「继续对话」用）。 */
  session_id?: string
  /** 用户手动归档（评测克隆仓等噪音），折叠到已归档组。 */
  archived?: boolean
}

export interface ProjectFailurePattern {
  tool_name: string
  error: string
  count: number
  last_at: string
}

export interface ProjectMemory {
  id: number
  workspace_root: string
  content: string
  source_type: string
  source_ref: string
  confidence: number
  verification_status: string
  expires_at: string | null
  created_at: string
  updated_at: string
}

export interface ProjectReport {
  project_id: string
  generated_at: string
  markdown: string
}

export interface ProjectImportResult {
  project_id: string
  session_id: string
  task_id: string
  workspace: string
  status: string
}

export interface ProjectEvidence {
  project_id: string
  workspace_root: string
  task: Record<string, unknown>
  task_history: Array<{
    task_id: string
    session_id?: string
    message: string
    status: string
    created_at: string
  }>
  entries: ProjectEvidenceEntry[]
  events: AgentEvent[]
  tool_runs: Array<{
    tool_name: string
    args: Record<string, unknown>
    result: Record<string, unknown>
    recovered_by_call_id?: string | null
    created_at: string
  }>
  changesets: Array<{ files: string[]; diff: string; created_at: string }>
  artifacts: AgentArtifact[]
  developer_layers: string[]
}

export type ProjectEvidenceCategory = 'events' | 'tools' | 'files' | 'verification' | 'processes' | 'observability'
export type ProjectEvidenceFilter = 'all' | 'failed' | 'recovered' | Exclude<ProjectEvidenceCategory, 'events' | 'observability'>

export interface ProjectEvidenceEntry {
  id: string
  task_id: string
  category: ProjectEvidenceCategory
  status: string
  title: string
  created_at: string
  details: Record<string, unknown>
}

export interface AgentArtifact {
  artifact_id: string
  task_id: string
  call_id: string
  tool_name: string
  mime_type: string
  size: number
  created_at: string
}

export interface AgentEvent {
  sequence: number
  type: string
  payload: Record<string, unknown>
  created_at: string
}

export interface ObservabilityStatus {
  local: {
    enabled: boolean
    storage: string
    retention: string
    coverage: string[]
    summary: {
      task_count: number
      status_counts: Record<string, number>
      terminal_reason_counts: Record<string, number>
      completion_evidence_counts: Record<string, number>
      budget_exhausted_count: number
      approval_count: number
      failed_tool_count: number
      recovered_tool_count: number
      false_completion_count: number
      false_incomplete_count: number
      average_model_latency_ms: number
      total_tokens: number
    }
  }
}

export type View = 'chat' | 'project' | 'explore' | 'activity' | 'settings'

export type SSEEvent =
  | { type: 'input_warning'; status: 'corrupted'; reason: string; message: string }
  | { type: 'workspace'; path: string; session_id: string; task_id?: string }
  | { type: 'plan'; steps: string[]; session_id: string; task_id: string }
  | { type: 'repo_map'; content: string; files_scanned: number; session_id: string; task_id: string }
  | { type: 'step'; step: string; icon: string }
  | { type: 'narration'; tool_name: string; content: string }
  | { type: 'thinking'; content: string; round?: number }
  | { type: 'tool_call'; name: string; tool_name?: string; args: Record<string, unknown>; call_id: string; batch_id: string }
  | { type: 'tool_result'; name: string; tool_name?: string; success: boolean; output: string; error?: string; error_kind?: string; data?: Record<string, unknown>; artifact?: AgentArtifact | null; call_id: string; batch_id: string }
  | { type: 'tool_recovered'; name: string; failed_call_id: string; recovered_by_call_id: string; recovery_key: string }
  | { type: 'cmd_preview'; command: string; risk: 'safe' | 'high'; reason: string }
  | { type: 'cmd_line'; text: string }
  | { type: 'cmd_done'; success: boolean; returncode: number }
  | { type: 'file_changed'; files: string[]; diff: string; path_kinds?: Record<string, 'file' | 'directory'>; task_id: string }
  | { type: 'verification'; success: boolean; checks: AgentVerification['checks']; task_id: string }
  | { type: 'acceptance'; success: boolean; items: AgentAcceptanceItem[]; task_id: string }
  | { type: 'process_started'; process_id: string; data: { status?: AgentProcess['status']; pid?: number; cwd?: string }; task_id: string }
  | { type: 'budget_warning'; diagnostic_tool_count: number; round_limit: number; message: string; plan?: string[]; task_id: string }
  | { type: 'context_usage'; window: number; used: number; breakdown: ContextUsage['breakdown']; cache_hit_tokens?: number; cache_hit_rate?: number | null; compactions: number }
  | { type: 'approval_required'; tool_name: string; args: Record<string, unknown>; reason: string; task_id: string }
  | { type: 'token'; content: string }
  | { type: 'done'; content: string; status?: 'completed' | 'incomplete' | 'waiting_approval' | 'failed' | 'blocked' | 'cancelled' }
  | { type: 'error'; content: string }
