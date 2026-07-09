import { useEffect, useState } from 'react';
import { useStore } from '../store';
import type { FileNode } from '../types';
import OpenFolder from './OpenFolder';
import './file-explorer.css';

// --- icons -------------------------------------------------------------------

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      className={`fx-chevron${open ? ' is-open' : ''}`}
      viewBox="0 0 16 16"
      width="12"
      height="12"
      fill="none"
      aria-hidden="true"
    >
      <path d="m6 4 4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function FolderIcon({ open }: { open: boolean }) {
  return (
    <svg className="fx-icon fx-folder" viewBox="0 0 16 16" width="15" height="15" fill="none" aria-hidden="true">
      <path
        d={
          open
            ? 'M2 4.5A1 1 0 0 1 3 3.5h3l1.2 1.4H13a1 1 0 0 1 1 1v.4H4.4a1 1 0 0 0-.95.68L2 12.5V4.5Z'
            : 'M2 4.5A1 1 0 0 1 3 3.5h3l1.2 1.4H13a1 1 0 0 1 1 1v6.1a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-8Z'
        }
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// File-type accent: keep a small, deliberate palette rather than a rainbow.
function fileAccent(name: string): string {
  const ext = name.slice(name.lastIndexOf('.') + 1).toLowerCase();
  switch (ext) {
    case 'ts':
    case 'tsx':
    case 'js':
    case 'jsx':
      return 'var(--accent)';
    case 'py':
      return 'var(--sev-medium)';
    case 'json':
    case 'yml':
    case 'yaml':
    case 'toml':
      return 'var(--sev-high)';
    case 'css':
    case 'scss':
    case 'html':
      return 'var(--sev-low)';
    case 'md':
      return 'var(--text-dim)';
    default:
      return 'var(--text-faint)';
  }
}

function FileIcon({ name }: { name: string }) {
  return (
    <svg
      className="fx-icon fx-file"
      viewBox="0 0 16 16"
      width="15"
      height="15"
      fill="none"
      aria-hidden="true"
      style={{ color: fileAccent(name) }}
    >
      <path
        d="M4 2.5h5L12.5 6v7.5a1 1 0 0 1-1 1h-7.5a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1Z"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <path d="M9 2.5V6h3.5" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
    </svg>
  );
}

// --- tree --------------------------------------------------------------------

// Dirs first, then files; each group alphabetized. Stable, editor-standard.
function sortChildren(nodes: FileNode[]): FileNode[] {
  return [...nodes].sort((a, b) => {
    if (a.type !== b.type) return a.type === 'dir' ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

const INDENT_STEP = 12;
const BASE_INDENT = 8;

function TreeItem({ node, depth }: { node: FileNode; depth: number }) {
  // Auto-expand the top level so the workspace isn't a wall of collapsed dirs.
  const [open, setOpen] = useState(depth === 0);
  const activePath = useStore((s) => s.activePath);
  const openFile = useStore((s) => s.openFile);

  const isDir = node.type === 'dir';
  const isActive = !isDir && node.path === activePath;
  const pad = BASE_INDENT + depth * INDENT_STEP;

  function onActivate() {
    if (isDir) {
      setOpen((o) => !o);
    } else {
      void openFile(node.path);
    }
  }

  return (
    <li className="fx-item" role="none">
      <button
        type="button"
        role="treeitem"
        aria-expanded={isDir ? open : undefined}
        aria-selected={isActive}
        className={`fx-row${isActive ? ' is-active' : ''}`}
        style={{ paddingLeft: pad }}
        title={node.path}
        onClick={onActivate}
      >
        {isDir ? <Chevron open={open} /> : <span className="fx-chevron-spacer" aria-hidden="true" />}
        {isDir ? <FolderIcon open={open} /> : <FileIcon name={node.name} />}
        <span className="fx-name">{node.name}</span>
      </button>

      {isDir && open && node.children && node.children.length > 0 && (
        <ul className="fx-children" role="group">
          {sortChildren(node.children).map((child) => (
            <TreeItem key={child.path} node={child} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function FileExplorer() {
  const tree = useStore((s) => s.tree);
  const loadTree = useStore((s) => s.loadTree);
  const [error, setError] = useState<string | null>(null);

  // Load the tree on mount if the shell hasn't populated it yet.
  useEffect(() => {
    if (tree) return;
    setError(null);
    void loadTree().catch((e: unknown) => {
      setError(e instanceof Error ? e.message : 'Failed to load files');
    });
  }, [tree, loadTree]);

  const rootChildren = tree?.children ?? [];

  return (
    <div className="file-explorer" aria-label="Explorer">
      <header className="fx-header fx-header--folder">
        <OpenFolder />
      </header>

      <div className="fx-scroll">
        {error && <p className="fx-empty fx-error">{error}</p>}

        {!error && !tree && <p className="fx-empty">Loading workspace…</p>}

        {!error && tree && rootChildren.length === 0 && (
          <p className="fx-empty">No files in workspace</p>
        )}

        {rootChildren.length > 0 && (
          <ul className="fx-tree" role="tree" aria-label="Files">
            {sortChildren(rootChildren).map((node) => (
              <TreeItem key={node.path} node={node} depth={0} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
