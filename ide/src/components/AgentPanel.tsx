import { useEffect, useMemo, useRef, useState } from 'react';
import { useStore } from '../store';
import type {
  AgentActionEvent,
  AgentEditEvent,
  AgentEvent,
  AgentFileChange,
} from '../types';
import './agent-panel.css';

// Cursor-Composer-style agent surface: describe a change, hit Run, and WATCH the
// agent think, read, search, and rewrite files live — with real unified diffs —
// then read a summary of everything it touched.

const PLACEHOLDER =
  "Describe a change… e.g. 'fix the SQL injection and hardcoded secrets in sample_repo'";

// --- action glyphs (SVG, matching the app's icon system — no icon-font dep) ---

function iconFor(tool: string) {
  switch (tool) {
    case 'read_file':
      return <ReadGlyph />;
    case 'list_dir':
      return <DirGlyph />;
    case 'search':
      return <SearchGlyph />;
    case 'edit_file':
      return <EditGlyph />;
    case 'create_file':
      return <PlusGlyph />;
    default:
      return <DotGlyph />;
  }
}

// Human line for an action, preferring the backend's own summary.
function actionLine(a: AgentActionEvent): string {
  if (a.summary) return a.summary;
  const t = a.target ?? '';
  switch (a.tool) {
    case 'read_file':
      return `Reading ${t}`;
    case 'list_dir':
      return `Listing ${t}`;
    case 'search':
      return `Searching ${t ? `"${t}"` : ''}`.trim();
    case 'edit_file':
      return `Editing ${t}`;
    case 'create_file':
      return `Creating ${t}`;
    default:
      return t || a.tool;
  }
}

function ReadGlyph() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="M4 2.5h5L12.5 6v7.5a1 1 0 0 1-1 1h-7.5a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
      <path d="M9 2.5V6h3.5M5.5 8.5h5M5.5 11h3.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function DirGlyph() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="M2 4.5A1 1 0 0 1 3 3.5h3l1.2 1.4H13a1 1 0 0 1 1 1v6.1a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-8Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}
function SearchGlyph() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <circle cx="7" cy="7" r="4" stroke="currentColor" strokeWidth="1.3" />
      <path d="m10 10 3.5 3.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}
function EditGlyph() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="M11 2.5 13.5 5 6 12.5l-3 .5.5-3L11 2.5Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}
function PlusGlyph() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="M8 3.5v9M3.5 8h9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}
function DotGlyph() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="2.5" fill="currentColor" />
    </svg>
  );
}

// --- unified-diff renderer ----------------------------------------------------

type DiffKind = 'add' | 'del' | 'hunk' | 'meta' | 'ctx';

function classifyDiffLine(line: string): DiffKind {
  if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ') || line.startsWith('index ')) {
    return 'meta';
  }
  if (line.startsWith('@@')) return 'hunk';
  if (line.startsWith('+')) return 'add';
  if (line.startsWith('-')) return 'del';
  return 'ctx';
}

function DiffView({ diff }: { diff: string }) {
  // Trim a single trailing newline so we don't render a phantom blank row.
  const lines = diff.replace(/\n$/, '').split('\n');
  return (
    <pre className="ag-diff" aria-label="Unified diff">
      {lines.map((line, i) => {
        const kind = classifyDiffLine(line);
        return (
          <code key={i} className={`ag-diff-line ag-diff-line--${kind}`}>
            {line || ' '}
          </code>
        );
      })}
    </pre>
  );
}

function DiffCard({ e, onOpen }: { e: AgentEditEvent; onOpen: (path: string) => void }) {
  const verb = e.change === 'created' ? 'Created' : 'Edited';
  return (
    <details className="ag-edit" open>
      <summary className="ag-edit-head">
        <span className={`ag-edit-badge ag-edit-badge--${e.change}`}>{verb}</span>
        <button
          type="button"
          className="ag-edit-path"
          onClick={(ev) => {
            ev.preventDefault();
            onOpen(e.path);
          }}
          title={`Open ${e.path}`}
        >
          {e.path}
        </button>
        <span className="ag-edit-stat">
          <span className="ag-stat-add">+{e.added}</span>
          <span className="ag-stat-del">-{e.removed}</span>
        </span>
      </summary>
      <DiffView diff={e.diff} />
    </details>
  );
}

// --- feed rows ----------------------------------------------------------------

function FeedRow({ e, onOpen }: { e: AgentEvent; onOpen: (path: string) => void }) {
  if (e.type === 'thought') {
    return <p className="ag-thought">{e.text}</p>;
  }
  if (e.type === 'action') {
    return (
      <div className="ag-action">
        <span className="ag-action-icon" aria-hidden="true">
          {iconFor(e.tool)}
        </span>
        <span className="ag-action-line">{actionLine(e)}</span>
      </div>
    );
  }
  if (e.type === 'observation') {
    return (
      <pre className="ag-observation">
        <code>{e.text}</code>
      </pre>
    );
  }
  if (e.type === 'edit') {
    return <DiffCard e={e} onOpen={onOpen} />;
  }
  // status
  return (
    <p className={`ag-status ag-status--${e.status}`}>
      {e.status === 'error'
        ? e.message || 'Agent run failed'
        : e.status === 'done'
          ? e.message || 'Done'
          : e.message || 'Running…'}
    </p>
  );
}

function ChangeRow({ f, onOpen }: { f: AgentFileChange; onOpen: (path: string) => void }) {
  return (
    <li className="ag-change">
      <button type="button" className="ag-change-btn" onClick={() => onOpen(f.path)} title={`Open ${f.path}`}>
        <span className={`ag-change-dot ag-change-dot--${f.change}`} aria-hidden="true" />
        <span className="ag-change-path">{f.path}</span>
        <span className="ag-change-stat">
          <span className="ag-stat-add">+{f.added}</span>
          <span className="ag-stat-del">-{f.removed}</span>
        </span>
      </button>
    </li>
  );
}

// --- panel --------------------------------------------------------------------

export default function AgentPanel() {
  const model = useStore((s) => s.model);
  const running = useStore((s) => s.agentRunning);
  const events = useStore((s) => s.agentEvents);
  const summary = useStore((s) => s.agentSummary);
  const runAgent = useStore((s) => s.runAgent);
  const stopAgent = useStore((s) => s.stopAgent);
  const openFile = useStore((s) => s.openFile);
  const setActivity = useStore((s) => s.setActivity);

  const [task, setTask] = useState('');
  const feedRef = useRef<HTMLDivElement | null>(null);

  // Follow the newest activity as events stream in.
  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events, summary]);

  const editCount = useMemo(
    () => events.filter((e) => e.type === 'edit').length,
    [events],
  );

  function openInEditor(path: string) {
    void openFile(path);
    setActivity('explorer');
  }

  function run() {
    const t = task.trim();
    if (!t || running) return;
    void runAgent(t);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Cmd/Ctrl+Enter runs — Enter alone stays a newline for multi-line tasks.
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      run();
    }
  }

  const hasFeed = events.length > 0 || !!summary;
  const canRun = task.trim().length > 0 && !running;

  return (
    <section className="ag" aria-label="Agent">
      <header className="ag-head">
        <div className="ag-head-title">
          <span className="ag-kicker">Agent</span>
          <span className="ag-sub">Composer</span>
        </div>
        <div className="ag-head-meta">
          {running && <span className="ag-live" aria-hidden="true" />}
          {editCount > 0 && (
            <span className="ag-editcount" title="Files changed this run">
              {editCount} {editCount === 1 ? 'edit' : 'edits'}
            </span>
          )}
          <span className="ag-model" title="Active model">
            {model || '—'}
          </span>
        </div>
      </header>

      <div className="ag-composer">
        <textarea
          className="ag-textarea"
          value={task}
          onChange={(e) => setTask(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={PLACEHOLDER}
          rows={3}
          disabled={running}
          aria-label="Describe a change for the agent"
        />
        <div className="ag-composer-row">
          <span className="ag-hint">⌘↵ to run</span>
          {running ? (
            <button type="button" className="ag-run ag-stop" onClick={() => void stopAgent()}>
              <span className="ag-stop-glyph" aria-hidden="true" />
              Stop
            </button>
          ) : (
            <button type="button" className="ag-run" onClick={run} disabled={!canRun} aria-label="Run agent">
              Run
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true">
                <path d="M7 5v14l11-7-11-7Z" fill="currentColor" />
              </svg>
            </button>
          )}
        </div>
      </div>

      <div className="ag-feed" ref={feedRef} aria-live="polite">
        {!hasFeed && (
          <div className="ag-empty">
            <div className="ag-empty-glyph" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none">
                <path d="m12 3 1.9 4.6L18.6 9l-4.7 1.4L12 15l-1.9-4.6L5.4 9l4.7-1.4L12 3Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
                <path d="M19 14.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
              </svg>
            </div>
            <p className="ag-empty-title">Put the agent to work</p>
            <p className="ag-empty-hint">
              Describe a change and watch it read, search, and rewrite files across your
              workspace — with a live diff for every edit.
            </p>
          </div>
        )}

        {events.map((e, i) => (
          <FeedRow key={i} e={e} onOpen={openInEditor} />
        ))}

        {running && (
          <div className="ag-working" aria-label="Agent is working">
            <span className="ag-working-dot" />
            <span className="ag-working-dot" />
            <span className="ag-working-dot" />
          </div>
        )}

        {summary && (
          <div className="ag-summary">
            <div className="ag-summary-head">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" aria-hidden="true">
                <path d="M5 12.5l4.2 4.2L19 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span>Summary</span>
            </div>
            <p className="ag-summary-text">{summary.text}</p>
            {summary.files.length > 0 && (
              <ul className="ag-changelist">
                {summary.files.map((f) => (
                  <ChangeRow key={f.path} f={f} onOpen={openInEditor} />
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
