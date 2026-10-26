// Light markdown: headings, bold/italic, lists, code blocks, quotes, tables, links.
// Every piece of text is escaped before any tag is added.
"use strict";

const MD = (() => {
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ESC[c]);
  const COPY_ICON = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="5" y="5" width="9" height="9" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M3 11V3a1 1 0 0 1 1-1h7" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';

  let copies = [];
  const reset = () => { copies = []; };
  const copyButton = (text) => {
    const i = copies.push(text) - 1;
    return `<button type="button" class="copy" data-copy="${i}" aria-label="Copy" title="Copy">${COPY_ICON}</button>`;
  };
  const copyText = (i) => copies[Number(i)];

  function fmt(escaped) {
    return escaped
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_]+)__/g, "<strong>$1</strong>")
      .replace(/(^|[^\w*])\*(?!\s)([^*\n]+?)\*(?!\w)/g, "$1<em>$2</em>")
      .replace(/(^|[^\w])_(?!\s)([^_\n]+?)_(?!\w)/g, "$1<em>$2</em>")
      .replace(/~~([^~]+)~~/g, "<del>$1</del>");
  }

  function inline(s) {
    return String(s).split(/(`[^`\n]+`)/).map((part) =>
      /^`[^`\n]+`$/.test(part) ? `<code>${esc(part.slice(1, -1))}</code>` : fmt(esc(part))).join("");
  }

  const LIST_RE = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
  const isTableSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l || "");
  const cells = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  const startsBlock = (l, next) => /^\s*```/.test(l) || /^#{1,6}\s/.test(l) || /^\s*>/.test(l) ||
    LIST_RE.test(l) || (/^\s*\|/.test(l) && isTableSep(next));

  function render(src) {
    const lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
    let out = "";
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (/^\s*```/.test(line)) {
        const body = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
        i++;
        const code = body.join("\n");
        out += `<div class="code"><pre><code>${esc(code)}</code></pre>${copyButton(code)}</div>`;
        continue;
      }
      let m = line.match(/^(#{1,6})\s+(.*)$/);
      if (m) {
        const level = Math.min(6, m[1].length + 2);
        out += `<h${level}>${inline(m[2])}</h${level}>`;
        i++;
        continue;
      }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out += "<hr>"; i++; continue; }
      if (/^\s*>/.test(line)) {
        const body = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
        const text = body.join("\n");
        out += `<blockquote>${render(text)}${copyButton(text)}</blockquote>`;
        continue;
      }
      if (/^\s*\|/.test(line) && isTableSep(lines[i + 1])) {
        const head = cells(line);
        i += 2;
        let rows = "";
        while (i < lines.length && /^\s*\|/.test(lines[i])) {
          rows += "<tr>" + cells(lines[i++]).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>";
        }
        out += `<div class="table-wrap"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
        continue;
      }
      if (LIST_RE.test(line)) {
        const ordered = /\d/.test(line.match(LIST_RE)[2]);
        let items = "";
        while (i < lines.length && LIST_RE.test(lines[i])) {
          const [, indent, , text] = lines[i].match(LIST_RE);
          let item = text;
          i++;
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !LIST_RE.test(lines[i])) item += " " + lines[i++].trim();
          const depth = Math.min(3, Math.floor(indent.replace(/\t/g, "  ").length / 2));
          items += `<li class="d${depth}">${inline(item)}</li>`;
        }
        out += ordered ? `<ol>${items}</ol>` : `<ul>${items}</ul>`;
        continue;
      }
      if (!line.trim()) { i++; continue; }
      const para = [];
      while (i < lines.length && lines[i].trim() && !(para.length && startsBlock(lines[i], lines[i + 1]))) para.push(lines[i++]);
      out += `<p>${para.map(inline).join("<br>")}</p>`;
    }
    return out;
  }

  // iOS over plain http has no clipboard API: fall back to a hidden textarea + execCommand.
  function fallbackCopy(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.className = "offscreen";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, text.length);
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    return ok;
  }

  function copy(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(() => true, () => fallbackCopy(text));
    }
    return Promise.resolve(fallbackCopy(text));
  }

  return { esc, render, inline, reset, copyText, copy };
})();
