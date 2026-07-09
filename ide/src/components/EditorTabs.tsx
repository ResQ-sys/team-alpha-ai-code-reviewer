import type { MouseEvent } from 'react';
import { useStore } from '../store';
import './editor-tabs.css';

export default function EditorTabs() {
  const open = useStore((s) => s.open);
  const activePath = useStore((s) => s.activePath);
  const setActive = useStore((s) => s.setActive);
  const closeFile = useStore((s) => s.closeFile);

  if (open.length === 0) return <div className="editor-tabs is-empty" aria-hidden="true" />;

  function onClose(e: MouseEvent, path: string) {
    // Don't let the close click also select the tab.
    e.stopPropagation();
    closeFile(path);
  }

  return (
    <div className="editor-tabs" role="tablist" aria-label="Open editors">
      {open.map((file) => {
        const isActive = file.path === activePath;
        return (
          <div
            key={file.path}
            role="tab"
            tabIndex={0}
            aria-selected={isActive}
            className={`et-tab${isActive ? ' is-active' : ''}${file.dirty ? ' is-dirty' : ''}`}
            title={file.path}
            onClick={() => setActive(file.path)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setActive(file.path);
              }
            }}
          >
            <span className="et-name">{file.name}</span>

            <button
              type="button"
              className="et-close"
              aria-label={`Close ${file.name}${file.dirty ? ' (unsaved changes)' : ''}`}
              title="Close"
              onClick={(e) => onClose(e, file.path)}
            >
              <span className="et-dirty-dot" aria-hidden="true" />
              <svg className="et-close-x" viewBox="0 0 16 16" width="13" height="13" fill="none" aria-hidden="true">
                <path
                  d="m4.5 4.5 7 7m0-7-7 7"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                />
              </svg>
            </button>
          </div>
        );
      })}
    </div>
  );
}
