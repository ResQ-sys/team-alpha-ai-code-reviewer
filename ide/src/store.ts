// Global app store (zustand). Panels read state via useStore and call actions.
// App places components with NO props, so all shared state lives here.
import { createStore, type StoreApi } from 'zustand/vanilla';
import { useStore as useZustand } from 'zustand';
import type {
  AgentEvent,
  AgentStep,
  AgentSummaryEvent,
  Finding,
  FileNode,
  OpenFile,
  Workspace,
} from './types';
import * as api from './lib/api';

type Activity = 'explorer' | 'review' | 'chat' | 'agent';
type ReviewStatus = 'idle' | 'running' | 'done' | 'error';

export interface AppState {
  // file tree + editor
  tree: FileNode | null;
  loadTree: () => Promise<void>;
  open: OpenFile[];
  activePath: string | null;
  openFile: (path: string) => Promise<void>;
  closeFile: (path: string) => void;
  setActive: (path: string) => void;
  updateContent: (path: string, content: string) => void;
  saveActive: () => Promise<void>;

  // shell chrome
  activity: Activity;
  setActivity: (a: Activity) => void;
  chatOpen: boolean;
  toggleChat: () => void;

  // models
  model: string;
  models: string[];
  setModel: (m: string) => void;
  loadModels: () => Promise<void>;
  pulling: boolean;
  pullProgress: string;
  pullModel: (name: string) => Promise<void>;

  // review
  findings: Finding[];
  jobId: string | null;
  reviewStatus: ReviewStatus;
  agents: AgentStep[];
  agentLog: string[];
  runReview: () => Promise<void>;
  // Apply the whole job's fix set in one call (the backend /api/apply is
  // batch-only). Clears the findings list on success. Throws on failure.
  applyAllFixes: () => Promise<void>;

  // workspace (which local repo the fs + agent operate on)
  workspace: Workspace | null;
  loadWorkspace: () => Promise<void>;
  openWorkspace: (path: string) => Promise<void>;

  // agent mode (Cursor-Composer-style run over the active workspace)
  agentRunning: boolean;
  agentRunId: string | null;
  agentEvents: AgentEvent[];
  agentSummary: AgentSummaryEvent | null;
  runAgent: (task: string) => Promise<void>;
  stopAgent: () => Promise<void>;
}

const REPO_ROOT = '.';
const POLL_MS = 1200;
const MAX_LOG = 40;

// The 9 pipeline agents, in execution order. Node ids + labels are the shared
// backend<->frontend contract; a run seeds this list all-'pending'.
const AGENT_PIPELINE: ReadonlyArray<Pick<AgentStep, 'node' | 'label'>> = [
  { node: 'ingestion', label: 'Ingestion' },
  { node: 'static_analysis', label: 'Static Analysis (Semgrep)' },
  { node: 'llm_review', label: 'Code-LLM Review' },
  { node: 'vulnerability', label: 'Vulnerability Merge' },
  { node: 'rag', label: 'Secure-Coding RAG' },
  { node: 'review_generation', label: 'Fix Generation' },
  { node: 'verifier', label: 'Verifier / Self-Reflection' },
  { node: 'approval', label: 'Human-Approval Gate' },
  { node: 'report', label: 'Report' },
];

function seedAgents(): AgentStep[] {
  return AGENT_PIPELINE.map((a) => ({ ...a, status: 'pending' as const }));
}

function langFromPath(path: string): string {
  const ext = path.slice(path.lastIndexOf('.') + 1).toLowerCase();
  const map: Record<string, string> = {
    ts: 'typescript',
    tsx: 'typescript',
    js: 'javascript',
    jsx: 'javascript',
    py: 'python',
    rs: 'rust',
    go: 'go',
    java: 'java',
    rb: 'ruby',
    php: 'php',
    c: 'c',
    h: 'c',
    cpp: 'cpp',
    hpp: 'cpp',
    cs: 'csharp',
    json: 'json',
    css: 'css',
    html: 'html',
    md: 'markdown',
    yml: 'yaml',
    yaml: 'yaml',
    sh: 'shell',
    sql: 'sql',
    toml: 'toml',
  };
  return map[ext] ?? 'plaintext';
}

// The backend job record carries findings under `report`, not a top-level
// `findings` key: the security findings live in `bug_and_vulnerability_findings`
// and their generated fixes in `suggested_fixes`, joined by `finding_ref`
// (`file:line:rule_id`). Flatten that into the flat Finding shape the panels
// render, attaching each finding's fixed_code when a fix exists.
function mapReportFindings(report: any): Finding[] {
  if (!report || typeof report !== 'object') return [];
  const raw: any[] = Array.isArray(report.bug_and_vulnerability_findings)
    ? report.bug_and_vulnerability_findings
    : [];
  const fixes: any[] = Array.isArray(report.suggested_fixes)
    ? report.suggested_fixes
    : [];

  const fixByRef = new Map<string, any>();
  for (const fx of fixes) {
    if (fx && typeof fx.finding_ref === 'string') fixByRef.set(fx.finding_ref, fx);
  }

  return raw.map((r) => {
    const file = typeof r?.file === 'string' ? r.file : '';
    const line = typeof r?.line === 'number' ? r.line : 0;
    const rule_id = typeof r?.rule_id === 'string' ? r.rule_id : '';
    const ref = `${file}:${line}:${rule_id}`;
    const fix = fixByRef.get(ref);
    return {
      file,
      line,
      severity: typeof r?.severity === 'string' ? r.severity : 'info',
      rule_id,
      message: typeof r?.message === 'string' ? r.message : '',
      cwe: Array.isArray(r?.cwe) ? r.cwe : [],
      fixed_code: fix?.fixed_code ? String(fix.fixed_code) : undefined,
      key: ref,
    };
  });
}

type SetState = StoreApi<AppState>['setState'];
type GetState = StoreApi<AppState>['getState'];

// Fold one live SSE event into the store: advance the matching agent's
// status/detail, or append a fine-grained log line. Unknown shapes are ignored.
function handleAgentEvent(set: SetState, get: GetState) {
  return (e: any): void => {
    if (!e || typeof e !== 'object') return;

    if (e.type === 'agent' && typeof e.node === 'string') {
      const status: AgentStep['status'] = e.status === 'done' ? 'done' : 'running';
      set({
        agents: get().agents.map((a) =>
          a.node === e.node
            ? {
                ...a,
                status,
                label: typeof e.label === 'string' ? e.label : a.label,
                detail: typeof e.detail === 'string' ? e.detail : a.detail,
              }
            : a,
        ),
      });
      return;
    }

    if (e.type === 'log' && typeof e.message === 'string') {
      const line = e.node ? `${e.node}: ${e.message}` : e.message;
      set({ agentLog: [...get().agentLog, line].slice(-MAX_LOG) });
    }
  };
}

// Holds the in-flight agent stream so stopAgent() can abort the reader. Lives at
// module scope (not in state) since it's imperative plumbing, not rendered data.
let agentAbort: AbortController | null = null;

// Same, for the review job-events stream: lets a re-triggered review abort the
// previous SSE connection so a stale handleAgentEvent closure can't write into a
// fresh run, and so the old connection doesn't linger until the server deadline.
let reviewAbort: AbortController | null = null;

// After an agent edit lands: refresh the file's buffer if it's open, and reload
// the tree when a new file was created so it appears in the Explorer. Best-effort.
async function refreshAfterEdit(
  path: string,
  change: 'edited' | 'created',
  set: SetState,
  get: GetState,
): Promise<void> {
  if (change === 'created') {
    await get().loadTree().catch(() => {});
  }
  const openFile = get().open.find((o) => o.path === path);
  if (!openFile) return;
  try {
    const { content } = await api.getFile(path);
    set((s) => ({
      open: s.open.map((o) =>
        o.path === path ? { ...o, content, dirty: false } : o,
      ),
    }));
  } catch {
    // leave the buffer as-is if refresh fails
  }
}

// Fold one live agent SSE frame into the store. Feed events are appended in
// order; `summary` is lifted out into agentSummary; terminal statuses stop the
// run. Unknown shapes are ignored so one odd frame can't corrupt the feed.
function foldAgentEvent(set: SetState, get: GetState) {
  return (e: AgentEvent | { type: string; [k: string]: unknown }): void => {
    if (!e || typeof e !== 'object' || typeof (e as { type?: unknown }).type !== 'string') {
      return;
    }
    const ev = e as AgentEvent | AgentSummaryEvent;

    if (ev.type === 'summary') {
      set({ agentSummary: ev });
      return;
    }

    if (ev.type === 'status') {
      if (ev.status === 'done' || ev.status === 'error') set({ agentRunning: false });
      set({ agentEvents: [...get().agentEvents, ev] });
      return;
    }

    if (
      ev.type === 'thought' ||
      ev.type === 'action' ||
      ev.type === 'observation' ||
      ev.type === 'edit'
    ) {
      set({ agentEvents: [...get().agentEvents, ev] });
      if (ev.type === 'edit') void refreshAfterEdit(ev.path, ev.change, set, get);
    }
  };
}

// Poll a job to a terminal state, streaming findings in as they appear and
// flipping reviewStatus to done/error. Owns the authoritative run outcome.
async function pollJob(jobId: string, set: SetState): Promise<void> {
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const job = await api.getJob(jobId);
    const status: string = job?.status ?? '';
    const findings: Finding[] = mapReportFindings(job?.report);
    if (findings.length) set({ findings });

    if (status === 'done' || status === 'completed' || status === 'succeeded') {
      set({ findings, reviewStatus: 'done' });
      return;
    }
    if (status === 'error' || status === 'failed') {
      set({ reviewStatus: 'error' });
      return;
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}

export const store = createStore<AppState>((set, get) => ({
  tree: null,
  async loadTree() {
    const tree = await api.getTree(REPO_ROOT);
    set({ tree });
  },

  open: [],
  activePath: null,

  async openFile(path) {
    const existing = get().open.find((f) => f.path === path);
    if (existing) {
      set({ activePath: path });
      return;
    }
    const { content, language } = await api.getFile(path);
    const name = path.slice(path.lastIndexOf('/') + 1);
    const file: OpenFile = {
      path,
      name,
      language: language || langFromPath(path),
      content,
      dirty: false,
    };
    set((s) => ({ open: [...s.open, file], activePath: path }));
  },

  closeFile(path) {
    set((s) => {
      const open = s.open.filter((f) => f.path !== path);
      let activePath = s.activePath;
      if (activePath === path) {
        activePath = open.length ? open[open.length - 1].path : null;
      }
      return { open, activePath };
    });
  },

  setActive(path) {
    set({ activePath: path });
  },

  updateContent(path, content) {
    set((s) => ({
      open: s.open.map((f) =>
        f.path === path ? { ...f, content, dirty: true } : f,
      ),
    }));
  },

  async saveActive() {
    const { open, activePath } = get();
    if (!activePath) return;
    const file = open.find((f) => f.path === activePath);
    if (!file || !file.dirty) return;
    await api.saveFile(file.path, file.content);
    set((s) => ({
      open: s.open.map((f) =>
        f.path === file.path ? { ...f, dirty: false } : f,
      ),
    }));
  },

  activity: 'explorer',
  setActivity(a) {
    set({ activity: a });
  },

  chatOpen: false,
  toggleChat() {
    set((s) => ({ chatOpen: !s.chatOpen }));
  },

  model: '',
  models: [],
  setModel(m) {
    set({ model: m });
  },
  async loadModels() {
    const models = await api.getModels();
    set((s) => ({ models, model: s.model || models[0] || '' }));
  },
  pulling: false,
  pullProgress: '',
  async pullModel(name) {
    const target = name.trim();
    if (!target || get().pulling) return;
    set({ pulling: true, pullProgress: 'starting…' });
    try {
      await api.pullModel(target, (p) => {
        const pct = typeof p.percent === 'number' ? ` ${p.percent}%` : '';
        set({ pullProgress: p.error ? 'pull failed' : `${p.status}${pct}` });
      });
      const models = await api.getModels();
      set({ models, model: target, pulling: false, pullProgress: '' });
    } catch {
      set({ pulling: false, pullProgress: 'pull failed' });
    }
  },

  findings: [],
  jobId: null,
  reviewStatus: 'idle',
  agents: seedAgents(),
  agentLog: [],

  async runReview() {
    // Abort any prior review stream so its handler can't fold events into this
    // fresh run.
    reviewAbort?.abort();
    const controller = new AbortController();
    reviewAbort = controller;

    set({
      reviewStatus: 'running',
      findings: [],
      agents: seedAgents(),
      agentLog: [],
      activity: 'review',
    });
    try {
      // Send the picked model so the pipeline runs what the UI advertises.
      const { job_id } = await api.analyze(REPO_ROOT, get().model || undefined);
      set({ jobId: job_id });

      // (a) Live agent-activity stream — best-effort. It may end before or
      // after polling finishes; either way the poller owns the terminal state.
      const streamDone = api
        .streamJobEvents(job_id, handleAgentEvent(set, get), controller.signal)
        .catch(() => {
          // Stream is decorative; a dropped/aborted stream must not fail the run.
        });

      // (b) Poll the job until it reaches a terminal state.
      const pollDone = pollJob(job_id, set);

      // Polling is authoritative. Once it resolves, tear down the now-decorative
      // stream promptly instead of leaving it open until the server deadline.
      await pollDone;
      controller.abort();
      await streamDone;
    } catch {
      set({ reviewStatus: 'error' });
    } finally {
      if (reviewAbort === controller) reviewAbort = null;
    }
  },

  async applyAllFixes() {
    const { jobId } = get();
    if (!jobId) return;
    await api.applyFixes(jobId, false);
    // The whole job's fix set is now written to disk, so every remaining
    // finding is stale — clear the list rather than dropping a single row.
    const touched = get().findings.map((f) => f.file);
    set({ findings: [] });
    // Refresh any open buffers whose file may have been rewritten.
    const openPaths = new Set(get().open.map((o) => o.path));
    const toRefresh = [...new Set(touched)].filter((p) => openPaths.has(p));
    for (const path of toRefresh) {
      try {
        const { content } = await api.getFile(path);
        set((s) => ({
          open: s.open.map((o) =>
            o.path === path ? { ...o, content, dirty: false } : o,
          ),
        }));
      } catch {
        // leave the buffer as-is if refresh fails
      }
    }
  },

  // --- workspace --------------------------------------------------------------
  workspace: null,

  async loadWorkspace() {
    try {
      const workspace = await api.getWorkspace();
      set({ workspace });
    } catch {
      // No active workspace yet is fine; the control shows a fallback name.
    }
  },

  async openWorkspace(path) {
    const target = path.trim();
    if (!target) return;
    // Throws on a missing/invalid dir — callers surface the message.
    const workspace = await api.setWorkspace(target);
    // New root: drop every open buffer and the stale tree, then reload.
    set({ workspace, open: [], activePath: null, tree: null });
    await get().loadTree();
  },

  // --- agent mode -------------------------------------------------------------
  agentRunning: false,
  agentRunId: null,
  agentEvents: [],
  agentSummary: null,

  async runAgent(task) {
    const t = task.trim();
    if (!t || get().agentRunning) return;

    // Reset the feed for a fresh run and abort any stale stream.
    agentAbort?.abort();
    const controller = new AbortController();
    agentAbort = controller;
    set({
      agentRunning: true,
      agentRunId: null,
      agentEvents: [],
      agentSummary: null,
      activity: 'agent',
    });

    try {
      const { run_id } = await api.startAgent(t, get().model || undefined);
      set({ agentRunId: run_id });
      await api.streamAgentEvents(run_id, foldAgentEvent(set, get), controller.signal);
    } catch (err) {
      if (!controller.signal.aborted) {
        const message = err instanceof Error ? err.message : 'Agent run failed';
        set({
          agentEvents: [
            ...get().agentEvents,
            { type: 'status', status: 'error', message },
          ],
        });
      }
    } finally {
      set({ agentRunning: false });
      if (agentAbort === controller) agentAbort = null;
    }
  },

  async stopAgent() {
    const { agentRunId } = get();
    agentAbort?.abort();
    agentAbort = null;
    set({ agentRunning: false });
    if (agentRunId) {
      try {
        await api.stopAgent(agentRunId);
      } catch {
        // Cancel is best-effort; the stream is already torn down locally.
      }
    }
  },
}));

export function useStore<T>(selector: (s: AppState) => T): T {
  return useZustand(store, selector);
}
