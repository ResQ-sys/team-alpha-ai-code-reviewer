import { useEffect, useRef, useState } from 'react';
import { useStore } from '../store';
import './open-folder.css';

// File System Access API is not in every lib.dom; probe it narrowly. We only
// want the picked folder's *name* as a suggestion — the browser never hands us
// an absolute path, so the user still confirms the real path in the input.
type DirHandle = { name: string };
type DirPicker = () => Promise<DirHandle>;
function getDirPicker(): DirPicker | null {
  const p = (window as unknown as { showDirectoryPicker?: DirPicker }).showDirectoryPicker;
  return typeof p === 'function' ? p : null;
}

function FolderGlyph() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path
        d="M2 4.5A1 1 0 0 1 3 3.5h3l1.2 1.4H13a1 1 0 0 1 1 1v6.1a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-8Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// Explorer-header control: shows the active workspace name and opens a small
// form to point the IDE at any local repo root.
export default function OpenFolder() {
  const workspace = useStore((s) => s.workspace);
  const loadWorkspace = useStore((s) => s.loadWorkspace);
  const openWorkspace = useStore((s) => s.openWorkspace);

  const [open, setOpen] = useState(false);
  const [path, setPath] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const picker = getDirPicker();

  // Learn the current root on mount so the button isn't blank.
  useEffect(() => {
    if (!workspace) void loadWorkspace();
  }, [workspace, loadWorkspace]);

  // Focus the input when the form opens.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  function toggle() {
    setError(null);
    setPath(workspace?.root ?? '');
    setOpen((v) => !v);
  }

  async function browse() {
    if (!picker) return;
    try {
      const handle = await picker();
      // Suggest the folder name; the user completes the absolute path.
      if (!path.trim()) setPath(handle.name);
      setError(
        `Picked "${handle.name}". Browsers hide absolute paths — enter its full path to open it.`,
      );
      inputRef.current?.focus();
    } catch {
      // User dismissed the native picker; nothing to do.
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const target = path.trim();
    if (!target || busy) return;
    setBusy(true);
    setError(null);
    try {
      await openWorkspace(target);
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not open folder');
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
    }
  }

  const name = workspace?.name ?? 'No folder';

  return (
    <div className="of" onKeyDown={onKeyDown}>
      <button
        type="button"
        className="of-trigger"
        onClick={toggle}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={workspace?.root ?? 'Open a folder'}
      >
        <FolderGlyph />
        <span className="of-name">{name}</span>
        <svg
          className={`of-caret${open ? ' is-open' : ''}`}
          viewBox="0 0 16 16"
          width="11"
          height="11"
          fill="none"
          aria-hidden="true"
        >
          <path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <form className="of-pop" role="dialog" aria-label="Open folder" onSubmit={submit}>
          <label className="of-field-label" htmlFor="of-path">
            Absolute folder path
          </label>
          <div className="of-field">
            <input
              id="of-path"
              ref={inputRef}
              className="of-input"
              type="text"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="/Users/you/code/sample_repo"
              spellCheck={false}
              autoComplete="off"
            />
            {picker && (
              <button
                type="button"
                className="of-browse"
                onClick={() => void browse()}
                title="Pick a folder to suggest its name"
              >
                Browse…
              </button>
            )}
          </div>

          {error && (
            <p className="of-error" role="alert">
              {error}
            </p>
          )}

          <div className="of-actions">
            <button type="button" className="of-btn of-btn--ghost" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button type="submit" className="of-btn of-btn--primary" disabled={!path.trim() || busy}>
              {busy ? 'Opening…' : 'Open'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
