import { useEffect, useMemo, useRef, useState } from 'react';
import { useStore } from '../store';
import * as api from '../lib/api';
import type { Finding } from '../types';
import AgentActivity from './AgentActivity';
import './problems-panel.css';

// Editor owner listens for this to scroll/highlight the finding's line.
// Decoupled the same way App exposes INLINE_EDIT_EVENT — the shell never
// reaches into Monaco internals.
export const REVEAL_LINE_EVENT = 'ide:reveal-line';

const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low'] as const;
type Sev = (typeof SEVERITY_ORDER)[number];

// Map any backend severity string onto a token color + rank. Unknown values
// fall through to a neutral "info" bucket so nothing is dropped.
function normSeverity(raw: string | undefined | null): Sev | 'info' {
  const s = String(raw ?? '').toLowerCase();
  if (s.startsWith('crit')) return 'critical';
  if (s.startsWith('high') || s === 'error') return 'high';
  if (s.startsWith('med') || s === 'warning' || s === 'warn') return 'medium';
  if (s.startsWith('low') || s === 'info' || s === 'note') return 'low';
  return 'info';
}

const SEV_COLOR: Record<Sev | 'info', string> = {
  critical: 'var(--sev-critical)',
  high: 'var(--sev-high)',
  medium: 'var(--sev-medium)',
  low: 'var(--sev-low)',
  info: 'var(--text-faint)',
};

function findingKey(f: Finding): string {
  return f.key ?? `${f.file}:${f.line}:${f.rule_id}`;
}

function splitPath(path: string): { dir: string; name: string } {
  const i = path.lastIndexOf('/');
  return i === -1
    ? { dir: '', name: path }
    : { dir: path.slice(0, i + 1), name: path.slice(i + 1) };
}

interface Group {
  file: string;
  items: Finding[];
  worst: Sev | 'info';
}

// Group findings by file and rank each file by its most severe finding so the
// scariest files float to the top — like VS Code's Problems tree, but ordered.
function groupByFile(findings: Finding[]): Group[] {
  const map = new Map<string, Finding[]>();
  for (const f of findings) {
    const arr = map.get(f.file);
    if (arr) arr.push(f);
    else map.set(f.file, [f]);
  }
  const rank = (s: Sev | 'info') =>
    s === 'info' ? SEVERITY_ORDER.length : SEVERITY_ORDER.indexOf(s);
  const groups: Group[] = [];
  for (const [file, items] of map) {
    const sorted = [...items].sort(
      (a, b) => rank(normSeverity(a.severity)) - rank(normSeverity(b.severity)),
    );
    groups.push({ file, items: sorted, worst: normSeverity(sorted[0].severity) });
  }
  groups.sort((a, b) => rank(a.worst) - rank(b.worst) || a.file.localeCompare(b.file));
  return groups;
}

// Optional quality score lives on the job report, which the store doesn't hold.
// Pull it directly when a review finishes.
function useQualityScore(jobId: string | null, done: boolean): number | null {
  const [score, setScore] = useState<number | null>(null);
  useEffect(() => {
    setScore(null);
    if (!jobId || !done) return;
    let cancelled = false;
    void api
      .getJob(jobId)
      .then((job) => {
        if (cancelled) return;
        const raw =
          job?.report?.quality_score ??
          job?.quality_score ??
          job?.report?.score ??
          job?.score;
        if (typeof raw === 'number' && Number.isFinite(raw)) setScore(raw);
      })
      .catch(() => {
        /* score is decorative; ignore failures */
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, done]);
  return score;
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true">
      <path
        d="M8 5.5v13l11-6.5-11-6.5Z"
        fill="currentColor"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Chevron() {
  return (
    <svg
      className="pr-chevron"
      viewBox="0 0 24 24"
      width="14"
      height="14"
      fill="none"
      aria-hidden="true"
    >
      <path d="m9 6 6 6-6 6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

type FixState = 'idle' | 'applying' | 'ok' | 'err';

export default function ProblemsPanel() {
  const findings = useStore((s) => s.findings);
  const reviewStatus = useStore((s) => s.reviewStatus);
  const jobId = useStore((s) => s.jobId);
  const runReview = useStore((s) => s.runReview);
  const applyAllFixes = useStore((s) => s.applyAllFixes);
  const openFile = useStore((s) => s.openFile);

  const running = reviewStatus === 'running';
  const done = reviewStatus === 'done';

  const score = useQualityScore(jobId, done);
  const groups = useMemo(() => groupByFile(findings), [findings]);

  const counts = useMemo(() => {
    const c: Record<Sev | 'info', number> = {
      critical: 0,
      high: 0,
      medium: 0,
      low: 0,
      info: 0,
    };
    for (const f of findings) c[normSeverity(f.severity)] += 1;
    return c;
  }, [findings]);

  // Collapsed file paths (default: all expanded).
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggleGroup = (file: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      next.has(file) ? next.delete(file) : next.add(file);
      return next;
    });

  // Single job-level Apply feedback. The backend /api/apply is batch-only, so a
  // per-row button would silently rewrite every file the job touched; instead we
  // expose one "Apply all fixes" action over the whole job.
  const [applyState, setApplyState] = useState<FixState>('idle');
  const timers = useRef<number[]>([]);
  useEffect(
    () => () => {
      timers.current.forEach((t) => window.clearTimeout(t));
    },
    [],
  );

  const fixableCount = useMemo(
    () => findings.filter((f) => !!f.fixed_code).length,
    [findings],
  );

  async function openFinding(f: Finding) {
    try {
      await openFile(f.file);
      window.dispatchEvent(
        new CustomEvent(REVEAL_LINE_EVENT, {
          detail: { path: f.file, line: f.line },
        }),
      );
    } catch {
      /* opening is best-effort; a failed open shouldn't break the panel */
    }
  }

  async function onApplyAll() {
    if (applyState === 'applying') return;
    setApplyState('applying');
    try {
      await applyAllFixes();
      setApplyState('ok');
    } catch {
      setApplyState('err');
      timers.current.push(
        window.setTimeout(() => setApplyState('idle'), 2600),
      );
    }
  }

  return (
    <section className="problems" aria-label="Security review">
      <div className="pr-head">
        <div className="pr-title-row">
          <span className="pr-title">Security Review</span>
          {score !== null && (
            <span className="pr-score" title="Overall quality score">
              <b>{Math.round(score)}</b>/100
            </span>
          )}
        </div>

        <button
          type="button"
          className="pr-run"
          onClick={() => void runReview()}
          disabled={running}
          aria-label="Run security review"
        >
          {running ? (
            <>
              <span className="pr-spinner" style={{ width: 14, height: 14 }} aria-hidden="true" />
              Analyzing…
            </>
          ) : (
            <>
              <PlayIcon />
              Run Security Review
            </>
          )}
        </button>

        {findings.length > 0 && (
          <div className="pr-summary" aria-label="Findings by severity">
            {SEVERITY_ORDER.map((sev) =>
              counts[sev] > 0 ? (
                <span key={sev} className="pr-pill" title={`${counts[sev]} ${sev}`}>
                  <span
                    className="pr-dot"
                    style={{ ['--sev-color' as string]: SEV_COLOR[sev], marginTop: 0 }}
                    aria-hidden="true"
                  />
                  <b>{counts[sev]}</b> {sev}
                </span>
              ) : null,
            )}
            {counts.info > 0 && (
              <span className="pr-pill" title={`${counts.info} info`}>
                <b>{counts.info}</b> info
              </span>
            )}
          </div>
        )}

        {done && fixableCount > 0 && (
          <button
            type="button"
            className={`pr-apply-all${
              applyState === 'ok'
                ? ' pr-apply-all--ok'
                : applyState === 'err'
                  ? ' pr-apply-all--err'
                  : ''
            }`}
            onClick={() => void onApplyAll()}
            disabled={applyState === 'applying' || applyState === 'ok'}
            aria-label={`Apply all ${fixableCount} suggested fixes`}
          >
            {applyState === 'applying'
              ? 'Applying…'
              : applyState === 'ok'
                ? 'Applied ✓'
                : applyState === 'err'
                  ? 'Failed — retry'
                  : `Apply all fixes (${fixableCount})`}
          </button>
        )}
      </div>

      <div className="pr-body">
        {/* Once a run has started, watch the agents work in a live timeline. */}
        {reviewStatus !== 'idle' && <AgentActivity />}

        {reviewStatus === 'error' && (
          <div className="pr-state pr-state--error">
            Review failed. Check the backend and try again.
          </div>
        )}

        {reviewStatus === 'idle' && (
          <div className="pr-state">
            Run a security review to surface vulnerabilities across the workspace.
          </div>
        )}

        {done && findings.length === 0 && (
          <div className="pr-state">No issues found. The workspace is clean.</div>
        )}

        {groups.map((g) => {
          const { dir, name } = splitPath(g.file);
          const isOpen = !collapsed.has(g.file);
          return (
            <div key={g.file} className={`pr-group${isOpen ? ' is-open' : ''}`}>
              <div
                className="pr-group-head"
                role="button"
                tabIndex={0}
                aria-expanded={isOpen}
                onClick={() => toggleGroup(g.file)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    toggleGroup(g.file);
                  }
                }}
              >
                <Chevron />
                <span className="pr-file" title={g.file}>
                  {dir && <span className="pr-dir">{dir}</span>}
                  {name}
                </span>
                <span className="pr-count">{g.items.length}</span>
              </div>

              {isOpen &&
                g.items.map((f) => {
                  const key = findingKey(f);
                  const sev = normSeverity(f.severity);
                  return (
                    <div
                      key={key}
                      className="pr-finding"
                      role="button"
                      tabIndex={0}
                      onClick={() => void openFinding(f)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          void openFinding(f);
                        }
                      }}
                    >
                      <span
                        className="pr-dot"
                        style={{ ['--sev-color' as string]: SEV_COLOR[sev] }}
                        aria-hidden="true"
                      />
                      <div className="pr-main">
                        <span className="pr-msg">{f.message}</span>
                        <span className="pr-meta">
                          <span
                            className="pr-rule"
                            style={{ ['--sev-color' as string]: SEV_COLOR[sev] }}
                          >
                            {f.rule_id}
                          </span>
                          <span className="pr-line">Ln {f.line}</span>
                          {f.cwe?.map((c) => (
                            <span key={c} className="pr-cwe">
                              {c}
                            </span>
                          ))}
                        </span>
                      </div>

                      {f.fixed_code && (
                        <span className="pr-fix-badge" title="A suggested fix is available">
                          Fix ready
                        </span>
                      )}
                    </div>
                  );
                })}
            </div>
          );
        })}
      </div>
    </section>
  );
}
