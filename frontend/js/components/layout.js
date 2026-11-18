import { button, el, emptyState } from "../dom.js";
import { currentRepo, isIndexed } from "../state.js";

export const navigate = (route) => { location.hash = `#/${route}`; };

export function viewHead(title, blurb, ...actions) {
  return el("div", { class: "view-head" },
    el("div", {}, el("h1", { text: title }), blurb ? el("p", { text: blurb }) : null),
    actions.length ? el("div", { class: "row" }, actions) : null);
}

// Renders an explanatory empty state and returns null when the view cannot work yet.
export function repoGate(root, { indexed = true } = {}) {
  const repo = currentRepo();
  if (!repo) {
    root.append(emptyState("No repository selected", "Register a local source repository, then index it so the tutor can learn it.",
      button("Add a repository", { variant: "primary", onclick: () => navigate("repos") })));
    return null;
  }
  if (indexed && !isIndexed(repo)) {
    root.append(emptyState(`${repo.name} has not been indexed yet`, "Indexing reads the repository once and keeps the knowledge base in sync afterwards.",
      button("Open My Repositories", { variant: "primary", onclick: () => navigate("repos") })));
    return null;
  }
  return repo;
}

export function section(title, ...children) {
  return el("section", { class: "card" }, el("h2", { text: title }), children);
}
