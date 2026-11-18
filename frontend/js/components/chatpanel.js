// Conversation panel shared by Explain, Request Flow, Compare, Feature deep dive and Troubleshooting.
import { api } from "../api.js";
import { button, el, errorBox, spinner, toast } from "../dom.js";
import { currentRepo, isIndexed, state } from "../state.js";
import { assistantMessage, userMessage } from "./message.js";

const REGENERATABLE = new Set(["explain", "flow", "feature", "compare"]);

export function chatPanel({ mode, placeholder, submitLabel = "Ask", examples = [], compare = false, rows = 3, intro, minChars = 3 }) {
  let sessionId = null;
  let busy = false;
  let controller = null;

  const log = el("div", { class: "chat", "aria-live": "polite" });
  const input = el("textarea", { rows, placeholder, "aria-label": "Your question" });
  const second = compare ? el("textarea", { rows: 2, placeholder: "Second concept to compare with, for example: Mutating webhook", "aria-label": "Compare with" }) : null;
  const send = button(submitLabel, { variant: "primary", iconName: "send", type: "submit" });
  const cancel = button("Cancel", { variant: "secondary", onclick: () => controller && controller.abort(), hidden: true });
  const restart = button("New conversation", { variant: "ghost", small: true, iconName: "plus", onclick: () => reset() });
  const introBox = intro ? el("div", { class: "card" }, intro) : null;
  const depthNote = el("span", { class: "hint" });

  const form = el("form", { class: "composer", onsubmit: (event) => { event.preventDefault(); ask(input.value, { compareWith: second && second.value }); } },
    el("div", { class: "card stack" },
      examples.length ? el("div", { class: "chips", role: "group", "aria-label": "Example questions" }, examples.map((text) => el("button", {
        type: "button", class: "chip", onclick: () => { input.value = text; input.focus(); },
      }, text))) : null,
      input, second,
      el("div", { class: "row between" }, depthNote, el("div", { class: "row" }, restart, cancel, send))));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); form.requestSubmit(); }
  });

  const root = el("div", { class: "stack" }, introBox, log, form);

  function refreshHints() {
    depthNote.textContent = `Depth: ${state.depth} (change in the header). Ctrl+Enter to send.`;
  }
  refreshHints();

  function setBusy(value) {
    busy = value;
    input.disabled = send.disabled = value;
    if (second) second.disabled = value;
    cancel.hidden = !value;
  }

  function scrollTo(node) {
    node.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  async function ask(question, { compareWith = "", force = false } = {}) {
    question = (question || "").trim();
    if (busy) return;
    const repo = currentRepo();
    if (!repo || !isIndexed(repo)) { toast("Select an indexed repository first.", "error"); return; }
    if (question.length < minChars && !state.featureId) { toast("Please enter a question or topic.", "error"); input.focus(); return; }
    if (compare && !compareWith.trim()) { toast("Enter the second concept to compare with.", "error"); second.focus(); return; }
    refreshHints();

    if (introBox) introBox.hidden = true;
    log.append(userMessage(compare ? `${question}  vs  ${compareWith.trim()}` : question || "(selected feature)"));
    const pending = el("div", { class: "msg" }, spinner("Retrieving code and preparing an answer\u2026"));
    log.append(pending);
    scrollTo(pending);
    controller = new AbortController();
    setBusy(true);
    try {
      const result = await api.learn({
        repo_id: repo.id, mode, question, depth: state.depth, feature_id: state.featureId,
        compare_with: compare ? compareWith.trim() : null, session_id: sessionId, force,
      }, controller.signal);
      sessionId = result.session_id;
      const node = assistantMessage({
        content: result.answer, sources: result.sources, provider: result.provider, cached: result.cached,
        warnings: result.warnings, citations: result.citations, title: question || "Explanation", repoId: repo.id,
        followUp: result.follow_up,
        onRegenerate: REGENERATABLE.has(mode) ? () => ask(question, { compareWith, force: true }) : null,
      });
      pending.replaceWith(node);
      input.value = "";
      scrollTo(node);
    } catch (err) {
      if (err.name === "AbortError") {
        pending.replaceWith(el("div", { class: "msg" }, el("p", { class: "note", text: "Request cancelled." })));
      } else {
        const retry = button("Retry", { variant: "secondary", small: true, onclick: () => { pending.remove(); ask(question, { compareWith, force }); } });
        pending.replaceWith(errorBox(err.friendly || err.message, retry));
      }
    } finally {
      controller = null;
      setBusy(false);
      input.focus();
    }
  }

  function reset() {
    if (controller) controller.abort();
    sessionId = null;
    log.replaceChildren();
    if (introBox) introBox.hidden = false;
    input.value = "";
    if (second) second.value = "";
    input.focus();
  }

  async function loadSession(id) {
    const session = await api.session(id);
    sessionId = session.id;
    if (introBox) introBox.hidden = true;
    log.replaceChildren(...session.messages.map((m) => (m.role === "user"
      ? userMessage(m.content)
      : assistantMessage({
        content: m.content, sources: m.sources, cached: m.meta.cached, warnings: m.meta.warnings || [],
        provider: m.meta.provider ? { provider: m.meta.provider, model: m.meta.model, mock: !!m.meta.mock } : null,
        title: session.title, repoId: session.repo_id,
      }))));
    log.lastElementChild?.scrollIntoView({ block: "end" });
  }

  return { root, ask, reset, loadSession, input, setQuestion: (text) => { input.value = text; }, abort: () => controller && controller.abort() };
}
