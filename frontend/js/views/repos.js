import { api } from "../api.js";
import { badge, button, clear, confirmDialog, el, emptyState, errorBox, fmtNum, labelled, spinner, timeAgo, toast } from "../dom.js";
import { refreshRepos, trackIndex } from "../data.js";
import { viewHead } from "../components/layout.js";
import { isIndexed, on, setRepo, state } from "../state.js";

function indexProgress(status) {
  if (!status || status.state === "idle") return null;
  const running = status.state === "running";
  const pct = status.files_total ? Math.round((status.files_done / status.files_total) * 100) : 0;
  const nodes = [];
  if (running) {
    nodes.push(el("div", { class: "small muted", text: `${status.phase}: ${fmtNum(status.files_done)} of ${fmtNum(status.files_total)} files` }),
      el("div", { class: "progress", role: "progressbar", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 }, el("span", { style: `width:${pct}%` })));
  } else if (status.state === "error") {
    nodes.push(el("div", { class: "alert alert-error", role: "alert", text: `Indexing failed: ${status.error}` }));
  } else {
    nodes.push(el("div", { class: "small muted", text: `Last run: ${status.added} added, ${status.changed} changed, ${status.removed} removed, ${status.unchanged} unchanged` +
      (status.embedded != null ? `, ${status.embedded} chunks embedded` : "") }));
  }
  for (const w of (status.warnings || []).slice(0, 3)) nodes.push(el("div", { class: "alert alert-warn", role: "note", text: w }));
  return el("div", { class: "stack" }, nodes);
}

export async function mount(root) {
  const list = el("div", { class: "stack" });
  const discoverOut = el("div", { class: "stack" });
  const pathInput = el("input", { type: "text", placeholder: "C:\\work\\my-service  or  /repos/my-service", autocomplete: "off", spellcheck: false });
  const nameInput = el("input", { type: "text", placeholder: "Optional display name", autocomplete: "off" });
  const rootInput = el("input", { type: "text", placeholder: "Folder that contains several repositories", autocomplete: "off", spellcheck: false });

  async function register(path, name) {
    try {
      const repo = await api.addRepo(path, name);
      await refreshRepos();
      setRepo(repo.id);
      toast(`Added ${repo.name}. Index it to start learning.`, "success");
      return true;
    } catch (err) {
      toast(err.friendly || err.message, "error");
      return false;
    }
  }

  const addForm = el("form", { class: "card", onsubmit: async (event) => {
    event.preventDefault();
    if (!pathInput.value.trim()) { pathInput.focus(); return; }
    if (await register(pathInput.value.trim(), nameInput.value.trim())) { pathInput.value = nameInput.value = ""; }
  } },
    el("h2", { text: "Add a repository" }),
    labelled("Local path", pathInput, "The folder is read in place and never modified. In Docker, use the path as mounted inside the container (for example /repos/my-service)."),
    labelled("Name", nameInput),
    button("Add repository", { variant: "primary", type: "submit", iconName: "plus" }));

  const discoverForm = el("form", { class: "card", onsubmit: async (event) => {
    event.preventDefault();
    const rootPath = rootInput.value.trim();
    if (!rootPath) { rootInput.focus(); return; }
    discoverOut.replaceChildren(spinner("Scanning for repositories\u2026"));
    try {
      const found = await api.discover(rootPath);
      if (!found.repositories.length) { discoverOut.replaceChildren(el("p", { class: "muted", text: `No Git repositories found under ${found.root}.` })); return; }
      discoverOut.replaceChildren(el("p", { class: "small muted", text: `${found.repositories.length} repositories under ${found.root}` }),
        el("ul", { class: "list" }, found.repositories.map((r) => el("li", { class: "list-item" },
          el("div", { class: "grow" }, el("div", { class: "title", text: r.name }), el("div", { class: "small muted mono", text: r.path })),
          r.registered ? badge("Added", "ok") : button("Add", { small: true, onclick: async (e) => {
            const btn = e.currentTarget;
            btn.disabled = true;
            if (!(await register(r.path, r.name))) btn.disabled = false;
          } })))));
    } catch (err) {
      discoverOut.replaceChildren(errorBox(err.friendly || err.message));
    }
  } },
    el("h2", { text: "Discover repositories" }),
    labelled("Parent folder", rootInput, "Finds Git repositories below this folder so you can add several at once."),
    button("Scan folder", { type: "submit", iconName: "search" }), discoverOut);

  root.append(viewHead("My Repositories", "Register the source repositories you want to learn. Indexing is incremental: only new or changed files are re-read."),
    el("div", { class: "grid cols-2" }, addForm, discoverForm), el("h2", { text: "Registered repositories" }), list);

  function paint() {
    clear(list);
    if (!state.repos.length) {
      list.append(emptyState("No repositories yet", "Add one above to get started."));
      return;
    }
    for (const repo of state.repos) {
      const status = state.indexStatus[repo.id];
      const running = status && status.state === "running";
      const selected = repo.id === state.repoId;
      list.append(el("article", { class: "card stack" },
        el("div", { class: "row between" },
          el("div", {}, el("h3", { text: repo.name }), el("div", { class: "small muted mono", text: repo.path })),
          el("div", { class: "row" }, selected ? badge("Selected", "info") : button("Select", { small: true, onclick: () => setRepo(repo.id) }),
            isIndexed(repo) ? badge("Indexed", "ok") : badge("Not indexed", "warn"))),
        isIndexed(repo) ? el("div", { class: "small muted", text: `${fmtNum(repo.file_count)} files \u00b7 ${fmtNum(repo.chunk_count)} chunks \u00b7 ${fmtNum(repo.symbol_count)} symbols \u00b7 indexed ${timeAgo(repo.last_indexed_at)}` }) : null,
        indexProgress(status),
        el("div", { class: "row" },
          button(isIndexed(repo) ? "Update index" : "Index now", { variant: "primary", small: true, iconName: "refresh", disabled: running, onclick: async () => {
            try { await api.startIndex(repo.id, false); trackIndex(repo.id); } catch (err) { toast(err.friendly || err.message, "error"); }
          } }),
          isIndexed(repo) ? button("Full re-index", { small: true, disabled: running, title: "Re-read every file and rebuild embeddings", onclick: async () => {
            try { await api.startIndex(repo.id, true); trackIndex(repo.id); } catch (err) { toast(err.friendly || err.message, "error"); }
          } }) : null,
          button("Remove", { small: true, variant: "ghost", iconName: "trash", disabled: running, onclick: async () => {
            const ok = await confirmDialog({ title: `Remove ${repo.name}?`, message: "This deletes the tutor's index, explanations and history for this repository. Your source files are not touched.", confirmText: "Remove", danger: true });
            if (!ok) return;
            try { await api.deleteRepo(repo.id); await refreshRepos(); toast("Repository removed", "success"); } catch (err) { toast(err.friendly || err.message, "error"); }
          } }))));
    }
  }

  paint();
  const offs = [on("repos", paint), on("index", paint)];
  return () => offs.forEach((off) => off());
}
