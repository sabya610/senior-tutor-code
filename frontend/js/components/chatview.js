// Shared scaffolding for views that are a single conversation with the tutor.
import { el } from "../dom.js";
import { takeHandoff, on, currentFeature } from "../state.js";
import { chatPanel } from "./chatpanel.js";
import { repoGate, viewHead } from "./layout.js";

export function mountChat(root, { route, title, blurb, panel: panelOptions, showFeature = false }) {
  root.append(viewHead(title, blurb));
  const repo = repoGate(root);
  if (!repo) return undefined;

  const panel = chatPanel(panelOptions);
  const featureNote = showFeature ? el("div", { class: "alert", role: "note" }) : null;
  const paintFeature = () => {
    if (!featureNote) return;
    const feature = currentFeature();
    featureNote.hidden = !feature;
    featureNote.textContent = feature ? `Focused on feature: ${feature.name} (${feature.kind}). Change it in the header, or choose "All features" to widen the search.` : "";
  };
  paintFeature();
  root.append(featureNote, panel.root);

  const handoff = takeHandoff(route);
  if (handoff) {
    if (handoff.sessionId) panel.loadSession(handoff.sessionId).catch((err) => panel.root.prepend(el("div", { class: "alert alert-error", role: "alert", text: err.friendly || err.message })));
    else if (handoff.question) {
      panel.setQuestion(handoff.question);
      if (handoff.autoSend) panel.ask(handoff.question);
    }
  }
  const off = on("feature", paintFeature);
  return () => { off(); panel.abort(); };
}
