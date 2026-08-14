export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  time: string
  toolCalls?: ToolCall[]
  steps?: Step[]
  cmdBlocks?: CmdBlockData[]
  agentRun?: AgentRunSummary
}

export interface Chat {
  id: number
  title: string
  sessionId: string
  messages: Message[]
  created: number
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
}

export interface CustomModelInput {
  name: string
  model: string
  protocol: 'anthropic' | 'openai'
  base_url: string
  api_key: string
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
  status: 'completed' | 'incomplete' | 'failed' | 'blocked' | 'cancelled' | null
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
  tool_count: number
  failed_tool_count: number
  recovered_tool_count: number
  changed_file_count: number
  verification: 'passed' | 'failed' | 'not_run'
  created_at: string
  updated_at: string
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
  trace: AgentTrace | null
}

export interface ProjectSummary {
  project_id: string
  workspace_root: string
  latest_task_id: string
  task_count: number
  updated_at?: string
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
  local: { enabled: boolean; storage: string; retention: string; coverage: string[] }
}

export type View = 'chat' | 'project' | 'explore' | 'activity' | 'settings'

export type SSEEvent =
  | { type: 'workspace'; path: string; session_id: string; task_id?: string }
  | { type: 'plan'; steps: string[]; session_id: string; task_id: string }
  | { type: 'repo_map'; content: string; files_scanned: number; session_id: string; task_id: string }
  | { type: 'step'; step: string; icon: string }
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
  | { type: 'approval_required'; tool_name: string; args: Record<string, unknown>; reason: string; task_id: string }
  | { type: 'token'; content: string }
  | { type: 'done'; content: string; status?: 'completed' | 'incomplete' | 'waiting_approval' | 'failed' | 'blocked' | 'cancelled' }
  | { type: 'error'; content: string }
