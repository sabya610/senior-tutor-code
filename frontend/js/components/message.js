// Chat message building blocks shared by the learning views, history and knowledge base.
import { api } from "../api.js";
import { badge, button, copyText, downloadText, el, slug, toast, truncate } from "../dom.js";
import { state } from "../state.js";
import { renderMarkdown } from "./markdown.js";

export function userMessage(text) {
  return el("article", { class: "msg msg-user" }, el("div", { class: "msg-role", text: "You" }),
    el("div", { class: "md" }, el("p", { text })));
}

export function providerBadge(provider) {
  if (!provider) return null;
  if (provider.mock) return badge("Evidence-only (no LLM)", "warn", "Answer assembled from retrieved code without a language model");
  return badge(`${provider.provider} \u00b7 ${provider.model}`, "info", "Language model that wrote this answer");
}

export function sourceList(sources, repoId) {
  if (!sources || !sources.length) return null;
  return el("details", { class: "sources" },
    el("summary", { text: `Sources (${sources.length})` }),
    el("div", { class: "source-list" }, sources.map((s) => el("button", {
      type: "button", class: "source-btn",
      onclick: async () => (await import("./drawer.js")).openSource(s.path, s.start_line, s.end_line, repoId),
    }, `[${s.id}] ${s.path}:${s.start_line}-${s.end_line}${s.symbol ? " \u00b7 " + s.symbol : ""}`))));
}

export function assistantMessage({ content, sources = [], provider, cached, warnings = [], citations, title, repoId = state.repoId,
  followUp, onRegenerate, role = "Senior Tutor" }) {
  const actions = el("div", { class: "msg-actions" },
    button("Copy", { variant: "ghost", small: true, iconName: "copy", onclick: () => copyText(content).then(() => toast("Copied", "success")).catch((e) => toast(e.message, "error")) }),
    button("Download .md", { variant: "ghost", small: true, iconName: "download", onclick: () => downloadText(`${slug(title)}.md`, content) }),
    button("Bookmark", { variant: "ghost", small: true, iconName: "bookmark", onclick: async (event) => {
      const btn = event.currentTarget;
      btn.disabled = true;
      try {
        await api.addBookmark({ repo_id: repoId, title: truncate(title || "Explanation", 200), content, sources });
        toast("Bookmarked. Find it in Knowledge Base.", "success");
      } catch (err) {
        btn.disabled = false;
        toast(err.friendly || err.message, "error");
      }
    } }),
    onRegenerate ? button("Regenerate", { variant: "ghost", small: true, iconName: "refresh", onclick: onRegenerate }) : null,
  );
  const meta = el("div", { class: "msg-meta" },
    providerBadge(provider),
    cached ? badge("From knowledge base", "ok", "Reused a previous answer whose source files have not changed") : null,
    citations && !(provider && provider.mock) ? badge(`${citations.cited.length} of ${citations.available} sources cited`, citations.cited.length ? "ok" : "warn") : null);
  return el("article", { class: "msg" },
    el("div", { class: "msg-role", text: role }),
    followUp ? el("p", { class: "note", text: `Read as a follow-up about: ${followUp.topic}` }) : null,
    warnings.map((w) => el("div", { class: "alert alert-warn", role: "note", text: w })),
    renderMarkdown(content, { sources, repoId }),
    meta, sourceList(sources, repoId), actions);
}
