// Minimal, safe Markdown renderer. It only ever creates DOM nodes (no innerHTML), so model output and
// repository text cannot inject markup. `[S#]` citations and `path:line` mentions become links only when
// they resolve to a retrieved source or an indexed file.
import { el } from "../dom.js";
import { resolveIndexedPath } from "../paths.js";
import { state } from "../state.js";
import { openSource } from "./drawer.js";
import { mermaidBlock } from "./mermaid.js";

const PATH_REF = /(?<![\w/.-])((?:[\w.@-]+\/)*[\w.@-]+\.[A-Za-z0-9]{1,8}):(\d+)(?:-(\d+))?/g;
const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[S\d+\])|(\[unverified citation\])|(\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\))|((?<![\w*])\*[^*\n]+\*(?![\w*]))/g;
const FENCE = /^\s*```\s*([\w+-]*)\s*$/;
const HEADING = /^(#{1,4})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function sourceButton(label, path, start, end, ctx, className = "path-link", title) {
  return el("button", {
    type: "button", class: className, title: title || `${path}:${start}${end > start ? "-" + end : ""}`,
    onclick: () => openSource(path, start, end, ctx.repoId),
  }, label);
}

function linkPaths(text, ctx) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(PATH_REF)) {
    const path = resolveIndexedPath(m[1]);
    if (!path) continue;
    if (m.index > last) out.push(text.slice(last, m.index));
    const start = parseInt(m[2], 10);
    const end = m[3] ? parseInt(m[3], 10) : start;
    out.push(sourceButton(m[0], path, start, Math.max(start, end), ctx));
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function inline(text, ctx) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push(...linkPaths(text.slice(last, m.index), ctx));
    if (m[1]) {
      const code = m[1].slice(1, -1);
      const ref = /^([\w./@-]+\.[A-Za-z0-9]{1,8}):(\d+)(?:-(\d+))?$/.exec(code);
      const path = ref && resolveIndexedPath(ref[1]);
      if (path) {
        const start = parseInt(ref[2], 10);
        out.push(sourceButton(code, path, start, Math.max(start, ref[3] ? parseInt(ref[3], 10) : start), ctx));
      } else {
        out.push(el("code", { text: code }));
      }
    } else if (m[2]) {
      out.push(el("strong", {}, inline(m[2].slice(2, -2), ctx)));
    } else if (m[3]) {
      const id = m[3].slice(1, -1);
      const src = ctx.sources.find((s) => s.id === id);
      out.push(src
        ? sourceButton(id, src.path, src.start_line, src.end_line, ctx, "cite", `${src.path}:${src.start_line}-${src.end_line}${src.symbol ? " (" + src.symbol + ")" : ""}`)
        : el("span", { class: "cite-bad", text: m[3], title: "No such source was retrieved" }));
    } else if (m[4]) {
      out.push(el("span", { class: "cite-bad", text: m[4], title: "The model cited a source that was not retrieved" }));
    } else if (m[5]) {
      out.push(el("a", { href: m[7], target: "_blank", rel: "noopener noreferrer", text: m[6] }));
    } else if (m[8]) {
      out.push(el("em", {}, inline(m[8].slice(1, -1), ctx)));
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(...linkPaths(text.slice(last), ctx));
  return out;
}

function splitRow(line) {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  return row.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

function buildList(items, start, baseIndent, ctx) {
  const list = el(items[start].ordered ? "ol" : "ul");
  let i = start;
  while (i < items.length && items[i].indent >= baseIndent) {
    const li = el("li", {}, inline(items[i].text, ctx));
    i++;
    if (i < items.length && items[i].indent > baseIndent) {
      const [sub, next] = buildList(items, i, items[i].indent, ctx);
      li.appendChild(sub);
      i = next;
    }
    list.appendChild(li);
  }
  return [list, i];
}

export function renderMarkdown(text, { sources = [], repoId = state.repoId } = {}) {
  const ctx = { sources, repoId };
  const root = el("div", { class: "md" });
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    const fence = FENCE.exec(line);
    if (fence) {
      const body = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
      i++;
      const code = body.join("\n");
      root.appendChild(fence[1].toLowerCase() === "mermaid"
        ? mermaidBlock(code)
        : el("pre", {}, el("code", { text: code, class: fence[1] ? `language-${fence[1]}` : null })));
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      root.appendChild(el(`h${Math.min(4, heading[1].length + 1)}`, {}, inline(heading[2], ctx)));
      i++;
      continue;
    }
    if (RULE.test(line) && !line.includes("|")) { root.appendChild(el("hr")); i++; continue; }

    if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) rows.push(splitRow(lines[i++]));
      root.appendChild(el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, head.map((c) => el("th", {}, inline(c, ctx))))),
        el("tbody", {}, rows.map((r) => el("tr", {}, head.map((_, k) => el("td", {}, inline(r[k] || "", ctx)))))))));
      continue;
    }

    if (/^\s*>/.test(line)) {
      const quote = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ""));
      root.appendChild(el("blockquote", {}, quote.map((q, k) => [k ? el("br") : null, inline(q, ctx)])));
      continue;
    }

    if (LIST_ITEM.test(line)) {
      const items = [];
      while (i < lines.length) {
        const m = LIST_ITEM.exec(lines[i]);
        if (m) {
          items.push({ indent: m[1].replace(/\t/g, "    ").length, ordered: /\d/.test(m[2]), text: m[3] });
          i++;
        } else if (lines[i].trim() && /^\s{2,}\S/.test(lines[i]) && items.length) {
          items[items.length - 1].text += " " + lines[i].trim();
          i++;
        } else {
          break;
        }
      }
      let at = 0;
      while (at < items.length) {
        const [list, next] = buildList(items, at, items[at].indent, ctx);
        root.appendChild(list);
        at = next;
      }
      continue;
    }

    const para = [];
    while (i < lines.length && lines[i].trim() && !FENCE.test(lines[i]) && !HEADING.test(lines[i]) && !LIST_ITEM.test(lines[i])
      && !/^\s*>/.test(lines[i]) && !(lines[i].includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]))) {
      para.push(lines[i++]);
    }
    if (!para.length) para.push(lines[i++]); // guarantees progress on any line no block rule claimed
    root.appendChild(el("p", {}, para.map((p, k) => [k ? el("br") : null, inline(p, ctx)])));
  }
  return root;
}
