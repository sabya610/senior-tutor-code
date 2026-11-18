import { api } from "../api.js";
import { badge, button, clear, debounce, el, emptyState, errorBox, fmtNum, spinner } from "../dom.js";
import { openSource } from "../components/drawer.js";
import { repoGate, viewHead } from "../components/layout.js";
import { state } from "../state.js";

function buildTree(files) {
  const root = { dirs: new Map(), files: [] };
  for (const file of files) {
    const parts = file.path.split("/");
    let node = root;
    for (const dir of parts.slice(0, -1)) {
      if (!node.dirs.has(dir)) node.dirs.set(dir, { dirs: new Map(), files: [] });
      node = node.dirs.get(dir);
    }
    node.files.push({ ...file, name: parts[parts.length - 1] });
  }
  return root;
}

function renderTree(node, open, depth = 0) {
  const out = [];
  for (const [name, child] of [...node.dirs.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    const details = el("details", { open: depth < 1 }, el("summary", { text: name }));
    const holder = el("div", { class: "children" });
    details.append(holder);
    let filled = false;
    const fill = () => {
      if (filled) return;
      filled = true;
      holder.append(...renderTree(child, open, depth + 1));
    };
    if (details.open) fill();
    details.addEventListener("toggle", () => details.open && fill());
    out.push(details);
  }
  for (const file of node.files.sort((a, b) => a.name.localeCompare(b.name))) {
    out.push(el("button", { type: "button", class: "file", title: `${file.path} (${fmtNum(file.lines)} lines)`, onclick: () => open(file.path) },
      el("span", { class: "mono", text: file.name })));
  }
  return out;
}

export async function mount(root) {
  root.append(viewHead("Repository Explorer", "Browse indexed files, look up symbols, and search the code with hybrid keyword and semantic retrieval."));
  const repo = repoGate(root);
  if (!repo) return undefined;

  const treeBox = el("div", { class: "card tree" }, spinner());
  const results = el("div", { class: "stack" });
  const queryInput = el("input", { type: "search", placeholder: "Search code, concepts or symbol names", "aria-label": "Search the repository", autocomplete: "off" });
  const kindSelect = el("select", { "aria-label": "Search type" },
    el("option", { value: "code", text: "Code search" }), el("option", { value: "symbols", text: "Symbols" }));
  const open = (path, line = 1, end = line) => openSource(path, line, end, repo.id);
  let ticket = 0;

  async function runSearch() {
    const q = queryInput.value.trim();
    const mine = ++ticket;
    if (!q) { results.replaceChildren(el("p", { class: "muted", text: "Type to search. Code search combines keyword (FTS) and embedding similarity; symbol search matches names." })); return; }
    results.replaceChildren(spinner("Searching"));
    try {
      if (kindSelect.value === "symbols") {
        const symbols = await api.symbols(repo.id, q, "", 100);
        if (mine !== ticket) return;
        results.replaceChildren(symbols.length ? el("div", { class: "card table-wrap" }, el("table", {},
          el("thead", {}, el("tr", {}, ["Symbol", "Kind", "Location"].map((h) => el("th", { text: h })))),
          el("tbody", {}, symbols.map((s) => el("tr", {},
            el("td", {}, el("code", { text: s.name }), s.parent ? el("div", { class: "small muted", text: s.parent }) : null),
            el("td", {}, badge(s.kind)),
            el("td", {}, el("button", { type: "button", class: "chip", onclick: () => open(s.path, s.start_line, s.end_line) }, `${s.path}:${s.start_line}`))))))) : emptyState("No matching symbols"));
      } else {
        const hits = await api.search(repo.id, q, 12);
        if (mine !== ticket) return;
        results.replaceChildren(...(hits.length ? hits.map((h) => el("article", { class: "card stack" },
          el("div", { class: "row between" },
            el("button", { type: "button", class: "chip", onclick: () => open(h.path, h.start_line, h.end_line) }, `${h.path}:${h.start_line}-${h.end_line}`),
            el("div", { class: "row" }, h.symbol ? badge(h.symbol) : null, badge(h.via, "info", "How this result was found"))),
          el("pre", { class: "mono small", style: "white-space:pre-wrap;overflow-x:auto;margin:0", text: h.preview }))) : [emptyState("Nothing matched", "Try different words, or use the symbol search for exact names.")]));
      }
    } catch (err) {
      if (mine === ticket) results.replaceChildren(errorBox(err.friendly || err.message));
    }
  }
  const debounced = debounce(runSearch, 300);
  queryInput.addEventListener("input", debounced);
  kindSelect.addEventListener("change", runSearch);

  root.append(el("div", { class: "split" },
    el("div", { class: "stack" }, el("h2", { text: "Files" }), treeBox),
    el("div", { class: "stack" }, el("h2", { text: "Search" }),
      el("form", { class: "row", onsubmit: (e) => { e.preventDefault(); runSearch(); } }, queryInput, kindSelect, button("Search", { type: "submit", variant: "primary", iconName: "search" })),
      results)));
  runSearch();

  try {
    const files = await api.files(repo.id);
    if (state.repoId !== repo.id) return undefined;
    clear(treeBox);
    treeBox.append(el("p", { class: "small muted", text: `${fmtNum(files.length)} indexed files. Select one to read it with line numbers.` }), ...renderTree(buildTree(files), open));
  } catch (err) {
    treeBox.replaceChildren(errorBox(err.friendly || err.message));
  }
  return () => { ticket++; };
}
