import { useCallback, useEffect, useRef, useState } from 'react';
import Editor from '@monaco-editor/react';
import type { Monaco } from '@monaco-editor/react';
import type { editor as MonacoEditor } from 'monaco-editor';
import { useStore, store } from '../store';
import { ensureTheme, IDE_THEME } from '../lib/monaco-env';
import { INLINE_EDIT_EVENT } from '../App';
import InlineEdit, { type InlineEditTarget } from './InlineEdit';
import './editor-pane.css';

const MONO_STACK = "'JetBrains Mono', 'SF Mono', Menlo, ui-monospace, monospace";

export default function EditorPane() {
  const open = useStore((s) => s.open);
  const activePath = useStore((s) => s.activePath);
  const active = open.find((f) => f.path === activePath) ?? null;

  const editorRef = useRef<MonacoEditor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const [inlineTarget, setInlineTarget] = useState<InlineEditTarget | null>(null);

  // Open the inline-edit widget over the current selection (or current line
  // when the selection is empty), Cursor-style.
  const triggerInlineEdit = useCallback(() => {
    const ed = editorRef.current;
    const model = ed?.getModel();
    if (!ed || !model) return;

    const sel = ed.getSelection();
    let startLineNumber = sel?.startLineNumber ?? 1;
    let startColumn = sel?.startColumn ?? 1;
    let endLineNumber = sel?.endLineNumber ?? 1;
    let endColumn = sel?.endColumn ?? 1;

    // Empty selection → target the whole current line.
    if (!sel || sel.isEmpty()) {
      const line = sel?.startLineNumber ?? ed.getPosition()?.lineNumber ?? 1;
      startLineNumber = line;
      startColumn = 1;
      endLineNumber = line;
      endColumn = model.getLineMaxColumn(line);
    }

    const text = model.getValueInRange({
      startLineNumber,
      startColumn,
      endLineNumber,
      endColumn,
    });

    const pos = ed.getScrolledVisiblePosition({ lineNumber: startLineNumber, column: startColumn });
    const top = (pos?.top ?? 0) + (pos?.height ?? 18);
    const left = pos?.left ?? 0;

    setInlineTarget({
      range: { startLineNumber, startColumn, endLineNumber, endColumn },
      text,
      top,
      left,
    });
  }, []);

  // Keep a live ref so Monaco's registered command always calls the latest fn.
  const triggerRef = useRef(triggerInlineEdit);
  triggerRef.current = triggerInlineEdit;

  // App dispatches this on Cmd/Ctrl+K when the shell has focus; the in-editor
  // keybinding (registered in onMount) covers the focused-editor case.
  useEffect(() => {
    function onEvt() {
      triggerRef.current();
    }
    window.addEventListener(INLINE_EDIT_EVENT, onEvt);
    return () => window.removeEventListener(INLINE_EDIT_EVENT, onEvt);
  }, []);

  function handleBeforeMount(monaco: Monaco) {
    ensureTheme(monaco);
  }

  function handleMount(ed: MonacoEditor.IStandaloneCodeEditor, monaco: Monaco) {
    editorRef.current = ed;
    monacoRef.current = monaco;

    // Cmd/Ctrl+S — save without popping the browser dialog.
    ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      void store.getState().saveActive();
    });

    // Cmd/Ctrl+K — inline edit. Registered in-editor so Monaco doesn't swallow
    // it as a chord prefix.
    ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyK, () => {
      triggerRef.current();
    });
  }

  function handleChange(value: string | undefined) {
    const path = store.getState().activePath;
    if (path == null) return;
    store.getState().updateContent(path, value ?? '');
  }

  return (
    <div className="editor-pane">
      {active ? (
        <div className="ep-monaco-host">
          <Editor
            className="ep-monaco"
            theme={IDE_THEME}
            path={active.path}
            language={active.language}
            value={active.content}
            beforeMount={handleBeforeMount}
            onMount={handleMount}
            onChange={handleChange}
            loading={<div className="ep-empty">Loading editor…</div>}
            options={{
              fontFamily: MONO_STACK,
              fontLigatures: true,
              fontSize: 13,
              lineHeight: 20,
              letterSpacing: 0.2,
              minimap: { enabled: true, renderCharacters: false, maxColumn: 80 },
              smoothScrolling: true,
              cursorBlinking: 'smooth',
              cursorSmoothCaretAnimation: 'on',
              renderLineHighlight: 'all',
              renderWhitespace: 'selection',
              scrollBeyondLastLine: false,
              padding: { top: 12, bottom: 12 },
              automaticLayout: true,
              tabSize: 2,
              guides: { indentation: true, bracketPairs: true },
              scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
              stickyScroll: { enabled: true },
              fixedOverflowWidgets: true,
            }}
          />

          {inlineTarget && editorRef.current && monacoRef.current && (
            <InlineEdit
              editor={editorRef.current}
              monaco={monacoRef.current}
              language={active.language}
              target={inlineTarget}
              onClose={() => {
                setInlineTarget(null);
                editorRef.current?.focus();
              }}
            />
          )}
        </div>
      ) : (
        <div className="ep-empty" role="status">
          <svg viewBox="0 0 24 24" width="42" height="42" fill="none" aria-hidden="true">
            <path
              d="M4 5.5A1.5 1.5 0 0 1 5.5 4h6l2 2H18.5A1.5 1.5 0 0 1 20 7.5v11A1.5 1.5 0 0 1 18.5 20h-13A1.5 1.5 0 0 1 4 18.5v-13Z"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinejoin="round"
            />
          </svg>
          <p className="ep-empty-title">No file open</p>
          <p className="ep-empty-hint">Select a file in the Explorer to start editing</p>
        </div>
      )}
    </div>
  );
}
