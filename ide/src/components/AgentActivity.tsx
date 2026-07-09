import { useMemo } from 'react';
import { useStore } from '../store';
import type { AgentStep } from '../types';
import './agent-activity.css';

// Live, Cursor-style timeline of the 9-agent security pipeline. Each agent
// lights up as it runs, reports its finding, and passes the baton to the next.
// State is fed by the SSE stream wired up in the store (agents + agentLog).

function DotGlyph() {
  return <span className="aa-glyph aa-glyph--pending" aria-hidden="true" />;
}

function SpinnerGlyph() {
  return (
    <span className="aa-glyph aa-glyph--running" aria-hidden="true">
      <span className="aa-spinner" />
    </span>
  );
}

function CheckGlyph() {
  return (
    <span className="aa-glyph aa-glyph--done" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="12" height="12" fill="none">
        <path
          d="m5 12.5 4.2 4.2L19 7"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}

function Glyph({ status }: { status: AgentStep['status'] }) {
  if (status === 'done') return <CheckGlyph />;
  if (status === 'running') return <SpinnerGlyph />;
  return <DotGlyph />;
}

const STATUS_LABEL: Record<AgentStep['status'], string> = {
  pending: 'pending',
  running: 'running',
  done: 'done',
};

export default function AgentActivity() {
  const agents = useStore((s) => s.agents);
  const agentLog = useStore((s) => s.agentLog);
  const model = useStore((s) => s.model);
  const reviewStatus = useStore((s) => s.reviewStatus);

  const doneCount = useMemo(
    () => agents.filter((a) => a.status === 'done').length,
    [agents],
  );
  const activeNode = useMemo(
    () => agents.find((a) => a.status === 'running')?.node ?? null,
    [agents],
  );

  const recentLog = agentLog.slice(-3);

  return (
    <section className="aa" aria-label="Agent activity">
      <header className="aa-head">
        <div className="aa-head-row">
          <span className="aa-kicker">Agent Pipeline</span>
          <span className="aa-progress" aria-label={`${doneCount} of ${agents.length} agents complete`}>
            {doneCount}/{agents.length}
          </span>
        </div>
        {model && (
          <span className="aa-model" title="Reviewing model">
            <span className="aa-model-dot" aria-hidden="true" />
            {model}
          </span>
        )}
      </header>

      <ol className="aa-list" aria-live="polite">
        {agents.map((a, i) => {
          const isActive = a.status === 'running';
          const isLast = i === agents.length - 1;
          return (
            <li
              key={a.node}
              className={`aa-step aa-step--${a.status}${isActive ? ' is-active' : ''}`}
            >
              <div className="aa-rail" aria-hidden="true">
                <Glyph status={a.status} />
                {!isLast && <span className="aa-connector" />}
              </div>

              <div className="aa-body">
                <div className="aa-label-row">
                  <span className="aa-label">{a.label}</span>
                  <span className="aa-status-tag">{STATUS_LABEL[a.status]}</span>
                </div>

                {a.status === 'done' && a.detail && (
                  <span className="aa-detail">{a.detail}</span>
                )}

                {isActive && recentLog.length > 0 && (
                  <ul className="aa-log">
                    {recentLog.map((line, j) => (
                      <li key={`${line}-${j}`} className="aa-log-line">
                        {line}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      {reviewStatus === 'running' && !activeNode && doneCount === 0 && (
        <p className="aa-warming">Warming up the pipeline…</p>
      )}
    </section>
  );
}
