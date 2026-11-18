import { api } from "../api.js";
import { button, el, errorBox, labelled, spinner } from "../dom.js";
import { repoGate, viewHead } from "../components/layout.js";
import { assistantMessage } from "../components/message.js";
import { currentFeature, on, state } from "../state.js";

export function mount(root) {
  root.append(viewHead("Session Preparation", "Prepare to teach or present a topic: a study plan, talking points, a demo script and likely questions, all grounded in the code."));
  const repo = repoGate(root);
  if (!repo) return undefined;

  const topic = el("input", { type: "text", maxlength: 2000, placeholder: "For example: How the admission webhook validates deployments", autocomplete: "off" });
  const featureNote = el("p", { class: "hint" });
  const out = el("div", { class: "stack" });
  let controller = 0;

  const paintFeature = () => {
    const feature = currentFeature();
    featureNote.textContent = feature ? `The plan will focus on feature "${feature.name}". Change it in the header.` : "Optional: pick a feature in the header to focus the plan.";
  };
  paintFeature();

  async function generate(force) {
    const mine = ++controller;
    if (!topic.value.trim() && !state.featureId) { topic.focus(); out.replaceChildren(errorBox("Enter a topic, or choose a feature in the header.")); return; }
    out.replaceChildren(spinner("Building your session plan\u2026 this can take a minute with a local model"));
    try {
      const r = await api.sessionPrep({ repo_id: repo.id, topic: topic.value.trim(), feature_id: state.featureId, force: !!force });
      if (mine !== controller) return;
      out.replaceChildren(assistantMessage({
        role: "Session plan", content: r.content, sources: r.sources, provider: r.provider, cached: r.cached, warnings: r.warnings,
        title: `Session prep: ${topic.value.trim() || (currentFeature() && currentFeature().name) || repo.name}`, repoId: repo.id,
        onRegenerate: () => generate(true),
      }));
    } catch (err) {
      if (mine === controller) out.replaceChildren(errorBox(err.friendly || err.message, button("Retry", { small: true, onclick: () => generate(force) })));
    }
  }

  root.append(el("form", { class: "card", onsubmit: (e) => { e.preventDefault(); generate(false); } },
    labelled("Topic", topic, "What are you preparing to explain?"), featureNote,
    button("Prepare session", { variant: "primary", type: "submit", iconName: "prep" })), out);
  const off = on("feature", paintFeature);
  return () => { controller++; off(); };
}
