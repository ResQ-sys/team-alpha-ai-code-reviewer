import { useStore } from '../store';
import ModelPicker from './ModelPicker';
import './status-bar.css';

// Thin bottom bar: review state + findings count (left), model + cursor (right).
export default function StatusBar() {
  const reviewStatus = useStore((s) => s.reviewStatus);
  const findingsCount = useStore((s) => s.findings.length);
  const activePath = useStore((s) => s.activePath);
  const dirty = useStore((s) =>
    s.open.some((f) => f.path === s.activePath && f.dirty),
  );

  const statusLabel: Record<string, string> = {
    idle: 'Ready',
    running: 'Reviewing…',
    done: 'Review complete',
    error: 'Review failed',
  };

  return (
    <footer className="status-bar" aria-label="Status">
      <div className="sb-left">
        <span className={`sb-item sb-status sb-status--${reviewStatus}`}>
          <span className="sb-dot" aria-hidden="true" />
          {statusLabel[reviewStatus] ?? 'Ready'}
        </span>
        <span className="sb-item">
          {findingsCount} {findingsCount === 1 ? 'finding' : 'findings'}
        </span>
      </div>

      <div className="sb-right">
        {activePath && (
          <span className="sb-item sb-path" title={activePath}>
            {dirty ? '● ' : ''}
            {activePath}
          </span>
        )}
        <ModelPicker />
      </div>
    </footer>
  );
}
