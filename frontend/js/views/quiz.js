import { api } from "../api.js";
import { append, badge, button, clear, el, emptyState, errorBox, labelled, spinner, timeAgo } from "../dom.js";
import { repoGate, viewHead } from "../components/layout.js";
import { openSource } from "../components/drawer.js";
import { providerBadge, sourceList } from "../components/message.js";
import { renderMarkdown } from "../components/markdown.js";
import { currentFeature, on, state } from "../state.js";

const scoreKind = (score) => (score >= 7 ? "ok" : score >= 4 ? "warn" : "err");

// Questions reference a retrieved source by id (for example "S2"); turn it into a link to the code.
function sourceChip(q, ctx) {
  const src = (ctx.sources || []).find((s) => s.id === q.source);
  if (!src) return null;
  return el("button", { type: "button", class: "chip mono", title: "Open the code this question comes from",
    onclick: () => openSource(src.path, src.start_line, src.end_line, ctx.repo.id) }, `${src.path}:${src.start_line}`);
}

function questionCard(q, index, ctx) {
  const answer = el("textarea", { rows: 4, maxlength: 8000, placeholder: "Answer in your own words, as you would to a teammate.", "aria-label": `Answer to question ${index + 1}` });
  const result = el("div", { class: "stack" });
  const submit = button("Evaluate my answer", { variant: "primary", small: true, iconName: "check", onclick: async () => {
    if (!answer.value.trim()) { answer.focus(); return; }
    submit.disabled = answer.disabled = true;
    result.replaceChildren(spinner("Evaluating"));
    try {
      const r = await api.evaluate({ repo_id: ctx.repo.id, question: q.question, answer: answer.value.trim(), expected_points: q.expected_points || [], topic: ctx.topic() });
      // append() skips null children; a bare replaceChildren() would print the text "null".
      append(clear(result), [
        el("div", { class: "row" }, badge(`Score ${r.score}/10`, scoreKind(r.score)), badge(r.method === "llm" ? "Graded by the language model" : "Graded against expected points", "", "How this score was calculated")),
        el("div", { class: "md" }, el("p", { text: r.feedback })),
        r.gaps && r.gaps.length ? el("div", {}, el("strong", { text: "Gaps to revisit" }), el("ul", {}, r.gaps.map((g) => el("li", { text: g })))) : null,
        r.model_answer ? el("details", {}, el("summary", { text: "Model answer" }), renderMarkdown(r.model_answer, { sources: r.sources || [], repoId: ctx.repo.id })) : null,
        sourceList(r.sources, ctx.repo.id)]);
      ctx.onScored();
    } catch (err) {
      submit.disabled = answer.disabled = false;
      result.replaceChildren(errorBox(err.friendly || err.message));
    }
  } });
  return el("article", { class: "card stack" },
    el("div", { class: "row between" }, el("h3", { text: `Question ${index + 1}` }), sourceChip(q, ctx)),
    el("p", { text: q.question }), answer, el("div", { class: "row" }, submit), result);
}

export function mount(root) {
  root.append(viewHead("Quiz & Interview", "Check your understanding. Questions are drawn from the code; answers are scored and your gaps are tracked on the dashboard."));
  const repo = repoGate(root);
  if (!repo) return undefined;

  let mode = "quiz";
  let alive = true;
  const topic = el("input", { type: "text", maxlength: 2000, placeholder: "Topic (optional if a feature is selected in the header)", autocomplete: "off" });
  const count = el("select", { "aria-label": "Number of questions" }, [3, 5, 8, 10].map((n) => el("option", { value: n, text: `${n} questions`, selected: n === 5 })));
  const featureNote = el("p", { class: "hint" });
  const out = el("div", { class: "stack" });
  const history = el("div", { class: "stack" });
  const tabs = el("div", { class: "tabs", role: "tablist" });
  const TABS = [["quiz", "Quiz"], ["interview", "Mock interview"], ["past", "Past results"]];
  const form = el("form", { class: "card stack", onsubmit: (e) => { e.preventDefault(); generate(); } },
    labelled("Topic", topic), featureNote,
    el("div", { class: "row" }, count, button("Generate questions", { variant: "primary", type: "submit", iconName: "quiz" })));

  const paintFeature = () => {
    const f = currentFeature();
    featureNote.textContent = f ? `Questions will focus on feature "${f.name}".` : "Choose a feature in the header to focus the questions.";
  };

  async function loadPast() {
    history.replaceChildren(spinner());
    try {
      const rows = await api.quizResults(repo.id);
      if (!alive) return;
      history.replaceChildren(rows.length ? el("div", { class: "card table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, ["When", "Question", "Score", "Gaps"].map((h) => el("th", { text: h })))),
        el("tbody", {}, rows.map((r) => el("tr", {},
          el("td", { text: r.created_at ? timeAgo(r.created_at) : "" }),
          el("td", { text: r.question }),
          el("td", {}, badge(`${r.score}/10`, scoreKind(r.score))),
          el("td", { text: (r.gaps || []).join("; ") })))))) : emptyState("No attempts yet", "Generate a quiz and answer a question to start tracking progress."));
    } catch (err) {
      if (alive) history.replaceChildren(errorBox(err.friendly || err.message));
    }
  }

  let ticket = 0;
  async function generate() {
    const mine = ++ticket;
    if (!topic.value.trim() && !state.featureId) { topic.focus(); out.replaceChildren(errorBox("Enter a topic, or choose a feature in the header.")); return; }
    out.replaceChildren(spinner("Writing questions from the code\u2026"));
    try {
      const r = await api.quiz({ repo_id: repo.id, topic: topic.value.trim(), count: parseInt(count.value, 10), feature_id: state.featureId, interview: mode === "interview" });
      if (mine !== ticket) return;
      const ctx = { repo, sources: r.sources, topic: () => topic.value.trim() || r.topic || "", onScored: () => {} };
      out.replaceChildren(
        el("div", { class: "row" }, el("strong", { text: r.topic || "Quiz" }), providerBadge(r.provider)),
        ...(r.warnings || []).map((w) => el("div", { class: "alert alert-warn", role: "note", text: w })),
        ...(r.questions.length ? r.questions.map((q, i) => questionCard(q, i, ctx)) : [emptyState("No questions could be generated", "Index more of the repository or try a broader topic.")]));
    } catch (err) {
      if (mine === ticket) out.replaceChildren(errorBox(err.friendly || err.message));
    }
  }

  function paintTabs() {
    tabs.replaceChildren(...TABS.map(([id, label]) => el("button", {
      type: "button", class: "tab", role: "tab", "aria-selected": String(id === mode), text: label,
      onclick: () => { mode = id; paintTabs(); paintMode(); },
    })));
  }
  function paintMode() {
    const past = mode === "past";
    count.hidden = mode === "interview"; // an interview round asks one open-ended question at a time
    form.hidden = out.hidden = past;
    history.hidden = !past;
    if (past) loadPast();
    else out.replaceChildren(el("p", { class: "muted", text: mode === "interview"
      ? "Mock interview questions are open-ended and probe design reasons and trade-offs, the way an interviewer would."
      : "Quiz questions check that you can explain how the code works." }));
  }

  root.append(tabs, form, out, history);
  paintTabs();
  paintFeature();
  paintMode();
  const off = on("feature", paintFeature);
  return () => { alive = false; ticket++; off(); };
}
