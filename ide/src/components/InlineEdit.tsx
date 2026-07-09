import { useEffect, useRef, useState } from 'react';
import type { Monaco } from '@monaco-editor/react';
import type { editor as MonacoEditor } from 'monaco-editor';
import { store } from '../store';
import { streamEdit } from '../lib/api';
import './inline-edit.css';

export interface InlineEditRange {
  startLineNumber: number;
  startColumn: number;
  endLineNumber: number;
  endColumn: number;
}

export interface InlineEditTarget {
  range: InlineEditRange;
  text: string;
  top: number;
  left: number;
}

type Phase = 'input' | 'streaming' | 'review' | 'error';

interface Props {
  editor: MonacoEditor.IStandaloneCodeEditor;
  monaco: Monaco;
  language: string;
  target: InlineEditTarget;
  onClose: () => void;
}

// Clamp the widget horizontally so it never runs off the editor edge.
const MAX_LEFT_INSET = 24;
const WIDGET_WIDTH = 460;

export default function InlineEdit({ editor, monaco, language, target, onClose }: Props) {
  const [phase, setPhase] = useState<Phase>('input');
  const [instruction, setInstruction] = useState('');
  const [rewrite, setRewrite] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const acceptRef = useRef<HTMLButtonElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const decorationsRef = useRef<string[]>([]);

  // Highlight the target range in the editor while the widget is open.
  useEffect(() => {
    const ids = editor.deltaDecorations(
      [],
      [
        {
          range: new monaco.Range(
            target.range.startLineNumber,
            target.range.startColumn,
            target.range.endLineNumber,
            target.range.endColumn,
          ),
          options: {
            className: 'ie-target-highlight',
            isWholeLine: false,
          },
        },
      ],
    );
    decorationsRef.current = ids;
    return () => {
      editor.deltaDecorations(decorationsRef.current, []);
    };
  }, [editor, monaco, target]);

  // Move focus with the phase: prompt on input, Accept on review, so the
  // keyboard flow (Enter / Esc) stays live without a mouse.
  useEffect(() => {
    if (phase === 'input') inputRef.current?.focus();
    else if (phase === 'review') acceptRef.current?.focus();
  }, [phase]);

  // Abort any in-flight stream on unmount.
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  function cancel() {
    abortRef.current?.abort();
    onClose();
  }

  async function submit() {
    const trimmed = instruction.trim();
    if (!trimmed || phase === 'streaming') return;

    const model = store.getState().model;
    setRewrite('');
    setErrorMsg('');
    setPhase('streaming');

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      let acc = '';
      await streamEdit(
        target.text,
        trimmed,
        language,
        model,
        (tok) => {
          acc += tok;
          setRewrite(acc);
        },
        controller.signal,
      );
      // Trailing whitespace/newline from the model is noise for a range replace.
      setRewrite(acc.replace(/\n+$/, ''));
      setPhase('review');
    } catch (e: unknown) {
      if (controller.signal.aborted) return; // user cancelled — already closing
      setErrorMsg(e instanceof Error ? e.message : 'Edit failed');
      setPhase('error');
    }
  }

  function accept() {
    const range = new monaco.Range(
      target.range.startLineNumber,
      target.range.startColumn,
      target.range.endLineNumber,
      target.range.endColumn,
    );
    editor.executeEdits('inline-edit', [{ range, text: rewrite, forceMoveMarkers: true }]);
    editor.pushUndoStop();
    onClose();
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault();
      cancel();
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey && phase === 'input') {
      e.preventDefault();
      void submit();
      return;
    }
    if (e.key === 'Enter' && phase === 'review') {
      e.preventDefault();
      accept();
    }
  }

  const left = Math.max(MAX_LEFT_INSET, target.left);

  return (
    <div
      className="inline-edit"
      style={{ top: target.top, left, width: WIDGET_WIDTH }}
      onKeyDown={onKeyDown}
      role="dialog"
      aria-label="Inline edit"
    >
      <div className="ie-prompt-row">
        <span className="ie-badge" aria-hidden="true">
          <svg viewBox="0 0 16 16" width="13" height="13" fill="none">
            <path d="M8 2v12M2 8h12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </span>
        <textarea
          ref={inputRef}
          className="ie-input"
          rows={1}
          placeholder="Describe the edit…  (Enter to run, Esc to cancel)"
          value={instruction}
          disabled={phase === 'streaming'}
          onChange={(e) => setInstruction(e.target.value)}
        />
        {phase === 'input' && (
          <button
            type="button"
            className="ie-run"
            onClick={() => void submit()}
            disabled={!instruction.trim()}
            aria-label="Run edit"
          >
            Run
          </button>
        )}
        {phase === 'streaming' && <span className="ie-spinner" aria-label="Generating" />}
      </div>

      {(phase === 'streaming' || phase === 'review') && (
        <div className="ie-diff">
          <div className="ie-diff-col ie-old">
            <span className="ie-diff-tag">Original</span>
            <pre className="ie-code">{target.text}</pre>
          </div>
          <div className="ie-diff-col ie-new">
            <span className="ie-diff-tag">Suggestion</span>
            <pre className="ie-code">
              {rewrite}
              {phase === 'streaming' && <span className="ie-caret" aria-hidden="true" />}
            </pre>
          </div>
        </div>
      )}

      {phase === 'error' && <p className="ie-error">{errorMsg}</p>}

      {phase === 'review' && (
        <div className="ie-actions">
          <button ref={acceptRef} type="button" className="ie-btn ie-accept" onClick={accept}>
            Accept
            <kbd>↵</kbd>
          </button>
          <button type="button" className="ie-btn ie-reject" onClick={cancel}>
            Reject
            <kbd>Esc</kbd>
          </button>
        </div>
      )}
    </div>
  );
}
