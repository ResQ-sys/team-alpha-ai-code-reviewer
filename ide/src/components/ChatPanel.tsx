import { useEffect, useRef, useState } from 'react';
import type { ChatMessage, OpenFile } from '../types';
import { useStore } from '../store';
import * as api from '../lib/api';
import Markdown from './markdown';
import './chat-panel.css';

// Prepend the active file to the last user turn so the model has context, while
// leaving the on-screen messages clean (we never show the injected code).
function withFileContext(
  messages: ChatMessage[],
  file: OpenFile | null,
): ChatMessage[] {
  if (!file) return messages;
  const header =
    `The user attached the current file for context.\n` +
    `File: ${file.path} (${file.language})\n\n` +
    `\`\`\`${file.language}\n${file.content}\n\`\`\`\n\n---\n\n`;
  const out = messages.slice();
  for (let i = out.length - 1; i >= 0; i--) {
    if (out[i].role === 'user') {
      out[i] = { ...out[i], content: header + out[i].content };
      break;
    }
  }
  return out;
}

export default function ChatPanel() {
  const model = useStore((s) => s.model);
  const activeFile = useStore(
    (s) => s.open.find((f) => f.path === s.activePath) ?? null,
  );
  const toggleChat = useStore((s) => s.toggleChat);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [useContext, setUseContext] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  // Keep the transcript pinned to the newest content as tokens stream in.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // Abort any in-flight stream if the panel unmounts.
  useEffect(() => () => abortRef.current?.abort(), []);

  const canSend = input.trim().length > 0 && !streaming;

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;

    const userMsg: ChatMessage = { role: 'user', content: text };
    const base = [...messages, userMsg];
    // Optimistically render the user turn plus an empty assistant bubble.
    setMessages([...base, { role: 'assistant', content: '' }]);
    setInput('');
    setError(null);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;
    const outgoing = withFileContext(base, useContext ? activeFile : null);

    try {
      await api.streamChat(
        outgoing,
        model,
        (token) => {
          setMessages((prev) => {
            const copy = prev.slice();
            const last = copy[copy.length - 1];
            copy[copy.length - 1] = {
              ...last,
              content: last.content + token,
            };
            return copy;
          });
        },
        controller.signal,
      );
    } catch (err) {
      if (!controller.signal.aborted) {
        const msg = err instanceof Error ? err.message : 'Request failed';
        setError(msg);
        // Drop a trailing empty assistant bubble left by the failed request.
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.role === 'assistant' && last.content === '') {
            return prev.slice(0, -1);
          }
          return prev;
        });
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  function clear() {
    abortRef.current?.abort();
    setMessages([]);
    setError(null);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  const hasMessages = messages.length > 0;

  return (
    <section className="chat-panel" aria-label="AI Chat">
      <header className="chat-header">
        <span className="chat-title">Chat</span>
        <div className="chat-actions">
          <button
            type="button"
            className={`chat-ctx${useContext ? ' is-on' : ''}`}
            aria-pressed={useContext}
            onClick={() => setUseContext((v) => !v)}
            title={
              activeFile
                ? `Include ${activeFile.name} as context`
                : 'Open a file to add it as context'
            }
            disabled={!activeFile}
          >
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" aria-hidden="true">
              <path
                d="M8 4h8l4 4v12H4V4z"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinejoin="round"
              />
              <path d="M14 4v5h5" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
            </svg>
            <span className="chat-ctx-label">
              {activeFile ? activeFile.name : 'No file'}
            </span>
          </button>
          <button
            type="button"
            className="chat-icon-btn"
            onClick={clear}
            aria-label="Clear conversation"
            title="Clear conversation"
            disabled={!hasMessages && !streaming}
          >
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" aria-hidden="true">
              <path
                d="M5 7h14M10 7V5h4v2M6 7l1 12h10l1-12"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
          <button
            type="button"
            className="chat-icon-btn"
            onClick={toggleChat}
            aria-label="Close chat panel"
            title="Close chat"
          >
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" aria-hidden="true">
              <path
                d="M6 6l12 12M18 6L6 18"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
      </header>

      <div className="chat-messages" ref={listRef}>
        {!hasMessages && (
          <div className="chat-empty">
            <div className="chat-empty-glyph" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none">
                <path
                  d="M4 5h16v11H9l-5 4V5z"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            <p className="chat-empty-title">Ask about your code</p>
            <p className="chat-empty-hint">
              Explain a function, draft a fix, or plan a change. Toggle the file
              chip to give the model your open file.
            </p>
            <p className="chat-empty-model">
              {model ? (
                <>
                  Model <span className="chat-empty-model-name">{model}</span>
                </>
              ) : (
                'No model loaded'
              )}
            </p>
          </div>
        )}

        {messages.map((m, i) => {
          const isUser = m.role === 'user';
          const isLast = i === messages.length - 1;
          const pending = streaming && isLast && m.role === 'assistant';
          return (
            <div
              key={i}
              className={`chat-msg chat-msg--${m.role}`}
            >
              {isUser ? (
                <div className="chat-bubble">{m.content}</div>
              ) : (
                <div className="chat-assistant">
                  {m.content ? (
                    <Markdown text={m.content} />
                  ) : pending ? (
                    <span className="chat-typing" aria-label="Assistant is typing">
                      <span />
                      <span />
                      <span />
                    </span>
                  ) : null}
                  {pending && m.content && <span className="chat-caret" aria-hidden="true" />}
                </div>
              )}
            </div>
          );
        })}

        {error && (
          <div className="chat-error" role="alert">
            <span className="chat-error-dot" aria-hidden="true" />
            {error}
          </div>
        )}
      </div>

      <div className="chat-input">
        <textarea
          ref={taRef}
          className="chat-textarea"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={streaming ? 'Streaming…' : 'Ask anything. Enter to send, Shift+Enter for newline.'}
          rows={2}
          disabled={streaming}
          aria-label="Message"
        />
        <div className="chat-input-row">
          <span className="chat-input-model" title="Active model">
            {model || '—'}
          </span>
          {streaming ? (
            <button
              type="button"
              className="chat-send chat-stop"
              onClick={stop}
              aria-label="Stop generating"
            >
              Stop
            </button>
          ) : (
            <button
              type="button"
              className="chat-send"
              onClick={() => void send()}
              disabled={!canSend}
              aria-label="Send message"
            >
              Send
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true">
                <path
                  d="M5 12h13M12 5l7 7-7 7"
                  stroke="currentColor"
                  strokeWidth="1.7"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
