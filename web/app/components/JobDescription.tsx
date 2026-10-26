import type { JSX } from "react";

/**
 * Descriptions arrive as plain text, not HTML: the fetchers run every board's
 * markup through `_strip_html`, which turns block tags into newlines and `<li>`
 * into "- ". Rendering that as HTML collapses every newline and produces one
 * unreadable wall of text, so the structure has to be rebuilt from the text
 * itself. Doing it here also means no `dangerouslySetInnerHTML` anywhere near
 * third-party job copy.
 */

/** "- x", "* x", "• x", "1. x", or the 2+ space indent Amazon uses for lists. */
const BULLET = /^\s*(?:[-*•·–—]|\d{1,2}[.)])\s+/;
const INDENTED = /^ {2,}\S/;
const URL = /(https?:\/\/[^\s<>()]+[^\s<>().,;:!?])/g;

/**
 * Section labels ("Key job responsibilities", "BASIC QUALIFICATIONS") survive
 * stripping as bare short lines, indistinguishable from a paragraph without a
 * heuristic: short, few words, and not a finished sentence. A false positive
 * only renders a short line in bold, which is why the bar is deliberately low.
 */
function isHeading(line: string): boolean {
  const t = line.trim();
  if (t.length > 60 || t.split(/\s+/).length > 8) return false;
  if (/[.,;]$/.test(t)) return false; // a finished sentence is prose
  if (/:$/.test(t)) return true; // "Requirements:"
  if (t === t.toUpperCase() && /[A-Z]/.test(t)) return true; // "BASIC QUALIFICATIONS"
  // Anything left is a short fragment standing alone between blank lines, which
  // in stripped board copy is a section label far more often than it is prose.
  return true;
}

/** URLs are common in job copy and worth clicking. Everything else React escapes. */
function linkify(text: string, key: string): (string | JSX.Element)[] {
  // `split` on a capturing group returns the captures interleaved; testing the
  // part against URL directly would be wrong, since /g carries `lastIndex`.
  return text.split(URL).map((part, i) =>
    /^https?:\/\//.test(part) ? (
      <a
        key={`${key}-${i}`}
        href={part}
        target="_blank"
        rel="noreferrer"
        className="text-accent underline"
      >
        {part}
      </a>
    ) : (
      part
    ),
  );
}

export function JobDescription({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: JSX.Element[] = [];
  let list: string[] = [];

  const flushList = () => {
    if (!list.length) return;
    const items = list;
    list = [];
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="my-2 list-disc space-y-1 pl-5">
        {items.map((item, i) => (
          <li key={i}>{linkify(item, `li-${blocks.length}-${i}`)}</li>
        ))}
      </ul>,
    );
  };

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];

    if (!raw.trim()) {
      // Some boards blank-line-separate their own list items. Closing the list
      // on every blank would shatter one list into several, so only close it
      // when whatever comes next is genuinely not another item.
      const next = lines.slice(i + 1).find((l) => l.trim());
      if (!next || !(BULLET.test(next) || INDENTED.test(next))) flushList();
      continue;
    }

    if (BULLET.test(raw) || INDENTED.test(raw)) {
      list.push(raw.replace(BULLET, "").trim());
      continue;
    }

    flushList();
    const line = raw.trim();

    if (isHeading(line)) {
      blocks.push(
        <h3 key={`h-${blocks.length}`} className="mt-4 mb-1 font-semibold first:mt-0">
          {line}
        </h3>,
      );
    } else {
      // `_strip_html` only emits a newline at a block boundary, so one line is
      // one paragraph — no need to re-join wrapped text.
      blocks.push(
        <p key={`p-${blocks.length}`} className="my-2">
          {linkify(line, `p-${blocks.length}`)}
        </p>,
      );
    }
  }
  flushList();

  return <div className="text-sm leading-relaxed">{blocks}</div>;
}
