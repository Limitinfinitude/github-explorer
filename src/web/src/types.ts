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
}

export interface AgentVerification {
  success: boolean
  checks: Array<{ command: string; success: boolean; returncode?: number; output?: string }>
}

export interface AgentProcess {
  processId: string
  status: string
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

export interface AgentRunSummary {
  taskId: string | null
  plan: string[]
  fileChanges: AgentFileChange[]
  verification: AgentVerification | null
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
  status: string
  tool_count: number
  failed_tool_count: number
  changed_file_count: number
  verification: 'passed' | 'failed' | 'not_run'
  created_at: string
  updated_at: string
}

export interface AgentTraceDetail {
  task: Record<string, unknown> | null
  activity: {
    tool_runs: Array<{ tool_name: string; args: Record<string, unknown>; result: Record<string, unknown>; created_at: string }>
    changesets: Array<{ files: string[]; diff: string; created_at: string }>
  }
}

export interface ObservabilityStatus {
  local: { enabled: boolean; storage: string; retention: string }
  langsmith: { enabled: boolean; configured: boolean; project: string }
}

export type View = 'chat' | 'explore' | 'activity' | 'settings'

export type SSEEvent =
  | { type: 'workspace'; path: string; session_id: string }
  | { type: 'plan'; steps: string[]; session_id: string; task_id: string }
  | { type: 'repo_map'; content: string; files_scanned: number; session_id: string; task_id: string }
  | { type: 'step'; step: string; icon: string }
  | { type: 'tool_call'; name: string; args: Record<string, unknown> }
  | { type: 'tool_result'; name: string; success: boolean; output: string; error?: string; data?: Record<string, unknown> }
  | { type: 'cmd_preview'; command: string; risk: 'safe' | 'high'; reason: string }
  | { type: 'cmd_line'; text: string }
  | { type: 'cmd_done'; success: boolean; returncode: number }
  | { type: 'file_changed'; files: string[]; diff: string; task_id: string }
  | { type: 'verification'; success: boolean; checks: AgentVerification['checks']; task_id: string }
  | { type: 'process_started'; process_id: string; data: { status?: string; pid?: number; cwd?: string }; task_id: string }
  | { type: 'approval_required'; tool_name: string; args: Record<string, unknown>; reason: string; task_id: string }
  | { type: 'token'; content: string }
  | { type: 'done'; content: string; status?: 'completed' | 'waiting_approval' | 'failed' }
  | { type: 'error'; content: string }
