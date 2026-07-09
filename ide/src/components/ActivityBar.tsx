import { useStore } from '../store';
import './activity-bar.css';

type Activity = 'explorer' | 'review' | 'chat' | 'agent';

// Inline stroke icons keep the shell self-contained (no icon-font network dep).
function ExplorerIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true">
      <path
        d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4l2 2.5H19.5A1.5 1.5 0 0 1 21 9v9.5A1.5 1.5 0 0 1 19.5 20h-15A1.5 1.5 0 0 1 3 18.5v-12Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ShieldIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true">
      <path
        d="M12 3 5 5.6v5.2c0 4.3 2.9 8 7 9.2 4.1-1.2 7-4.9 7-9.2V5.6L12 3Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path d="m9 12 2 2 4-4.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function AgentIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true">
      <path
        d="m12 3 1.9 4.6L18.6 9l-4.7 1.4L12 15l-1.9-4.6L5.4 9l4.7-1.4L12 3Z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path
        d="M18.5 14.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8.8-2Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ChatIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true">
      <path
        d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9A1.5 1.5 0 0 1 18.5 16H9l-4 4v-4H5.5A1.5 1.5 0 0 1 4 14.5v-9Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

interface Item {
  id: Activity;
  label: string;
  icon: JSX.Element;
}

const SIDEBAR_ITEMS: Item[] = [
  { id: 'explorer', label: 'Explorer', icon: <ExplorerIcon /> },
  { id: 'review', label: 'Review', icon: <ShieldIcon /> },
  { id: 'agent', label: 'Agent', icon: <AgentIcon /> },
];

export default function ActivityBar() {
  const activity = useStore((s) => s.activity);
  const setActivity = useStore((s) => s.setActivity);
  const chatOpen = useStore((s) => s.chatOpen);
  const toggleChat = useStore((s) => s.toggleChat);
  const findingsCount = useStore((s) => s.findings.length);
  const agentRunning = useStore((s) => s.agentRunning);

  return (
    <nav className="activity-bar" aria-label="Primary">
      {SIDEBAR_ITEMS.map((item) => (
        <button
          key={item.id}
          type="button"
          className={`ab-btn${activity === item.id ? ' is-active' : ''}`}
          aria-label={item.label}
          aria-pressed={activity === item.id}
          title={item.label}
          onClick={() => setActivity(item.id)}
        >
          {item.icon}
          {item.id === 'review' && findingsCount > 0 && (
            <span className="ab-badge" aria-hidden="true">
              {findingsCount > 99 ? '99+' : findingsCount}
            </span>
          )}
          {item.id === 'agent' && agentRunning && (
            <span className="ab-run-dot" aria-hidden="true" />
          )}
        </button>
      ))}

      <div className="ab-spacer" />

      <button
        type="button"
        className={`ab-btn${chatOpen ? ' is-active' : ''}`}
        aria-label="Toggle AI chat"
        aria-pressed={chatOpen}
        title="AI Chat"
        onClick={toggleChat}
      >
        <ChatIcon />
      </button>
    </nav>
  );
}
