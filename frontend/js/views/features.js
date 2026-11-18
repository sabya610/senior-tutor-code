import { badge, button, el, emptyState } from "../dom.js";
import { chatPanel } from "../components/chatpanel.js";
import { openSource } from "../components/drawer.js";
import { navigate, repoGate, viewHead } from "../components/layout.js";
import { currentFeature, on, setFeature, setHandoff, state, takeHandoff } from "../state.js";

const KIND_LABEL = {
  api: "API", controller: "Controller", webhook: "Webhook", crd: "CRD", cli: "CLI", component: "Component", chart: "Helm chart", service: "Service",
};

export function mount(root) {
  root.append(viewHead("Feature Explorer", "Features, components and entry points detected in the code. Pick one for a guided deep dive."));
  const repo = repoGate(root);
  if (!repo) return undefined;

  const listBox = el("div", { class: "stack" });
  const detailBox = el("div", { class: "stack" });
  const filter = el("input", { type: "search", placeholder: "Filter features", "aria-label": "Filter features", autocomplete: "off" });
  let panel = null;
  let shownFeature = null;

  function paintList() {
    const q = filter.value.trim().toLowerCase();
    const features = state.features.filter((f) => !q || f.name.toLowerCase().includes(q) || f.kind.toLowerCase().includes(q));
    if (!features.length) {
      listBox.replaceChildren(emptyState(state.features.length ? "No feature matches that filter" : "No features detected",
        state.features.length ? "" : "The indexer found no recognisable entry points or components. Use Explain Code to explore directly."));
      return;
    }
    const byKind = new Map();
    for (const f of features) byKind.set(f.kind, [...(byKind.get(f.kind) || []), f]);
    listBox.replaceChildren(...[...byKind.entries()].map(([kind, items]) => el("section", { class: "stack" },
      el("h3", { text: `${KIND_LABEL[kind] || kind} (${items.length})` }),
      el("ul", { class: "list" }, items.map((f) => el("li", {}, el("button", {
        type: "button", class: "list-item", style: "width:100%;text-align:left;cursor:pointer;background:var(--panel)",
        "aria-pressed": String(f.id === state.featureId),
        onclick: () => setFeature(f.id),
      }, el("div", { class: "grow" }, el("div", { class: "title", text: f.name }), el("div", { class: "small muted", text: f.root_path || "repository root" })),
        f.id === state.featureId ? badge("Selected", "info") : null)))))));
  }

  function paintDetail() {
    const feature = currentFeature();
    if (!feature) {
      shownFeature = null;
      panel = null;
      detailBox.replaceChildren(emptyState("Choose a feature", "Select one on the left, or from the Feature selector in the header, to see its files and start a deep dive."));
      return;
    }
    if (shownFeature === feature.id && panel) return;
    shownFeature = feature.id;
    if (panel) panel.abort();
    panel = chatPanel({
      mode: "feature", submitLabel: "Ask", rows: 2, minChars: 3,
      placeholder: "Optional: ask something specific about this feature, or press Deep dive for the full guided tour.",
      examples: ["How is it tested?", "What does it depend on?", "What could break if I change it?"],
    });
    const files = feature.files || [];
    detailBox.replaceChildren(
      el("section", { class: "card stack" },
        el("div", { class: "row between" }, el("h2", { text: feature.name }), badge(KIND_LABEL[feature.kind] || feature.kind, "info")),
        el("p", { class: "muted", text: feature.description }),
        el("div", { class: "row" },
          button("Deep dive", { variant: "primary", iconName: "send", onclick: () => panel.ask("") }),
          button("Trace a flow", { onclick: () => { setHandoff({ kind: "flow", question: `Trace the main request flow through ${feature.name}`, autoSend: false }); navigate("flow"); } }),
          button("Quiz me", { onclick: () => navigate("quiz") })),
        el("details", {}, el("summary", { text: `Files (${files.length})` }),
          el("div", { class: "chips", style: "margin-top:8px" }, files.slice(0, 80).map((p) => el("button", { type: "button", class: "chip mono", onclick: () => openSource(p, 1, 1, repo.id) }, p))),
          files.length > 80 ? el("p", { class: "hint", text: `${files.length - 80} more files not shown.` }) : null)),
      panel.root);
  }

  filter.addEventListener("input", paintList);
  root.append(el("div", { class: "split" }, el("div", { class: "stack" }, filter, listBox), detailBox));
  paintList();
  paintDetail();

  const handoff = takeHandoff("features");
  if (handoff && handoff.sessionId && panel) panel.loadSession(handoff.sessionId).catch(() => {});

  const offs = [on("features", () => { paintList(); paintDetail(); }), on("feature", () => { paintList(); paintDetail(); })];
  return () => { offs.forEach((off) => off()); if (panel) panel.abort(); };
}
