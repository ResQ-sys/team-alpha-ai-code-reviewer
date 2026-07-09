// Shared type contracts. Panel owners import these — do not change signatures.

export interface FileNode {
  name: string;
  path: string;
  type: 'file' | 'dir';
  children?: FileNode[];
}

export interface OpenFile {
  path: string;
  name: string;
  language: string;
  content: string;
  dirty: boolean;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export type AgentStatus = 'pending' | 'running' | 'done';

// One row in the live agent-activity timeline. `node`/`label` mirror the
// backend event contract; `detail` is the short finished-summary string.
export interface AgentStep {
  node: string;
  label: string;
  status: AgentStatus;
  detail?: string;
}

export interface Finding {
  file: string;
  line: number;
  severity: string;
  rule_id: string;
  message: string;
  cwe?: string[];
  fixed_code?: string;
  key?: string;
}

// --- Workspace + Agent-mode contract (shared backend<->frontend) -------------

export interface Workspace {
  root: string;
  name: string;
}

// One entry in an agent run's changelist / a single edited-or-created file.
export interface AgentFileChange {
  path: string;
  change: 'edited' | 'created';
  added: number;
  removed: number;
}

// Live agent-run events, mirroring the SSE frames on GET /api/agent/{id}/events.
export interface AgentThoughtEvent {
  type: 'thought';
  text: string;
}
export interface AgentActionEvent {
  type: 'action';
  tool: 'read_file' | 'list_dir' | 'search' | 'edit_file' | 'create_file' | string;
  target?: string;
  summary?: string;
}
export interface AgentObservationEvent {
  type: 'observation';
  text: string;
}
export interface AgentEditEvent {
  type: 'edit';
  path: string;
  change: 'edited' | 'created';
  diff: string;
  added: number;
  removed: number;
}
export interface AgentSummaryEvent {
  type: 'summary';
  text: string;
  files: AgentFileChange[];
}
export interface AgentStatusEvent {
  type: 'status';
  status: 'running' | 'done' | 'error';
  message?: string;
}

// The subset the live feed renders in order. `summary` is pulled out of the
// feed and rendered as a terminal card, so it isn't part of this union.
export type AgentEvent =
  | AgentThoughtEvent
  | AgentActionEvent
  | AgentObservationEvent
  | AgentEditEvent
  | AgentStatusEvent;
