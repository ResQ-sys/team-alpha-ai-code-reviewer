// API client. Everything hits the Vite dev proxy at /api -> http://localhost:8010.
// No host is hardcoded here so prod/dev share the same relative surface.
import type { AgentEvent, ChatMessage, FileNode, Workspace } from '../types';

const BASE = '/api';

// Optional API key, injected at build time (VITE_API_KEY). When the backend has
// API_KEY set, every state-changing call must carry X-API-Key or it 401s; in dev
// the backend is open-by-default so this is simply omitted.
const API_KEY = import.meta.env.VITE_API_KEY as string | undefined;

function authHeaders(): Record<string, string> {
  return API_KEY ? { 'X-API-Key': API_KEY } : {};
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}${detail ? `: ${detail}` : ''}`);
  }
  // Some endpoints (saveFile) return no body.
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export async function getModels(): Promise<string[]> {
  const data = await req<{ models: string[] }>('/models');
  return Array.isArray(data) ? (data as unknown as string[]) : (data?.models ?? []);
}

export interface PullProgress {
  status: string;
  percent?: number;
  error?: string;
}

/**
 * Pull an Ollama model by name, streaming download progress. Lets the user
 * bring any model, not just what is already installed. Resolves when the
 * stream completes (success or error frame).
 */
export function pullModel(
  name: string,
  onProgress: (p: PullProgress) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamSSE(
    '/models/pull',
    { name },
    (payload) => {
      const trimmed = payload.trim();
      if (!trimmed) return;
      try {
        onProgress(JSON.parse(trimmed) as PullProgress);
      } catch {
        // ignore non-JSON frames
      }
    },
    signal,
  );
}

export function getTree(root?: string): Promise<FileNode> {
  const qs = root ? `?root=${encodeURIComponent(root)}` : '';
  return req<FileNode>(`/fs/tree${qs}`);
}

export function getFile(
  path: string,
): Promise<{ path: string; content: string; language: string }> {
  return req(`/fs/file?path=${encodeURIComponent(path)}`);
}

export function saveFile(path: string, content: string): Promise<void> {
  return req<void>('/fs/file', {
    method: 'PUT',
    body: JSON.stringify({ path, content }),
  });
}

export function analyze(
  repoPath: string,
  model?: string,
): Promise<{ job_id: string }> {
  return req('/analyze', {
    method: 'POST',
    body: JSON.stringify(model ? { repo_path: repoPath, model } : { repo_path: repoPath }),
  });
}

export function getJob(jobId: string): Promise<any> {
  return req(`/jobs/${encodeURIComponent(jobId)}`);
}

export function applyFixes(jobId: string, dryRun: boolean): Promise<any> {
  return req('/apply', {
    method: 'POST',
    body: JSON.stringify({ job_id: jobId, dry_run: dryRun }),
  });
}

// --- SSE streaming ------------------------------------------------------------

/**
 * Robust SSE reader over fetch + ReadableStream. Parses standard
 * `data: <payload>\n\n` frames, tolerates multi-line data and CRLF, and stops
 * cleanly on a `[DONE]` sentinel. Each payload is passed to onFrame.
 */
async function streamSSE(
  path: string,
  body: unknown,
  onFrame: (payload: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...authHeaders(),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}${detail ? `: ${detail}` : ''}`);
  }

  await pumpSSE(res, onFrame);
}

/**
 * Drain a `text/event-stream` Response body, parsing `data: <payload>\n\n`
 * frames and invoking onFrame per payload. Stops on a `[DONE]` sentinel.
 * Shared by the POST (chat/edit) and GET (job events) stream helpers.
 */
async function pumpSSE(res: Response, onFrame: (payload: string) => void): Promise<void> {
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Frames are separated by a blank line. Normalize CRLF first.
      let sep: number;
      // eslint-disable-next-line no-cond-assign
      while ((sep = indexOfFrameBreak(buffer)) !== -1) {
        const rawFrame = buffer.slice(0, sep);
        buffer = buffer.slice(sep).replace(/^(\r?\n){2}/, '');

        const payload = extractData(rawFrame);
        if (payload === null) continue;
        if (payload === '[DONE]') return;
        onFrame(payload);
      }
    }
    // Flush any trailing frame with no terminating blank line.
    const payload = extractData(buffer);
    if (payload && payload !== '[DONE]') onFrame(payload);
  } finally {
    reader.releaseLock();
  }
}

function indexOfFrameBreak(buf: string): number {
  const lf = buf.indexOf('\n\n');
  const crlf = buf.indexOf('\r\n\r\n');
  if (lf === -1) return crlf;
  if (crlf === -1) return lf;
  return Math.min(lf, crlf);
}

// Concatenate every `data:` line in a frame (SSE allows multi-line data).
function extractData(frame: string): string | null {
  const lines = frame.split(/\r?\n/);
  const parts: string[] = [];
  for (const line of lines) {
    if (line.startsWith('data:')) {
      parts.push(line.slice(5).replace(/^ /, ''));
    }
  }
  return parts.length ? parts.join('\n') : null;
}

// Backends may stream raw text tokens or JSON like {"token":"..."} /
// {"delta":"..."} / {"content":"..."}. Normalize to a text chunk.
function frameToToken(payload: string): string {
  const trimmed = payload.trim();
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      const obj = JSON.parse(trimmed);
      const tok = obj.token ?? obj.delta ?? obj.content ?? obj.text;
      if (typeof tok === 'string') return tok;
    } catch {
      // fall through: treat as raw text
    }
  }
  return payload;
}

export function streamChat(
  messages: ChatMessage[],
  model: string,
  onToken: (t: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamSSE(
    '/chat',
    { messages, model },
    (payload) => onToken(frameToToken(payload)),
    signal,
  );
}

/**
 * Open GET /api/jobs/{jobId}/events and stream the live agent-activity SSE.
 * Each frame's JSON payload is parsed and handed to onEvent; malformed frames
 * are skipped so one bad frame can't kill the stream. Resolves on `[DONE]`,
 * stream end, or abort.
 */
export async function streamJobEvents(
  jobId: string,
  onEvent: (e: any) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/jobs/${encodeURIComponent(jobId)}/events`, {
    method: 'GET',
    headers: { Accept: 'text/event-stream', ...authHeaders() },
    signal,
  });
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}${detail ? `: ${detail}` : ''}`);
  }
  await pumpSSE(res, (payload) => {
    const trimmed = payload.trim();
    if (!trimmed) return;
    try {
      onEvent(JSON.parse(trimmed));
    } catch {
      // Ignore frames that aren't valid JSON.
    }
  });
}

export function streamEdit(
  code: string,
  instruction: string,
  language: string,
  model: string,
  onToken: (t: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamSSE(
    '/edit',
    { code, instruction, language, model },
    (payload) => onToken(frameToToken(payload)),
    signal,
  );
}

// --- Workspace ----------------------------------------------------------------

// Current active workspace root the fs/tree, fs/file, and agent all operate on.
export function getWorkspace(): Promise<Workspace> {
  return req<Workspace>('/workspace');
}

// Point the backend at a different local repo root. Throws on a bad path.
export function setWorkspace(path: string): Promise<Workspace> {
  return req<Workspace>('/workspace', {
    method: 'POST',
    body: JSON.stringify({ path }),
  });
}

// --- Agent mode ---------------------------------------------------------------

// Kick off an agent run over the active workspace; returns its run id.
export function startAgent(task: string, model?: string): Promise<{ run_id: string }> {
  return req('/agent', {
    method: 'POST',
    body: JSON.stringify(model ? { task, model } : { task }),
  });
}

// Cancel an in-flight agent run.
export function stopAgent(runId: string): Promise<{ ok: boolean }> {
  return req(`/agent/${encodeURIComponent(runId)}/stop`, { method: 'POST' });
}

/**
 * Open GET /api/agent/{runId}/events and stream the live agent run. Each frame's
 * JSON payload is parsed and handed to onEvent; malformed frames are skipped so
 * one bad frame can't kill the stream. Resolves on `[DONE]`, end, or abort.
 * Mirrors streamJobEvents so both live timelines share the same SSE plumbing.
 */
export async function streamAgentEvents(
  runId: string,
  onEvent: (e: AgentEvent | { type: string; [k: string]: unknown }) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/agent/${encodeURIComponent(runId)}/events`, {
    method: 'GET',
    headers: { Accept: 'text/event-stream', ...authHeaders() },
    signal,
  });
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}${detail ? `: ${detail}` : ''}`);
  }
  await pumpSSE(res, (payload) => {
    const trimmed = payload.trim();
    if (!trimmed) return;
    try {
      onEvent(JSON.parse(trimmed));
    } catch {
      // Ignore frames that aren't valid JSON.
    }
  });
}
