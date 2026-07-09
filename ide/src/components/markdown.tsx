import React, { useState } from 'react';

// Tiny, dependency-free markdown renderer tuned for chat output.
// Supports: fenced code blocks (with copy), headings, unordered/ordered lists,
// blockquotes, inline code, bold, italic, and safe links. Everything renders as
// React nodes so text is auto-escaped (no dangerouslySetInnerHTML, no XSS).

type Segment =
  | { type: 'prose'; text: string }
  | { type: 'code'; lang: string; code: string };

const FENCE = /^```(.*)$/;

// Split a document into prose and fenced-code segments. Tolerates an unclosed
// trailing fence (common mid-stream) by flushing it as an open code block.
function toSegments(md: string): Segment[] {
  const lines = md.split('\n');
  const segs: Segment[] = [];
  let buf: string[] = [];
  let inCode = false;
  let lang = '';

  const flushProse = () => {
    if (buf.length) segs.push({ type: 'prose', text: buf.join('\n') });
    buf = [];
  };
  const flushCode = () => {
    segs.push({ type: 'code', lang, code: buf.join('\n') });
    buf = [];
    lang = '';
  };

  for (const line of lines) {
    const fence = FENCE.exec(line.trim());
    if (fence) {
      if (!inCode) {
        flushProse();
        inCode = true;
        lang = fence[1].trim();
      } else {
        flushCode();
        inCode = false;
      }
      continue;
    }
    buf.push(line);
  }
  if (inCode) flushCode();
  else flushProse();
  return segs;
}

// Only permit safe link schemes; anything else renders as plain text.
function safeHref(url: string): string | null {
  const u = url.trim();
  if (/^(https?:|mailto:)/i.test(u)) return u;
  if (u.startsWith('/') || u.startsWith('#')) return u;
  return null;
}

const INLINE =
  /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(\[[^\]]+\]\([^)]+\))/g;

// Parse inline spans (code / bold / italic / link) into React nodes.
function renderInline(text: string, keyBase: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  let i = 0;
  let m: RegExpExecArray | null;
  INLINE.lastIndex = 0;

  while ((m = INLINE.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    const key = `${keyBase}-i${i++}`;

    if (tok.startsWith('`')) {
      nodes.push(
        <code key={key} className="md-inline-code">
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (tok.startsWith('**')) {
      nodes.push(<strong key={key}>{tok.slice(2, -2)}</strong>);
    } else if (tok.startsWith('*')) {
      nodes.push(<em key={key}>{tok.slice(1, -1)}</em>);
    } else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok);
      const href = link ? safeHref(link[2]) : null;
      if (link && href) {
        nodes.push(
          <a key={key} href={href} target="_blank" rel="noreferrer noopener">
            {link[1]}
          </a>,
        );
      } else {
        nodes.push(tok);
      }
    }
    last = m.index + tok.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

const HEADING = /^(#{1,6})\s+(.*)$/;
const UL_ITEM = /^\s*[-*]\s+(.*)$/;
const OL_ITEM = /^\s*\d+\.\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;

// Render a prose segment: headings, lists, blockquotes, and paragraphs.
function renderProse(text: string, keyBase: string): React.ReactNode {
  const lines = text.split('\n');
  const out: React.ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let para: string[] = [];
  let n = 0;

  const flushList = () => {
    if (!list) return;
    const key = `${keyBase}-l${n++}`;
    const items = list.items.map((it, idx) => (
      <li key={`${key}-${idx}`}>{renderInline(it, `${key}-${idx}`)}</li>
    ));
    out.push(
      list.ordered ? (
        <ol key={key} className="md-ol">
          {items}
        </ol>
      ) : (
        <ul key={key} className="md-ul">
          {items}
        </ul>
      ),
    );
    list = null;
  };

  const flushPara = () => {
    if (!para.length) return;
    const key = `${keyBase}-p${n++}`;
    out.push(
      <p key={key} className="md-p">
        {renderInline(para.join(' '), key)}
      </p>,
    );
    para = [];
  };

  for (const line of lines) {
    if (!line.trim()) {
      flushList();
      flushPara();
      continue;
    }

    const heading = HEADING.exec(line);
    const ul = UL_ITEM.exec(line);
    const ol = OL_ITEM.exec(line);
    const quote = QUOTE.exec(line);

    if (heading) {
      flushList();
      flushPara();
      const level = Math.min(heading[1].length + 1, 6);
      const key = `${keyBase}-h${n++}`;
      out.push(
        React.createElement(
          `h${level}`,
          { key, className: 'md-h' },
          renderInline(heading[2], key),
        ),
      );
    } else if (ul || ol) {
      flushPara();
      const ordered = Boolean(ol);
      const item = (ul ? ul[1] : (ol as RegExpExecArray)[1]).trim();
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push(item);
    } else if (quote) {
      flushList();
      flushPara();
      const key = `${keyBase}-q${n++}`;
      out.push(
        <blockquote key={key} className="md-quote">
          {renderInline(quote[1], key)}
        </blockquote>,
      );
    } else {
      flushList();
      para.push(line.trim());
    }
  }
  flushList();
  flushPara();
  return out;
}

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      // Clipboard unavailable (insecure context) — fail quietly.
    }
  };

  return (
    <div className="md-code">
      <div className="md-code-head">
        <span className="md-code-lang">{lang || 'code'}</span>
        <button
          type="button"
          className="md-copy"
          onClick={copy}
          aria-label="Copy code to clipboard"
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="md-pre">
        <code>{code}</code>
      </pre>
    </div>
  );
}

export default function Markdown({ text }: { text: string }) {
  const segments = toSegments(text);
  return (
    <div className="md">
      {segments.map((seg, i) =>
        seg.type === 'code' ? (
          <CodeBlock key={`s${i}`} lang={seg.lang} code={seg.code} />
        ) : (
          <React.Fragment key={`s${i}`}>
            {renderProse(seg.text, `s${i}`)}
          </React.Fragment>
        ),
      )}
    </div>
  );
}
