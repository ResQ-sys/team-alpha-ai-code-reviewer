// Monaco offline/Vite wiring. Imported once (side-effect) before any editor
// mounts. Uses the bundled monaco + Vite `?worker` imports so language
// intelligence works with ZERO CDN / network dependency.
import * as monaco from 'monaco-editor';
import { loader } from '@monaco-editor/react';
import type { Environment } from 'monaco-editor';

import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker';
import cssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker';
import htmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker';
import tsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker';

// Route each language to its dedicated bundled worker.
const env: Environment = {
  getWorker(_workerId: string, label: string): Worker {
    switch (label) {
      case 'json':
        return new jsonWorker();
      case 'css':
      case 'scss':
      case 'less':
        return new cssWorker();
      case 'html':
      case 'handlebars':
      case 'razor':
        return new htmlWorker();
      case 'typescript':
      case 'javascript':
        return new tsWorker();
      default:
        return new editorWorker();
    }
  },
};

(self as unknown as { MonacoEnvironment: Environment }).MonacoEnvironment = env;

// Point @monaco-editor/react at the bundled instance instead of the CDN.
loader.config({ monaco });

// Custom theme tuned to our --editor-bg so the canvas matches the shell.
let themeReady = false;
export const IDE_THEME = 'ide-dark';

export function ensureTheme(m: typeof monaco): void {
  if (themeReady) return;
  m.editor.defineTheme(IDE_THEME, {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: '', foreground: 'e4e4e7', background: '141417' },
      { token: 'comment', foreground: '6b6b73', fontStyle: 'italic' },
      { token: 'keyword', foreground: '4c8dff' },
      { token: 'string', foreground: '7bd88f' },
      { token: 'number', foreground: 'ffd452' },
      { token: 'type', foreground: 'ff9f43' },
      { token: 'function', foreground: '9a9aa2' },
    ],
    colors: {
      'editor.background': '#141417',
      'editor.foreground': '#e4e4e7',
      'editorLineNumber.foreground': '#3a3a42',
      'editorLineNumber.activeForeground': '#9a9aa2',
      'editor.selectionBackground': '#2d5db355',
      'editor.inactiveSelectionBackground': '#2d5db333',
      'editor.lineHighlightBackground': '#1b1b1f',
      'editor.lineHighlightBorder': '#00000000',
      'editorCursor.foreground': '#4c8dff',
      'editorIndentGuide.background1': '#1f1f24',
      'editorIndentGuide.activeBackground1': '#26262b',
      'editorGutter.background': '#141417',
      'editorWidget.background': '#161619',
      'editorWidget.border': '#26262b',
      'editorSuggestWidget.background': '#161619',
      'editorSuggestWidget.border': '#26262b',
      'editorSuggestWidget.selectedBackground': '#1b1b1f',
      'editorBracketMatch.background': '#2d5db333',
      'editorBracketMatch.border': '#2d5db3',
      'scrollbarSlider.background': '#30303655',
      'scrollbarSlider.hoverBackground': '#45454d88',
      'scrollbarSlider.activeBackground': '#45454daa',
      'diffEditor.insertedTextBackground': '#7bd88f22',
      'diffEditor.removedTextBackground': '#ff5c5c22',
    },
  });
  themeReady = true;
}
