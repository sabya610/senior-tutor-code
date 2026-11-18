import { api } from "../api.js";
import { button, copyText, downloadText, el, errorBox, slug, spinner, toast } from "../dom.js";
import { repoGate, viewHead } from "../components/layout.js";
import { providerBadge, sourceList } from "../components/message.js";
import { renderMarkdown } from "../components/markdown.js";
import { state } from "../state.js";

export async function mount(root) {
  root.append(viewHead("Architecture Overview", "A map of the repository built from the indexed source, plus a mentor-style walkthrough of how the parts fit together."));
  const repo = repoGate(root);
  if (!repo) return undefined;

  const body = el("div", { class: "stack" });
  const tabs = [
    { id: "map", label: "Architecture map" },
    { id: "narrative", label: "Guided walkthrough" },
  ];
  const tabBar = el("div", { class: "tabs", role: "tablist" });
  const panel = el("div", { role: "tabpanel" });
  const cache = {};
  let active = "map";
  let alive = true;

  function paintTabs() {
    tabBar.replaceChildren(...tabs.map((t) => el("button", {
      type: "button", class: "tab", role: "tab", "aria-selected": String(t.id === active), text: t.label,
      onclick: () => { active = t.id; paintTabs(); show(); },
    })));
  }

  async function showMap() {
    panel.replaceChildren(spinner("Loading architecture map"));
    try {
      cache.map = cache.map || await api.architecture(repo.id);
      if (!alive) return;
      const { markdown } = cache.map;
      panel.replaceChildren(el("div", { class: "card stack" },
        el("div", { class: "row end" },
          button("Copy", { variant: "ghost", small: true, iconName: "copy", onclick: () => copyText(markdown).then(() => toast("Copied", "success")) }),
          button("Download .md", { variant: "ghost", small: true, iconName: "download", onclick: () => downloadText(`${slug(repo.name)}-architecture.md`, markdown) })),
        renderMarkdown(markdown, { repoId: repo.id })));
    } catch (err) {
      if (alive) panel.replaceChildren(errorBox(err.friendly || err.message));
    }
  }

  async function loadNarrative(force) {
    panel.replaceChildren(spinner(force ? "Regenerating walkthrough\u2026" : "Preparing walkthrough\u2026 this can take a minute with a local model"));
    try {
      const result = await api.narrative(repo.id, force);
      if (!alive) return;
      cache.narrative = result;
      paintNarrative();
    } catch (err) {
      if (alive) panel.replaceChildren(errorBox(err.friendly || err.message, button("Retry", { small: true, onclick: () => loadNarrative(force) })));
    }
  }

  function paintNarrative() {
    const r = cache.narrative;
    panel.replaceChildren(el("div", { class: "card stack" },
      el("div", { class: "row between" }, el("div", { class: "row" }, providerBadge(r.provider)),
        el("div", { class: "row" },
          button("Copy", { variant: "ghost", small: true, iconName: "copy", onclick: () => copyText(r.content).then(() => toast("Copied", "success")) }),
          button("Download .md", { variant: "ghost", small: true, iconName: "download", onclick: () => downloadText(`${slug(repo.name)}-walkthrough.md`, r.content) }),
          button("Regenerate", { variant: "ghost", small: true, iconName: "refresh", onclick: () => loadNarrative(true) }))),
      (r.warnings || []).map((w) => el("div", { class: "alert alert-warn", role: "note", text: w })),
      renderMarkdown(r.content, { sources: r.sources, repoId: repo.id }),
      sourceList(r.sources, repo.id)));
  }

  function show() {
    if (active === "map") return showMap();
    if (cache.narrative) { paintNarrative(); return undefined; }
    panel.replaceChildren(el("div", { class: "card stack" },
      el("p", { text: "A guided walkthrough explains what the project is for, how it is layered, and where to start reading. It is written from the indexed code and cites its sources." }),
      el("div", { class: "row" }, button("Generate walkthrough", { variant: "primary", iconName: "send", onclick: () => loadNarrative(false) }))));
    return undefined;
  }

  body.append(tabBar, panel);
  root.append(body);
  paintTabs();
  show();
  return () => { alive = false; };
}
