// Application shell: navigation, context selectors (repository / feature / depth) and the hash router.
import { api } from "./api.js";
import { initDrawer } from "./components/drawer.js";
import { refreshFeatures, refreshRepos, trackIndex } from "./data.js";
import { $, badge, el, errorBox, icon, spinner, toast } from "./dom.js";
import {
  DEPTHS, currentRepo, effectiveTheme, isIndexed, on, setDepth, setFeature, setHealth, setRepo, state, toggleTheme,
} from "./state.js";

const ROUTES = [
  { id: "dashboard", label: "Dashboard", icon: "dashboard" },
  { id: "repos", label: "My Repositories", icon: "repos" },
  { id: "explorer", label: "Repository Explorer", icon: "explorer" },
  { id: "architecture", label: "Architecture Overview", icon: "architecture" },
  { id: "features", label: "Feature Explorer", icon: "features", feature: true, depth: true },
  { sep: true },
  { id: "explain", label: "Explain Code", icon: "explain", feature: true, depth: true },
  { id: "flow", label: "Request Flow", icon: "flow", feature: true, depth: true },
  { id: "compare", label: "Compare & Connect", icon: "compare", depth: true },
  { id: "prep", label: "Session Preparation", icon: "prep", feature: true },
  { id: "quiz", label: "Quiz & Interview", icon: "quiz", feature: true },
  { id: "troubleshoot", label: "Troubleshooting", icon: "troubleshoot", feature: true },
  { sep: true },
  { id: "history", label: "Learning History", icon: "history" },
  { id: "knowledge", label: "Knowledge Base", icon: "knowledge" },
  { id: "settings", label: "Settings", icon: "settings" },
];
const PAGES = ROUTES.filter((r) => r.id);

let cleanup = null;
let renderTicket = 0;

function buildNav() {
  const nav = $("#app-nav");
  nav.replaceChildren(...ROUTES.map((route) => (route.sep
    ? el("div", { class: "nav-sep", role: "separator" })
    : el("a", { class: "nav-link", href: `#/${route.id}`, dataset: { route: route.id } }, icon(route.icon), el("span", { text: route.label })))));
  const toggle = $("#nav-toggle");
  toggle.replaceChildren(icon("menu"));
  const setOpen = (open) => {
    nav.classList.toggle("open", open);
    $("#nav-scrim").hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
  };
  toggle.addEventListener("click", () => setOpen(!nav.classList.contains("open")));
  $("#nav-scrim").addEventListener("click", () => setOpen(false));
  nav.addEventListener("click", (event) => { if (event.target.closest("a")) setOpen(false); });
}

function buildTheme() {
  const button = $("#theme-toggle");
  const paint = () => {
    const dark = effectiveTheme() === "dark";
    button.replaceChildren(icon(dark ? "sun" : "moon"));
    button.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  };
  button.addEventListener("click", toggleTheme);
  on("theme", paint);
  paint();
}

function fillSelect(select, options, value) {
  select.replaceChildren(...options.map(([val, text]) => el("option", { value: String(val), text })));
  select.value = String(value ?? "");
}

function buildContext() {
  const repoSelect = $("#repo-select");
  const featureSelect = $("#feature-select");
  const depthSelect = $("#depth-select");

  const paintRepos = () => {
    fillSelect(repoSelect, state.repos.length
      ? state.repos.map((r) => [r.id, isIndexed(r) ? r.name : `${r.name} (not indexed)`])
      : [["", "No repositories"]], state.repoId);
    repoSelect.disabled = !state.repos.length;
  };
  const paintFeatures = () => {
    fillSelect(featureSelect, [["", "All features"], ...state.features.map((f) => [f.id, `${f.name} (${f.kind})`])], state.featureId);
  };
  fillSelect(depthSelect, DEPTHS.map((d) => [d, d[0].toUpperCase() + d.slice(1)]), state.depth);

  repoSelect.addEventListener("change", () => setRepo(parseInt(repoSelect.value, 10) || null));
  featureSelect.addEventListener("change", () => setFeature(parseInt(featureSelect.value, 10) || null));
  depthSelect.addEventListener("change", () => setDepth(depthSelect.value));

  on("repos", paintRepos);
  on("repo", () => { paintRepos(); paintFeatures(); refreshFeatures().then(render); });
  on("features", paintFeatures);
  on("feature", paintFeatures);
  on("depth", () => { depthSelect.value = state.depth; });
  paintRepos();
  paintFeatures();
}

function paintProvider() {
  const link = $("#provider-badge");
  const llm = state.health && state.health.llm;
  if (!llm) {
    link.textContent = "Server unreachable";
    link.className = "badge badge-err";
    return;
  }
  link.textContent = llm.mock ? "Evidence-only mode (no LLM)" : `${llm.provider} \u00b7 ${llm.model}`;
  link.className = `badge ${llm.mock ? "badge-warn" : "badge-ok"}`;
}

function paintIndexPill() {
  const pill = $("#index-pill");
  const repo = currentRepo();
  const status = repo && state.indexStatus[repo.id];
  if (status && status.state === "running") {
    pill.hidden = false;
    pill.textContent = status.files_total ? `Indexing ${status.files_done}/${status.files_total}` : "Indexing\u2026";
  } else {
    pill.hidden = true;
  }
}

function currentRoute() {
  const id = location.hash.replace(/^#\/?/, "").split(/[?/]/)[0] || "dashboard";
  return PAGES.find((r) => r.id === id) || PAGES[0];
}

async function render() {
  const route = currentRoute();
  const mine = ++renderTicket;
  if (cleanup) {
    try { cleanup(); } catch (err) { console.error("view cleanup failed", err); }
    cleanup = null;
  }
  for (const link of document.querySelectorAll(".nav-link")) {
    if (link.dataset.route === route.id) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  $("#feature-field").hidden = !route.feature;
  $("#depth-field").hidden = !route.depth;
  document.title = `${route.label} \u00b7 Senior Tutor`;

  const main = $("#main");
  main.replaceChildren(spinner());
  try {
    const mod = await import(`./views/${route.id}.js`);
    if (mine !== renderTicket) return;
    const root = el("div", { class: "view" });
    main.replaceChildren(root);
    const result = await mod.mount(root);
    if (mine !== renderTicket) {
      if (typeof result === "function") result();
      return;
    }
    cleanup = typeof result === "function" ? result : null;
  } catch (err) {
    if (mine !== renderTicket) return;
    console.error(err);
    main.replaceChildren(el("div", { class: "view" }, errorBox(`This page failed to load: ${err.friendly || err.message}`)));
  }
  main.scrollTop = 0;
}

async function boot() {
  initDrawer();
  buildNav();
  buildTheme();
  buildContext();
  on("health", paintProvider);
  on("index", paintIndexPill);
  on("repo", paintIndexPill);
  on("repos", paintIndexPill);

  try {
    setHealth(await api.health());
  } catch (err) {
    paintProvider();
    toast(err.friendly || "Cannot reach the tutor server.", "error");
  }
  try {
    await refreshRepos();
    await refreshFeatures();
    const repo = currentRepo();
    if (repo && state.indexStatus[repo.id] && state.indexStatus[repo.id].state === "running") trackIndex(repo.id);
  } catch (err) {
    toast(err.friendly || "Could not load repositories.", "error");
  }
  window.addEventListener("hashchange", render);
  await render();
}

boot();
