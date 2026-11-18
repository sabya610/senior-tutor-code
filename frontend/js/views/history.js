import { api } from "../api.js";
import { badge, button, confirmDialog, el, emptyState, errorBox, spinner, timeAgo, toast } from "../dom.js";
import { refreshFeatures } from "../data.js";
import { navigate, viewHead } from "../components/layout.js";
import { assistantMessage, userMessage } from "../components/message.js";
import { setFeature, setHandoff, setRepo, state } from "../state.js";

const MODE_LABEL = { explain: "Explain", flow: "Request flow", feature: "Feature", compare: "Compare", chat: "Chat", troubleshoot: "Troubleshooting", prep: "Session prep" };
const MODE_ROUTE = { explain: "explain", chat: "explain", flow: "flow", feature: "features", compare: "compare", troubleshoot: "troubleshoot" };

export function mount(root) {
  root.append(viewHead("Learning History", "Every conversation is saved. Reopen one to re-read it, or continue where you left off."));
  const list = el("div", { class: "stack" });
  const detail = el("div", { class: "stack" });
  const scope = el("select", { "aria-label": "Repository scope" },
    el("option", { value: "repo", text: "Current repository" }), el("option", { value: "all", text: "All repositories" }));
  let selectedId = null;
  let alive = true;
  let sessions = [];

  async function open(id) {
    selectedId = id;
    paintList();
    detail.replaceChildren(spinner());
    try {
      const s = await api.session(id);
      if (!alive || selectedId !== id) return;
      const messages = s.messages.map((m) => (m.role === "user" ? userMessage(m.content) : assistantMessage({
        content: m.content, sources: m.sources, cached: m.meta && m.meta.cached, warnings: (m.meta && m.meta.warnings) || [],
        provider: m.meta && m.meta.provider ? { provider: m.meta.provider, model: m.meta.model, mock: !!m.meta.mock } : null,
        title: s.title, repoId: s.repo_id,
      })));
      const route = MODE_ROUTE[s.mode];
      detail.replaceChildren(
        el("div", { class: "card row between" },
          el("div", {}, el("h2", { text: s.title }), el("div", { class: "small muted", text: `${MODE_LABEL[s.mode] || s.mode} \u00b7 ${timeAgo(s.updated_at)}` })),
          el("div", { class: "row" },
            route ? button("Continue", { variant: "primary", iconName: "send", onclick: () => resume(s, route) }) : null,
            button("Delete", { variant: "ghost", iconName: "trash", onclick: () => remove(s) }))),
        ...messages);
    } catch (err) {
      if (alive) detail.replaceChildren(errorBox(err.friendly || err.message));
    }
  }

  async function resume(s, route) {
    if (s.repo_id && s.repo_id !== state.repoId) {
      setRepo(s.repo_id);
      await refreshFeatures();
    }
    if (s.mode === "feature" && s.feature) {
      const match = state.features.find((f) => f.name === s.feature);
      if (match) setFeature(match.id);
    }
    setHandoff({ kind: route, sessionId: s.id });
    navigate(route);
  }

  async function remove(s) {
    const ok = await confirmDialog({ title: "Delete this conversation?", message: `"${s.title}" will be removed from your history. Saved bookmarks are kept.`, confirmText: "Delete", danger: true });
    if (!ok) return;
    try {
      await api.deleteSession(s.id);
      toast("Conversation deleted", "success");
      selectedId = null;
      detail.replaceChildren(emptyState("Select a conversation"));
      load();
    } catch (err) {
      toast(err.friendly || err.message, "error");
    }
  }

  function paintList() {
    if (!sessions.length) {
      list.replaceChildren(emptyState("No conversations yet", "Ask something in Explain Code, Request Flow or Troubleshooting and it will appear here."));
      return;
    }
    list.replaceChildren(el("ul", { class: "list" }, sessions.map((s) => el("li", {}, el("button", {
      type: "button", class: "list-item", style: "width:100%;text-align:left;cursor:pointer;background:var(--panel)", "aria-pressed": String(s.id === selectedId),
      onclick: () => open(s.id),
    }, el("div", { class: "grow" }, el("div", { class: "title", text: s.title }),
      el("div", { class: "small muted", text: `${s.repo_name || "-"} \u00b7 ${s.message_count} messages \u00b7 ${timeAgo(s.updated_at)}` })),
    badge(MODE_LABEL[s.mode] || s.mode, s.id === selectedId ? "info" : ""))))));
  }

  async function load() {
    list.replaceChildren(spinner());
    try {
      sessions = await api.sessions(scope.value === "repo" ? state.repoId : undefined);
      if (alive) paintList();
    } catch (err) {
      if (alive) list.replaceChildren(errorBox(err.friendly || err.message));
    }
  }

  scope.addEventListener("change", load);
  detail.append(emptyState("Select a conversation", "Pick one on the left to read it."));
  root.append(el("div", { class: "split" }, el("div", { class: "stack" }, scope, list), detail));
  load();
  return () => { alive = false; };
}
