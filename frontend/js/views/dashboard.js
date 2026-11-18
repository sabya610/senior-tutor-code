import { api } from "../api.js";
import { badge, button, el, emptyState, errorBox, fmtNum, spinner, timeAgo } from "../dom.js";
import { navigate, viewHead } from "../components/layout.js";
import { isIndexed, setHandoff, setRepo, state } from "../state.js";

const MODE_LABEL = { explain: "Explain", flow: "Request flow", feature: "Feature", compare: "Compare", chat: "Chat", troubleshoot: "Troubleshooting", prep: "Session prep" };

function stat(value, label) {
  return el("div", { class: "card" }, el("div", { class: "stat", text: value }), el("div", { class: "stat-label", text: label }));
}

export async function mount(root) {
  root.append(viewHead("Dashboard", "Your personal senior engineering mentor. Everything it tells you is grounded in the code you index."), spinner());
  let data;
  try {
    data = await api.dashboard();
  } catch (err) {
    root.replaceChildren(viewHead("Dashboard"), errorBox(err.friendly || err.message));
    return;
  }
  const indexed = data.repos.filter(isIndexed);
  const body = el("div", { class: "stack" });

  body.append(el("div", { class: "grid cols-3" },
    stat(fmtNum(data.repos.length), "Repositories registered"),
    stat(fmtNum(indexed.length), "Repositories indexed"),
    stat(fmtNum(data.repos.reduce((n, r) => n + (r.symbol_count || 0), 0)), "Symbols known"),
    stat(data.quiz.average_score == null ? "-" : `${data.quiz.average_score}/10`, `Average quiz score (${data.quiz.attempts} attempts)`)));

  if (!data.repos.length) {
    body.append(emptyState("Start by adding a repository", "Point the tutor at a local source repository. It will index the code, then you can ask about any feature.",
      button("Add a repository", { variant: "primary", onclick: () => navigate("repos") })));
  } else {
    body.append(el("section", { class: "card" }, el("h2", { text: "Jump in" }),
      el("div", { class: "row" },
        button("Explain code", { variant: "primary", onclick: () => navigate("explain") }),
        button("Trace a request flow", { onclick: () => navigate("flow") }),
        button("Prepare a session", { onclick: () => navigate("prep") }),
        button("Quiz me", { onclick: () => navigate("quiz") }),
        button("Troubleshoot", { onclick: () => navigate("troubleshoot") }))));
    body.append(el("section", { class: "card" }, el("h2", { text: "Repositories" }),
      el("ul", { class: "list" }, data.repos.map((repo) => el("li", { class: "list-item" },
        el("div", { class: "grow" }, el("div", { class: "title", text: repo.name }),
          el("div", { class: "small muted", text: isIndexed(repo) ? `${fmtNum(repo.file_count)} files \u00b7 ${fmtNum(repo.symbol_count)} symbols \u00b7 indexed ${timeAgo(repo.last_indexed_at)}` : "Not indexed yet" })),
        button(isIndexed(repo) ? "Open" : "Index", { small: true, onclick: () => { setRepo(repo.id); navigate(isIndexed(repo) ? "architecture" : "repos"); } }))))));
  }

  const recent = el("div", { class: "grid cols-2" },
    el("section", { class: "card" }, el("h2", { text: "Recent learning sessions" }),
      data.recent_sessions.length ? el("ul", { class: "list" }, data.recent_sessions.map((s) => el("li", { class: "list-item" },
        el("div", { class: "grow" }, el("div", { class: "title", text: s.title }),
          el("div", { class: "small muted", text: `${s.repo_name || "-"} \u00b7 ${timeAgo(s.updated_at)}` })),
        badge(MODE_LABEL[s.mode] || s.mode)))) : el("p", { class: "muted", text: "No sessions yet." }),
      el("div", { class: "row" }, button("All history", { variant: "ghost", small: true, onclick: () => navigate("history") }))),
    el("section", { class: "card" }, el("h2", { text: "Knowledge gaps to revisit" }),
      data.quiz.top_gaps.length ? el("ul", { class: "list" }, data.quiz.top_gaps.map((g) => el("li", { class: "list-item" },
        el("span", { text: g.gap }), badge(`\u00d7${g.count}`, "warn")))) : el("p", { class: "muted", text: "Take a quiz or an interview round to find gaps." }),
      el("div", { class: "row" }, button("Start a quiz", { variant: "ghost", small: true, onclick: () => { setHandoff(null); navigate("quiz"); } }))));
  body.append(recent);

  root.replaceChildren(viewHead("Dashboard", "Your personal senior engineering mentor. Everything it tells you is grounded in the code you index.",
    badge(data.llm.mock ? "Evidence-only mode" : `${data.llm.provider} \u00b7 ${data.llm.model}`, data.llm.mock ? "warn" : "ok")), body);
}
