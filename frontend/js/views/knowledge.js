import { api } from "../api.js";
import { badge, button, confirmDialog, copyText, downloadText, el, emptyState, errorBox, slug, spinner, timeAgo, toast } from "../dom.js";
import { viewHead } from "../components/layout.js";
import { sourceList } from "../components/message.js";
import { renderMarkdown } from "../components/markdown.js";
import { state } from "../state.js";

const MODES = [["", "All types"], ["explain", "Explain"], ["flow", "Request flow"], ["feature", "Feature"], ["compare", "Compare"], ["troubleshoot", "Troubleshooting"], ["prep", "Session prep"], ["architecture", "Architecture"]];

function entryCard(title, content, sources, repoId, extra = [], onDelete) {
  return el("article", { class: "card stack" },
    el("div", { class: "row between" }, el("h3", { text: title }),
      el("div", { class: "row" }, extra,
        button("Copy", { variant: "ghost", small: true, iconName: "copy", onclick: () => copyText(content).then(() => toast("Copied", "success")) }),
        button("Download .md", { variant: "ghost", small: true, iconName: "download", onclick: () => downloadText(`${slug(title)}.md`, content) }),
        button("Delete", { variant: "ghost", small: true, iconName: "trash", onclick: onDelete }))),
    renderMarkdown(content, { sources, repoId }),
    sourceList(sources, repoId));
}

export function mount(root) {
  root.append(viewHead("Knowledge Base", "Everything the tutor has explained for you, plus the answers you bookmarked. Entries are flagged when the code they cite has changed."));
  let tab = "explanations";
  let alive = true;
  const tabs = el("div", { class: "tabs", role: "tablist" });
  const modeSelect = el("select", { "aria-label": "Filter by type" }, MODES.map(([v, t]) => el("option", { value: v, text: t })));
  const scope = el("select", { "aria-label": "Repository scope" },
    el("option", { value: "repo", text: "Current repository" }), el("option", { value: "all", text: "All repositories" }));
  const filters = el("div", { class: "row" }, modeSelect, scope);
  const body = el("div", { class: "stack" });

  const repoParam = () => (scope.value === "repo" ? state.repoId : undefined);

  async function loadExplanations() {
    body.replaceChildren(spinner());
    try {
      const rows = await api.knowledge(repoParam(), modeSelect.value || undefined);
      if (!alive) return;
      body.replaceChildren(...(rows.length ? rows.map((row) => {
        const holder = el("div", { class: "stack" });
        const header = el("div", { class: "list-item" },
          el("div", { class: "grow" }, el("div", { class: "title", text: row.topic || "(untitled)" }),
            el("div", { class: "small muted", text: row.preview.replace(/[#*`>|_-]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 160) })),
          el("div", { class: "row" }, badge(row.mode), row.stale ? badge("Code changed", "warn", "A file this explanation cites has changed since it was written") : null,
            el("span", { class: "small muted", text: timeAgo(row.created_at) }),
            button("Open", { small: true, onclick: async (event) => {
              if (holder.childElementCount) { holder.replaceChildren(); return; }
              const btn = event.currentTarget;
              btn.disabled = true;
              try {
                const full = await api.knowledgeItem(row.id);
                holder.replaceChildren(entryCard(full.topic || "Explanation", full.content, full.sources, full.repo_id,
                  [full.provider ? badge(full.provider, "info") : null],
                  async () => {
                    if (!(await confirmDialog({ title: "Delete this entry?", message: "It will be regenerated the next time you ask.", confirmText: "Delete", danger: true }))) return;
                    try { await api.deleteKnowledge(row.id); toast("Deleted", "success"); loadExplanations(); } catch (err) { toast(err.friendly || err.message, "error"); }
                  }));
              } catch (err) {
                toast(err.friendly || err.message, "error");
              } finally {
                btn.disabled = false;
              }
            } })));
        return el("div", { class: "stack" }, header, holder);
      }) : [emptyState("Nothing saved yet", "Answers you generate are stored here automatically.")]));
    } catch (err) {
      if (alive) body.replaceChildren(errorBox(err.friendly || err.message));
    }
  }

  async function loadBookmarks() {
    body.replaceChildren(spinner());
    try {
      const rows = await api.bookmarks(repoParam());
      if (!alive) return;
      body.replaceChildren(...(rows.length ? rows.map((b) => entryCard(b.title, b.content, b.sources || [], b.repo_id,
        [el("span", { class: "small muted", text: timeAgo(b.created_at) })],
        async () => {
          try { await api.deleteBookmark(b.id); toast("Bookmark removed", "success"); loadBookmarks(); } catch (err) { toast(err.friendly || err.message, "error"); }
        })) : [emptyState("No bookmarks", "Use Bookmark under any answer to keep it here.")]));
    } catch (err) {
      if (alive) body.replaceChildren(errorBox(err.friendly || err.message));
    }
  }

  function paint() {
    tabs.replaceChildren(...[["explanations", "Explanations"], ["bookmarks", "Bookmarks"]].map(([id, label]) => el("button", {
      type: "button", class: "tab", role: "tab", "aria-selected": String(id === tab), text: label,
      onclick: () => { tab = id; paint(); },
    })));
    modeSelect.hidden = tab !== "explanations";
    (tab === "explanations" ? loadExplanations : loadBookmarks)();
  }

  modeSelect.addEventListener("change", loadExplanations);
  scope.addEventListener("change", paint);
  root.append(tabs, filters, body);
  paint();
  return () => { alive = false; };
}
